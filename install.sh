#!/usr/bin/env bash
# Install krunner-abbr as a D-Bus-activated KRunner plugin for the current user.
set -euo pipefail

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
X-KDE-PluginInfo-Version=0.1
X-KDE-PluginInfo-License=GPL-3.0-or-later
X-KDE-PluginInfo-EnabledByDefault=true
X-Plasma-API=DBus
X-Plasma-API-Minimum-Version=2.0
X-Plasma-DBusRunner-Service=$SERVICE
X-Plasma-DBusRunner-Path=/runner
X-Plasma-Runner-Min-Letter-Count=2
EOF

mkdir -p "$DATA/applications"
cat > "$DATA/applications/krunner-abbr-settings.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Abbreviations Settings
Comment=Configure KRunner abbreviation expansion
Icon=edit-paste
Exec=/usr/bin/python3 $HERE/krunner_abbr_settings.py
Categories=Settings;Utility;Qt;KDE;
Keywords=krunner;abbreviation;expand;snippet;
EOF

cat > "$DATA/dbus-1/services/$SERVICE.service" <<EOF
[D-BUS Service]
Name=$SERVICE
Exec=/usr/bin/python3 $HERE/krunner_abbr.py
EOF

if [[ ! -e "$CONF/abbreviations.toml" ]]; then
    cp "$HERE/abbreviations.toml" "$CONF/abbreviations.toml"
    echo "Created $CONF/abbreviations.toml"
fi

# restart a running instance so code changes take effect; D-Bus re-activates it on demand
pkill -f "$HERE/krunner_abbr.py" || true
kquitapp6 krunner 2>/dev/null || true
echo "Installed. Open KRunner and type e.g. 'expan isod', or launch 'Abbreviations Settings'."
