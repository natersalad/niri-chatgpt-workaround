#!/usr/bin/env python3
"""Park oversized ChatGPT voice helpers without closing their voice session."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import select
import subprocess
import sys
import time

PARKING_WORKSPACE = "chatgpt-voice-hidden"


def niri(*args, read=False):
    result = subprocess.run(["niri", "msg", *( ["--json"] if read else []), *args],
                            check=True, text=True, capture_output=True, timeout=5)
    return json.loads(result.stdout) if read else None


def snapshot():
    return {key: niri(key, read=True) for key in ("windows", "workspaces", "outputs")}


def oversized_helpers(state, ratio=1.5):
    """Require app identity, floating layout, a tall surface, and a separate main window."""
    outputs = state["outputs"]
    heights = [o.get("logical", {}).get("height", 0) for o in outputs.values()
               if isinstance(o.get("logical"), dict)]
    heights = [h for h in heights if isinstance(h, (int, float))
               and not isinstance(h, bool) and math.isfinite(h) and h > 0]
    tallest = max(heights, default=0)
    if tallest <= 0:
        return []
    windows = state["windows"]
    candidates = []
    for window in windows:
        if str(window.get("app_id", "")).casefold() != "chatgpt":
            continue
        if window.get("title") != "ChatGPT" or window.get("is_floating") is not True:
            continue
        size = window.get("layout", {}).get("window_size")
        if not isinstance(size, list) or len(size) != 2:
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v) for v in size):
            continue
        width, height = size
        if not (400 <= width <= 1600 and height > max(1000, tallest * ratio)):
            continue
        if not isinstance(window.get("pid"), int) or window["pid"] <= 0:
            continue
        mains = [w for w in windows if str(w.get("app_id", "")).casefold() == "chatgpt"
                 and w.get("pid") == window["pid"] and w.get("is_floating") is False]
        if len(mains) == 1:
            candidates.append(window)
    return candidates


def planned_moves(state, workspace=PARKING_WORKSPACE, ratio=1.5):
    destinations = [w for w in state["workspaces"] if w.get("name") == workspace]
    if len(destinations) != 1:
        raise RuntimeError(f"Define one named Niri workspace: {workspace}")
    target = destinations[0]
    # Never put a blocking overlay on a workspace the user is currently viewing.
    if target.get("is_active"):
        return []
    return [w for w in oversized_helpers(state, ratio)
            if w.get("workspace_id") != target["id"]]


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base:
        raise RuntimeError("XDG_RUNTIME_DIR is required; run inside your Niri session")
    directory = Path(base) / "niri-chatgpt-workaround"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


class Parker:
    def __init__(self, workspace, ratio, dry_run=False, position=None):
        self.workspace, self.ratio, self.dry_run = workspace, ratio, dry_run
        self.position = position
        self.attempted = set()
        self.state_path = runtime_dir() / "parked.json"
        self.socket = os.environ.get("NIRI_SOCKET", "")
        if not self.socket:
            raise RuntimeError("NIRI_SOCKET is required; run inside your Niri session")
        self.saved = {}
        if self.state_path.exists():
            stored = json.loads(self.state_path.read_text())
            if stored.get("socket") == self.socket:
                self.saved = stored.get("windows", {})

    def save(self):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"socket": self.socket, "windows": self.saved}))
        temporary.chmod(0o600)
        temporary.replace(self.state_path)

    def reconcile(self):
        state = snapshot()
        live = {str(w["id"]) for w in state["windows"]}
        self.saved = {key: value for key, value in self.saved.items() if key in live}
        self.attempted.intersection_update(int(key) for key in live)
        for window in planned_moves(state, self.workspace, self.ratio):
            identity = window["id"]
            if identity in self.attempted:
                continue
            if self.dry_run:
                print(f"Would park oversized ChatGPT helper {identity}", flush=True)
                continue
            # Re-read all state immediately before each targeted action.
            fresh = snapshot()
            current = next((w for w in planned_moves(fresh, self.workspace, self.ratio)
                            if w["id"] == identity and w["pid"] == window["pid"]), None)
            if current is None:
                continue
            origin = next(w for w in fresh["workspaces"] if w["id"] == current["workspace_id"])
            self.saved[str(identity)] = {"pid": current["pid"], "workspace_id": origin["id"],
                                         "output": origin.get("output")}
            self.save()
            # A failure or ambiguous result gets no automatic retry for this window ID.
            self.attempted.add(identity)
            niri("action", "move-window-to-workspace", self.workspace,
                 "--window-id", str(identity), "--focus", "false")
            check = snapshot()
            moved = next((w for w in check["windows"] if w["id"] == identity), None)
            target = next(w for w in check["workspaces"] if w.get("name") == self.workspace)
            if moved is not None and moved["workspace_id"] != target["id"]:
                raise RuntimeError("Move did not verify; not retrying this window")
            if moved is not None and self.position is not None:
                self.position_helper(identity)
            self.attempted.discard(identity)
            print(f"Parked oversized ChatGPT helper {identity}", flush=True)
        if not self.dry_run:
            self.save()

    def position_helper(self, identity):
        state = snapshot()
        target = next(w for w in state["workspaces"] if w.get("name") == self.workspace)
        current = next((w for w in oversized_helpers(state, self.ratio)
                        if w["id"] == identity and w["workspace_id"] == target["id"]), None)
        if current is None:
            return
        before = current["layout"].get("tile_pos_in_workspace_view")
        if not isinstance(before, list) or len(before) != 2:
            raise RuntimeError("Cannot read helper position")
        # Signed CLI values mean relative motion. Using deltas also avoids the
        # working-area offset applied to absolute CLI positions.
        delta = [wanted - actual for wanted, actual in zip(self.position, before)]
        niri("action", "move-floating-window", "--id", str(identity),
             "--x", f"{delta[0]:+.6f}", "--y", f"{delta[1]:+.6f}")
        after = next((w for w in snapshot()["windows"] if w["id"] == identity), None)
        if after is not None:
            actual = after["layout"].get("tile_pos_in_workspace_view", [])
            if len(actual) != 2 or any(abs(a - b) > 1 for a, b in zip(actual, self.position)):
                raise RuntimeError("Niri clamped the requested position; not retrying")

    def restore(self):
        # Stop the service first, otherwise it will park the helper again.
        for identity, saved in list(self.saved.items()):
            state = snapshot()
            helper = next((w for w in oversized_helpers(state, self.ratio)
                           if str(w["id"]) == identity and w["pid"] == saved["pid"]), None)
            origin = next((w for w in state["workspaces"] if w["id"] == saved["workspace_id"]), None)
            if helper is None or origin is None:
                continue
            parking = next(w for w in state["workspaces"] if w["id"] == helper["workspace_id"])
            if parking.get("output") != origin.get("output"):
                raise RuntimeError("Original workspace is on another output; restore manually")
            focused = next((w for w in state["workspaces"] if w.get("is_focused")), None)
            if not origin.get("name") and (focused is None or focused.get("output") != origin.get("output")):
                raise RuntimeError("Focus the original output before restoring an unnamed workspace")
            reference = origin.get("name") or str(origin["idx"])
            niri("action", "move-window-to-workspace", reference,
                 "--window-id", identity, "--focus", "false")
            check = snapshot()
            moved = next((w for w in check["windows"] if str(w["id"]) == identity), None)
            if moved is not None and moved["workspace_id"] != origin["id"]:
                raise RuntimeError("Restore did not verify; restore manually")
            print(f"Restored helper {identity}", flush=True)
            del self.saved[identity]
        self.save()


def watch(parker):
    """React to Niri events; bounded polling also catches delayed geometry changes."""
    stream = subprocess.Popen(["niri", "msg", "--json", "event-stream"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        while stream.poll() is None:
            parker.reconcile()
            ready, _, _ = select.select([stream.stdout], [], [], 0.5)
            if ready and not stream.stdout.readline():
                break
            # Coalesce bursts and bound IPC traffic during window animations.
            time.sleep(0.1)
        if stream.poll() not in (None, 0):
            raise RuntimeError("Niri event stream ended")
    finally:
        stream.terminate()
        stream.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["once", "watch", "restore", "status"], nargs="?", default="once")
    parser.add_argument("--workspace", default=PARKING_WORKSPACE)
    parser.add_argument("--height-ratio", type=float, default=1.5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--position", nargs=2, type=float, metavar=("X", "Y"),
                        help="Position newly parked helpers in output-local logical pixels")
    args = parser.parse_args()
    if not math.isfinite(args.height_ratio) or args.height_ratio <= 1:
        parser.error("--height-ratio must be finite and greater than 1")
    if args.position is not None and not all(math.isfinite(v) for v in args.position):
        parser.error("--position coordinates must be finite")
    if args.mode == "status":
        state = snapshot()
        print(json.dumps({"helpers": [w["id"] for w in oversized_helpers(state, args.height_ratio)],
                          "would_park": [w["id"] for w in planned_moves(state, args.workspace, args.height_ratio)]}))
        return
    lock = (runtime_dir() / "lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("Another parker is running; stop the service before restore")
    parker = Parker(args.workspace, args.height_ratio, args.dry_run, args.position)
    if args.mode == "restore":
        if args.dry_run:
            parser.error("--dry-run is not supported with restore")
        parker.restore()
    elif args.mode == "watch":
        watch(parker)
    else:
        parker.reconcile()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        print(f"niri-chatgpt-workaround: {error}", file=sys.stderr)
        sys.exit(1)
