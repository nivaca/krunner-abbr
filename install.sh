#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
# Install krunner-abbr as a D-Bus-activated KRunner plugin for the current user.
# With --clean, replace your configuration with the example (a backup is kept).
set -euo pipefail

CLEAN=false
case "${1:-}" in
    "") ;;
    --clean) CLEAN=true ;;
    *) echo "usage: $0 [--clean]" >&2; exit 2 ;;
esac

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVICE=io.github.nivaca.AbbrKRunner
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/krunner-abbr"

mkdir -p "$DATA/krunner/dbusplugins" "$DATA/dbus-1/services" "$CONF"

cat > "$DATA/krunner/dbusplugins/krunner-abbr.desktop" <<EOF
[Desktop Entry]
Name=Abbreviations
Comment=Expand abbreviations and copy them to the clipboard
Icon=edit-paste
Type=Service
X-KDE-PluginInfo-Author=Nicolas Vaughan
X-KDE-PluginInfo-Name=krunner-abbr
X-KDE-PluginInfo-Version=1.1
X-KDE-PluginInfo-License=GPL-3.0-or-later
X-KDE-PluginInfo-EnabledByDefault=true
X-Plasma-API=DBus
X-Plasma-API-Minimum-Version=2.0
X-Plasma-DBusRunner-Service=$SERVICE
X-Plasma-DBusRunner-Path=/runner
X-Plasma-Runner-Min-Letter-Count=2
EOF

cat > "$DATA/dbus-1/services/$SERVICE.service" <<EOF
[D-BUS Service]
Name=$SERVICE
Exec=/usr/bin/python3 $HERE/krunner_abbr.py
EOF

# never touch an existing configuration: only create it when there is nothing at all
# at that path (-L also catches a dangling symlink, e.g. into a not-yet-synced folder);
# cp -n is a second guard against overwriting
CONFIG="$CONF/abbreviations.toml"
if $CLEAN && [[ -e "$CONFIG" ]]; then
    # back up first; through a symlink (e.g. into Dropbox) the file it points to is replaced
    BACKUP="$CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
    cp -L "$CONFIG" "$BACKUP"
    cat "$HERE/abbreviations.toml" > "$CONFIG"
    echo "Replaced $CONFIG with the example (your old one is in $BACKUP)"
elif $CLEAN && [[ -L "$CONFIG" ]]; then
    echo "Kept $CONFIG: it is a symlink to a file that is not there (not synced yet?)" >&2
elif [[ -e "$CONFIG" || -L "$CONFIG" ]]; then
    echo "Kept your existing $CONFIG"
else
    cp -n "$HERE/abbreviations.toml" "$CONFIG"
    echo "Created $CONFIG from the example"
fi

# restart a running instance so code changes take effect; D-Bus re-activates it on demand
pkill -f "$HERE/krunner_abbr.py" || true
kquitapp6 krunner 2>/dev/null || true
# build the usage hint from the configured keyword, using the runner's own rules
IFS=$'\t' read -r EXAMPLE SETTINGS < <(python3 - "$HERE" <<'EOF' 2>/dev/null || printf "'ex isod'\t'ex settings'\n"
import sys
sys.path.insert(0, sys.argv[1])
import krunner_abbr as k

cfg = k.Config(k.CONFIG_FILE)
cfg.reload_if_changed()

def with_trigger(trigger, word):
    return trigger + (" " if trigger[-1:].isalnum() else "") + word

key = next(iter(cfg.abbrs.values()), {"key": "isod"})["key"]
print(f"'{with_trigger(cfg.prefix, key)}'\t'{with_trigger(cfg.prefix or k.DEFAULT_PREFIX, k.SETTINGS_WORD)}'")
EOF
)
echo "Installed. Open KRunner and type e.g. $EXAMPLE, or $SETTINGS to configure."
