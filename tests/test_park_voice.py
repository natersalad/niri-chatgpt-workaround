import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("park_voice", Path(__file__).parents[1] / "park_voice.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def state():
    return {"outputs": {"DP-1": {"logical": {"height": 1152}}},
            "workspaces": [{"id": 1, "idx": 1, "name": None, "output": "DP-1", "is_active": True},
                           {"id": 2, "idx": 2, "name": module.PARKING_WORKSPACE,
                            "output": "DP-1", "is_active": False}],
            "windows": [{"id": 10, "pid": 900, "title": "ChatGPT", "app_id": "chatgpt",
                         "is_floating": False, "workspace_id": 1,
                         "layout": {"window_size": [1362, 1096]}},
                        {"id": 20, "pid": 900, "title": "ChatGPT", "app_id": "chatgpt",
                         "is_floating": True, "workspace_id": 1,
                         "layout": {"window_size": [1132, 2273]}}]}


class ClassificationTests(unittest.TestCase):
    def test_moves_only_oversized_helper(self):
        self.assertEqual([w["id"] for w in module.planned_moves(state())], [20])

    def test_normal_dialog_is_not_hidden(self):
        data = state(); data["windows"][1]["layout"]["window_size"] = [700, 700]
        self.assertEqual(module.planned_moves(data), [])

    def test_foreign_app_and_foreign_process_are_not_hidden(self):
        for field, value in [("app_id", "firefox"), ("pid", 901), ("title", "Other dialog")]:
            data = state(); data["windows"][1][field] = value
            self.assertEqual(module.planned_moves(data), [])

    def test_no_unique_main_fails_closed(self):
        data = state(); data["windows"].append(copy.deepcopy(data["windows"][0]))
        self.assertEqual(module.planned_moves(data), [])

    def test_malformed_geometry_fails_closed(self):
        for size in [None, [1], [1132, "2273"], [1132, float("nan")], [True, 2273]]:
            data = state(); data["windows"][1]["layout"]["window_size"] = size
            self.assertEqual(module.planned_moves(data), [])

    def test_inactive_or_unknown_outputs_fail_closed(self):
        data = state(); data["outputs"] = {"DP-1": {"logical": None}}
        self.assertEqual(module.planned_moves(data), [])
        for height in [None, "1152", float("nan"), True]:
            data = state(); data["outputs"]["DP-1"]["logical"]["height"] = height
            self.assertEqual(module.planned_moves(data), [])

    def test_active_parking_workspace_is_not_used(self):
        data = state(); data["workspaces"][1]["is_active"] = True
        self.assertEqual(module.planned_moves(data), [])

    def test_already_parked_helper_is_not_moved(self):
        data = state(); data["windows"][1]["workspace_id"] = 2
        self.assertEqual(module.planned_moves(data), [])

    def test_missing_destination_reports_setup_error(self):
        data = state(); data["workspaces"].pop()
        with self.assertRaises(RuntimeError): module.planned_moves(data)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.temporary.name, "NIRI_SOCKET": "test-socket"})
        self.env.start()
        self.parker = module.Parker(module.PARKING_WORKSPACE, 1.5)

    def tearDown(self):
        self.env.stop(); self.temporary.cleanup()

    def test_stale_candidate_is_not_acted_on(self):
        changed = state(); changed["windows"][1]["pid"] = 999
        with patch.object(module, "snapshot", side_effect=[state(), changed]), patch.object(module, "niri") as action:
            self.parker.reconcile(); action.assert_not_called()

    def test_verified_move_targets_id_without_following_focus(self):
        parked = state(); parked["windows"][1]["workspace_id"] = 2
        with patch.object(module, "snapshot", side_effect=[state(), state(), parked]), patch.object(module, "niri") as action:
            self.parker.reconcile()
            action.assert_called_once_with("action", "move-window-to-workspace", module.PARKING_WORKSPACE,
                                           "--window-id", "20", "--focus", "false")

    def test_dry_run_has_no_action_or_state_file(self):
        self.parker.dry_run = True
        with patch.object(module, "snapshot", return_value=state()), patch.object(module, "niri") as action:
            self.parker.reconcile(); action.assert_not_called()
            self.assertFalse(self.parker.state_path.exists())

    def test_unverified_move_is_not_retried(self):
        with patch.object(module, "snapshot", return_value=state()), patch.object(module, "niri") as action:
            with self.assertRaises(RuntimeError): self.parker.reconcile()
            self.parker.reconcile()
            self.assertEqual(action.call_count, 1)

    def test_reappearing_same_window_can_be_parked_again_after_verified_success(self):
        parked = state(); parked["windows"][1]["workspace_id"] = 2
        with patch.object(module, "snapshot", side_effect=[state(), state(), parked, state(), state(), parked]), patch.object(module, "niri") as action:
            self.parker.reconcile(); self.parker.reconcile()
            self.assertEqual(action.call_count, 2)

    def test_compositor_socket_change_discards_stale_restore_ids(self):
        self.parker.saved = {"20": {"pid": 900, "workspace_id": 1, "output": "DP-1"}}
        self.parker.save()
        with patch.dict(os.environ, {"NIRI_SOCKET": "different-session"}):
            other = module.Parker(module.PARKING_WORKSPACE, 1.5)
            self.assertEqual(other.saved, {})

    def test_restore_checks_destination_before_removing_saved_state(self):
        parked = state(); parked["windows"][1]["workspace_id"] = 2
        parked["workspaces"][0]["is_focused"] = True
        self.parker.saved = {"20": {"pid": 900, "workspace_id": 1, "output": "DP-1"}}
        with patch.object(module, "snapshot", return_value=parked), patch.object(module, "niri"):
            with self.assertRaises(RuntimeError): self.parker.restore()
            self.assertIn("20", self.parker.saved)

    def test_restore_does_not_use_index_on_a_different_focused_output(self):
        parked = state(); parked["windows"][1]["workspace_id"] = 2
        parked["workspaces"].append({"id": 3, "output": "HDMI", "is_focused": True})
        self.parker.saved = {"20": {"pid": 900, "workspace_id": 1, "output": "DP-1"}}
        with patch.object(module, "snapshot", return_value=parked), patch.object(module, "niri") as action:
            with self.assertRaises(RuntimeError): self.parker.restore()
            action.assert_not_called()

    def test_position_uses_signed_deltas_for_negative_coordinates(self):
        before = state(); before["windows"][1]["workspace_id"] = 2
        before["windows"][1]["layout"]["tile_pos_in_workspace_view"] = [800, 38.4]
        after = copy.deepcopy(before)
        after["windows"][1]["layout"]["tile_pos_in_workspace_view"] = [624.8, -537.6]
        self.parker.position = [624.8, -537.6]
        with patch.object(module, "snapshot", side_effect=[before, after]), patch.object(module, "niri") as action:
            self.parker.position_helper(20)
            action.assert_called_once_with("action", "move-floating-window", "--id", "20",
                                           "--x", "-175.200000", "--y", "-576.000000")

    def test_position_never_moves_a_helper_on_main_workspace(self):
        self.parker.position = [624.8, -537.6]
        with patch.object(module, "snapshot", return_value=state()), patch.object(module, "niri") as action:
            self.parker.position_helper(20); action.assert_not_called()

    def test_clamped_position_is_reported(self):
        parked = state(); parked["windows"][1]["workspace_id"] = 2
        parked["windows"][1]["layout"]["tile_pos_in_workspace_view"] = [800, 38.4]
        self.parker.position = [624.8, -537.6]
        with patch.object(module, "snapshot", return_value=parked), patch.object(module, "niri"):
            with self.assertRaises(RuntimeError): self.parker.position_helper(20)


if __name__ == "__main__":
    unittest.main()
