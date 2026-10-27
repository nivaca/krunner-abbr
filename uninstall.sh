#!/usr/bin/env bash
# Remove the krunner-abbr KRunner plugin. Keeps your abbreviations unless --purge is given.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVICE=io.github.nivaca.AbbrKRunner
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/krunner-abbr"

rm -fv "$DATA/krunner/dbusplugins/krunner-abbr.desktop" \
       "$DATA/dbus-1/services/$SERVICE.service"

if [[ "${1:-}" == "--purge" ]]; then
    rm -rfv "$CONF"
else
    echo "Kept $CONF (run with --purge to delete it)"
fi

pkill -f "$HERE/krunner_abbr.py" || true
kquitapp6 krunner 2>/dev/null || true
echo "Uninstalled."
