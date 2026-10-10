#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
config_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}
command -v niri >/dev/null
command -v python3 >/dev/null
mkdir -p "$HOME/.local/bin" "$config_dir/systemd/user" "$config_dir/niri"
install -m 755 "$repo_dir/park_voice.py" "$HOME/.local/bin/niri-chatgpt-park"
install -m 644 "$repo_dir/niri-chatgpt-workaround.service" "$config_dir/systemd/user/"
install -m 644 "$repo_dir/niri-workspace.kdl" "$config_dir/niri/chatgpt-voice-workaround.kdl"
systemctl --user daemon-reload
printf '%s\n' 'Installed. Add this line once to your main Niri config:'
printf '%s\n' 'include "chatgpt-voice-workaround.kdl"'
printf '%s\n' 'Then follow the validation and service-start steps in README.md.'
