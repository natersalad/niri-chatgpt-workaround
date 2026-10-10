# Niri ChatGPT voice workaround

ChatGPT's native Wayland voice helper can create an oversized transparent window that blocks clicks on other apps. Moving it away once helps, but ChatGPT can recreate it on the current workspace.

This small Python watcher parks matching helper windows on a dedicated, inactive Niri workspace. It watches for replacements and checks again when a helper moves back. It does not close the window, stop voice, modify ChatGPT, or change the app's Wayland launch settings.

**The floating voice controls disappear with the helper.** Use the main ChatGPT window to control the call. This is a desktop workaround, not a fix for the app's input region.

## Status

Developed on CachyOS with Niri 26.04 and the ChatGPT Linux preview running on native Wayland. Moving the helper away was confirmed to preserve voice and unblock clicks. During an active call, the installed watcher automatically parked several newly recreated helpers, parked an existing helper returned to the main workspace, and kept the helper away through a focus-switch test. Login startup is configured but has not been tested through a new login.

This code and its documentation were generated with OpenAI Codex and reviewed through automated tests and local runtime checks. It is an independent project, not an official OpenAI or Niri fix.

## Requirements

- Niri with JSON IPC, named workspaces, and `move-window-to-workspace`.
- Python 3.10 or newer; no third-party Python packages.
- A systemd user session for automatic startup.
- An oversized floating helper with app ID `chatgpt`, title `ChatGPT`, and a separate tiled main window from the same process.

## Install

Run these commands inside your Niri session:

```sh
git clone https://github.com/natersalad/niri-chatgpt-workaround.git
cd niri-chatgpt-workaround
sh install.sh
```

Add this line once to your main Niri configuration, normally `~/.config/niri/config.kdl`:

```kdl
include "chatgpt-voice-workaround.kdl"
```

Then validate, reload, inspect the match, and start the watcher:

```sh
niri validate
niri msg action load-config-file
~/.local/bin/niri-chatgpt-park once --dry-run
systemctl --user import-environment NIRI_SOCKET WAYLAND_DISPLAY XDG_RUNTIME_DIR
systemctl --user enable --now niri-chatgpt-workaround.service
```

The installer copies only the script, user service, and workspace snippet. It does not edit your main configuration or start the service. Leave the `chatgpt-voice-hidden` workspace inactive. Named workspaces can change numerical workspace indices, so check any bindings that depend on those indices.

The user service inherits Niri's session environment. If it fails at the next login, check that your Niri/session setup imports `NIRI_SOCKET` into the systemd user manager. Import the variables above from a terminal in that session and restart the service.

## Check it

During an active voice call, switch away from ChatGPT. Click another app where the helper used to cover the desktop. Switch back and away again to exercise helper recreation. Voice should continue and the main window should remain on its original workspace.

```sh
systemctl --user status niri-chatgpt-workaround.service
journalctl --user -u niri-chatgpt-workaround.service -n 20
~/.local/bin/niri-chatgpt-park status
```

There can be a short delay before a newly created helper is parked. The watcher reacts to compositor events and also checks about every 0.6 seconds when idle. It only reads window metadata, workspace metadata, and output geometry; it does not capture your screen, inspect conversations, or make network requests.

### Remember a position on the hidden workspace

Optional `--position X Y` puts each newly parked helper at those output-local logical coordinates. It keeps the helper on the hidden workspace. The coordinates describe the whole transparent canvas, not the small visible controls. Get the current position from `niri msg --json windows`, under `layout.tile_pos_in_workspace_view`.

Configure the service with `systemctl --user edit niri-chatgpt-workaround.service`:

```ini
[Service]
ExecStart=
ExecStart=%h/.local/bin/niri-chatgpt-park watch --position 600 -500
```

Replace the example coordinates with your own, then restart the service. Positioning happens after parking, rather than continuously overriding manual dragging. Niri can clamp out-of-bounds positions; an unverified position stops the watcher. Fixed coordinates may need adjusting after a monitor or scaling change. This does not enlarge the voice controls.

## Matching and limits

The helper does not expose a unique app ID or title, so matching also uses its geometry: width between 400 and 1600 logical pixels, and height greater than both 1000 pixels and 1.5 times the tallest active output. The same process must have exactly one tiled ChatGPT main window. Other windows are left alone.

These are deliberately narrow heuristics based on the observed bug. Different app versions, output layouts, a floating main window, or multiple main windows may prevent a match. An unusually tall ChatGPT dialog could match. Inspect the dry run before enabling this on another system. This does not make the underlying window invisible or click-through; visiting the parking workspace will show it again.

The watcher rechecks each candidate before moving it and verifies the result. It stops on an IPC error or unverified move; the service does not automatically restart after failure. Check the journal and restart manually after resolving the cause.

## Pause, restore, or remove

```sh
systemctl --user stop niri-chatgpt-workaround.service
~/.local/bin/niri-chatgpt-park restore
```

Restore uses private, temporary session state to return live helpers moved by this watcher to their original workspace. It skips helpers whose original workspace no longer exists. Focus the original monitor before restoring an unnamed workspace. Cross-output restoration requires a manual move. Stopping the watcher does not end the call.

To remove it:

```sh
systemctl --user disable --now niri-chatgpt-workaround.service
~/.local/bin/niri-chatgpt-park restore
rm ~/.local/bin/niri-chatgpt-park
rm ~/.config/systemd/user/niri-chatgpt-workaround.service
systemctl --user daemon-reload
```

Remove the include line from your Niri configuration before deleting `~/.config/niri/chatgpt-voice-workaround.kdl`, then validate and reload. If you use `XDG_CONFIG_HOME`, substitute that location for `~/.config`.

## Updates and development

ChatGPT keeps its normal update mechanism. This repository contains no patched copy of the app. To update the workaround, pull the latest repository version, rerun the installer, and restart the service.

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile park_voice.py
sh -n install.sh
```

Tests cover helper selection, ordinary dialogs, foreign processes, ambiguous main windows, active destinations, stale candidates, dry runs, repeated appearances, uncertain moves, and compositor-session changes. They do not substitute for a real voice/input check.

## Related reports

- [Linux floating voice widget blocks clicks](https://github.com/openai/codex/issues/50963)
- [Oversized Linux floating window report](https://github.com/openai/codex/issues/48018)
- [Hyprland input-region discussion](https://github.com/hyprwm/Hyprland/discussions/11834)

The Hyprland discussion concerns another compositor; its patch is not a Niri patch. This project works around the obstruction by moving the helper instead.
