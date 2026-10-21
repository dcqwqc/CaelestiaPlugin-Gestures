#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HOME/.local/bin" "$HOME/.local/libexec" "$HOME/.config/systemd/user" "$HOME/.local/share/caelestia/plugins"
"${CC:-cc}" -O2 -Wall -Wextra -std=c11 "$root/native/uinput_mouse.c" -o "$HOME/.local/libexec/caelestia-gesture-mouse"
install -m 0755 "$root/daemon/mirai_gestures.py" "$HOME/.local/bin/mirai-gestures"
install -m 0644 "$root/systemd/mirai-gestures.service" "$HOME/.config/systemd/user/mirai-gestures.service"
ln -sfn "$root" "$HOME/.local/share/caelestia/plugins/gestures"
python3 "$root/scripts/integrate-touchscreen-osk.py"
systemctl --user disable --now kagami-touchpad-remote-gesture.service 2>/dev/null || true
systemctl --user daemon-reload
# Caelestia QuickShell owns this daemon when its plugin is installed. Starting
# the legacy systemd unit as well duplicates raw input readers/actions.
if systemctl --user is-active --quiet caelestia-shell.service; then
  systemctl --user disable --now mirai-gestures.service 2>/dev/null || true
  echo "Caelestia owns the gesture daemon; standalone systemd unit disabled"
else
  systemctl --user enable --now mirai-gestures.service
fi
