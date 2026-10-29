# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""The configuration file, and usage counts for ranking."""

import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path

from krunner_abbr_template import ABBR_TEXTS, CMD_TIMEOUT


HERE = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "krunner-abbr"
CONFIG_FILE = CONFIG_DIR / "abbreviations.toml"
EXAMPLE_CONFIG = HERE / "abbreviations.toml"
DEFAULT_PREFIX = "ex"
# applications (KWin resource class or desktop file name, lowercase) that paste with other keys;
# [paste_keys] in the config adds to and overrides these
TERMINAL_PASTE_KEYS = {app: "ctrl+shift+v" for app in (
    "konsole", "org.kde.konsole", "yakuake", "org.kde.yakuake", "kitty", "alacritty",
    "foot", "footclient", "wezterm", "org.wezfurlong.wezterm", "ghostty", "com.mitchellh.ghostty",
    "gnome-terminal-server", "org.gnome.terminal", "org.gnome.console", "kgx", "ptyxis",
    "org.gnome.ptyxis", "tilix", "com.gexperts.tilix", "terminator", "xterm", "hyper",
    "cool-retro-term", "contour", "rio",
)}


# ---------------------------------------------------------------- config


def as_list(value) -> list[str]:
    """A TOML list, or a string of words separated by spaces or commas."""
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [w for w in re.split(r"[\s,]+", str(value or "")) if w]


STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "krunner-abbr"
USAGE_FILE = STATE_DIR / "usage.json"
USAGE_HALF_LIFE = 30  # days for a use to count half as much
RESULT_MAX = 300  # characters of the last result kept for lazy abbreviations


class Usage:
    """How often and how recently each abbreviation was used, for ranking."""

    def __init__(self, path: Path = USAGE_FILE):
        self.path, self.mtime, self.data = path, None, {}

    def load(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
            if mtime != self.mtime:
                self.data, self.mtime = json.loads(self.path.read_text()), mtime
        except (OSError, ValueError):
            pass
        return self.data

    def record(self, key: str, result: str | None = None) -> None:
        """Count a use of key; result is kept to show next time (for lazy abbreviations)."""
        entry = self.load().setdefault(key.lower(), {"count": 0, "last": 0})
        entry["count"] += 1
        entry["last"] = time.time()
        if result is not None:
            entry["result"] = result[:RESULT_MAX]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))
            self.mtime = self.path.stat().st_mtime
        except OSError as e:
            print(f"cannot save usage: {e}", file=sys.stderr)

    def last_result(self, key: str) -> str:
        return self.load().get(key.lower(), {}).get("result", "")

    def score(self, key: str) -> float:
        entry = self.load().get(key.lower())
        if not entry:
            return 0.0
        age = max(0.0, time.time() - entry["last"]) / 86400
        return entry["count"] * 0.5 ** (age / USAGE_HALF_LIFE)


BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def toml_str(s: str) -> str:
    if "\n" in s and "'''" not in s and "\r" not in s:
        return "'''\n" + s + "'''"  # literal multi-line: no escaping needed
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def toml_key(key: str) -> str:
    return key if BARE_KEY_RE.match(key) else json.dumps(key, ensure_ascii=False)


class Config:
    def __init__(self, path: Path):
        self.path = path
        self.mtime = None
        self.prefix = DEFAULT_PREFIX
        self.min_prefix_chars = 2
        self.cmd_timeout = CMD_TIMEOUT
        self.notify = True
        self.enter_pastes = False
        self.paste_keys: dict[str, str] = dict(TERMINAL_PASTE_KEYS)
        self.rank_by_use = True
        self.disabled_groups: list[str] = []
        self.abbrs: dict[str, dict] = {}  # by lowercase key; without disabled groups
        self.aliases: dict[str, str] = {}  # lowercase alias -> lowercase key
        self.disabled: dict[str, dict] = {}  # abbreviations in disabled groups
        self.error = ""

    def reload_if_changed(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self.abbrs, self.error = {}, f"{self.path} not found"
            return
        if mtime == self.mtime:
            return
        self.mtime = mtime
        try:
            data = tomllib.loads(self.path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as e:
            self.error = f"config error: {e}"
            print(self.error, file=sys.stderr)
            return  # keep the last good config
        settings = data.get("settings", {})
        self.prefix = settings.get("prefix", DEFAULT_PREFIX)
        self.min_prefix_chars = int(settings.get("min_prefix_chars", 2))
        self.cmd_timeout = float(settings.get("cmd_timeout", CMD_TIMEOUT))
        self.notify = bool(settings.get("notify", True))
        self.enter_pastes = bool(settings.get("enter_pastes", False))
        self.paste_keys = dict(TERMINAL_PASTE_KEYS)
        self.paste_keys.update({app.lower(): str(keys).lower()
                                for app, keys in data.get("paste_keys", {}).items()})
        self.rank_by_use = bool(settings.get("rank_by_use", True))
        self.disabled_groups = [str(g) for g in settings.get("disabled_groups", [])]
        off = {g.lower() for g in self.disabled_groups}
        abbrs, disabled = {}, {}
        for key, val in data.get("abbr", {}).items():
            if isinstance(val, str):
                val = {"text": val}
            if isinstance(val, dict) and isinstance(val.get("text"), str):
                target = disabled if str(val.get("group", "")).lower() in off else abbrs
                target[key.lower()] = {"key": key, **val}
        aliases = {}
        for key, abbr in abbrs.items():
            for alias in as_list(abbr.get("aliases")):
                if alias.lower() not in abbrs:  # a real name wins over an alias
                    aliases.setdefault(alias.lower(), key)
        self.abbrs, self.aliases, self.disabled, self.error = abbrs, aliases, disabled, ""
        ABBR_TEXTS.clear()  # disabled groups can still be included with {abbr:key}
        ABBR_TEXTS.update({key: abbr["text"] for key, abbr in (disabled | abbrs).items()})

def ensure_config() -> None:
    """Create the configuration from the example on the first run, as install.sh does
    (an RPM can't write into home folders). Never touches an existing file or symlink."""
    if CONFIG_FILE.exists() or CONFIG_FILE.is_symlink():
        return
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with CONFIG_FILE.open("x") as f:
            f.write(EXAMPLE_CONFIG.read_text())
    except OSError as e:
        print(f"cannot create {CONFIG_FILE}: {e}", file=sys.stderr)
