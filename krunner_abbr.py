#!/usr/bin/env python3
"""KRunner D-Bus plugin that expands abbreviations and copies them to the clipboard.

Abbreviations live in ~/.config/krunner-abbr/abbreviations.toml and may contain
dynamic placeholders such as {date}, {now+1d:%A}, {uuid}, {clipboard},
{cmd:...} or {arg}. See abbreviations.toml for the full syntax.

Run with --expand KEY [ARG...] to test an abbreviation from the shell.
"""

import argparse
import calendar
import locale
import os
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
from datetime import datetime, timedelta
from pathlib import Path

SERVICE = "io.github.nivaca.AbbrKRunner"
OBJPATH = "/runner"
IFACE = "org.kde.krunner1"

HERE = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "krunner-abbr"
CONFIG_FILE = CONFIG_DIR / "abbreviations.toml"
EXAMPLE_CONFIG = HERE / "abbreviations.toml"
SETTINGS_APP = HERE / "krunner_abbr_settings.py"

DEFAULT_PREFIX = "abbr"
SETTINGS_WORD = "settings"  # "<keyword> settings" opens the settings dialog
SETTINGS_ID = "\x1esettings"

# KRunner match categories (Plasma 6 QueryMatch::CategoryRelevance)
HIGHEST = 100
LOW = 30

CMD_TIMEOUT = 2.0  # default seconds for {cmd:...} placeholders
SUBTEXT_MAX = 80

# {name}, {name+1d-2h}, {name:spec}, {name+1w:spec}
PLACEHOLDER_RE = re.compile(
    r"\{(?P<name>[a-z_]+)(?P<offsets>(?:[+-]\d+(?:mo|[smhdwMy]))*)(?::(?P<spec>[^{}]*))?\}"
)
OFFSET_RE = re.compile(r"([+-])(\d+)(mo|[smhdwMy])")


# ---------------------------------------------------------------- clipboard


def _dbus_klipper():
    import dbus

    return dbus.Interface(
        dbus.SessionBus().get_object("org.kde.klipper", "/klipper"),
        "org.kde.klipper.klipper",
    )


def get_clipboard() -> str:
    try:
        return str(_dbus_klipper().getClipboardContents())
    except Exception:
        pass
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout
            except Exception:
                pass
    return ""


def set_clipboard(text: str) -> None:
    try:
        _dbus_klipper().setClipboardContents(text)
        return
    except Exception:
        pass
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True, start_new_session=True).communicate(text)
            return
    raise RuntimeError("no clipboard backend available (Klipper, wl-copy, xclip)")


# ---------------------------------------------------------------- templates


def add_months(dt: datetime, months: int) -> datetime:
    month0 = dt.month - 1 + months
    year, month = dt.year + month0 // 12, month0 % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def apply_offsets(dt: datetime, offsets: str) -> datetime:
    for sign, amount, unit in OFFSET_RE.findall(offsets):
        n = int(amount) * (1 if sign == "+" else -1)
        if unit in ("mo", "M"):
            dt = add_months(dt, n)
        elif unit == "y":
            dt = add_months(dt, 12 * n)
        else:
            key = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[unit]
            dt += timedelta(**{key: n})
    return dt


DATE_DEFAULTS = {
    "date": "%Y-%m-%d",
    "time": "%H:%M",
    "now": "%Y-%m-%dT%H:%M:%S",
}


def expand(template: str, arg: str = "", timeout: float = CMD_TIMEOUT) -> str:
    """Expand all placeholders in template. Raises ValueError on bad input."""
    now = datetime.now().astimezone()

    def repl(m: re.Match) -> str:
        name, offsets, spec = m["name"], m["offsets"], m["spec"]
        if name in DATE_DEFAULTS:
            return apply_offsets(now, offsets).strftime(spec or DATE_DEFAULTS[name])
        if offsets:
            raise ValueError(f"offsets only apply to date/time/now: {m[0]}")
        if name == "arg":
            return arg or (spec or "")
        if name == "uuid":
            return str(uuid.uuid4())
        if name == "env":
            return os.environ.get(spec or "", "")
        if name == "clipboard":
            return get_clipboard()
        if name == "cmd":
            if not spec:
                raise ValueError("{cmd:...} needs a command")
            env = dict(os.environ, ABBR_ARG=arg)
            res = subprocess.run(
                spec, shell=True, capture_output=True, text=True, timeout=timeout, env=env
            )
            return res.stdout.rstrip("\n")
        raise ValueError(f"unknown placeholder {m[0]}")

    # {{ and }} are literal braces
    parts = template.replace("{{", "\0L").replace("}}", "\0R").split("\0")
    out = []
    for i, part in enumerate(parts):
        if i > 0:
            out.append("{" if part[0] == "L" else "}")
            part = part[1:]
        out.append(PLACEHOLDER_RE.sub(repl, part))
    return "".join(out)


def uses_arg(template: str) -> bool:
    return any(m["name"] in ("arg", "cmd") for m in PLACEHOLDER_RE.finditer(template))


# ---------------------------------------------------------------- config


class Config:
    def __init__(self, path: Path):
        self.path = path
        self.mtime = None
        self.prefix = DEFAULT_PREFIX
        self.min_prefix_chars = 2
        self.cmd_timeout = CMD_TIMEOUT
        self.abbrs: dict[str, dict] = {}
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
        abbrs = {}
        for key, val in data.get("abbr", {}).items():
            if isinstance(val, str):
                val = {"text": val}
            if isinstance(val, dict) and isinstance(val.get("text"), str):
                abbrs[key.lower()] = {"key": key, **val}
        self.abbrs, self.error = abbrs, ""


# ---------------------------------------------------------------- matching


def strip_trigger(query: str, trigger: str) -> str | None:
    """Return query without the trigger keyword, or None if it doesn't start with it."""
    head, rest = query[:len(trigger)], query[len(trigger):]
    if head.lower() != trigger.lower():
        return None
    # a word keyword ("abbr") needs a space after it; a symbol (";") does not
    if trigger[-1].isalnum() and rest and not rest[0].isspace():
        return None
    return rest.lstrip()


def settings_match(cfg: Config, query: str) -> int | None:
    """Category of the "open settings" result for query, or None if it shouldn't show."""
    # without a configured keyword, "abbr settings" still opens the dialog
    rest = strip_trigger(query.strip(), cfg.prefix or DEFAULT_PREFIX)
    if rest is None:
        return None
    rest = rest.lower()
    if rest == SETTINGS_WORD:
        return HIGHEST
    if not rest and cfg.prefix:
        return LOW  # listed along with all abbreviations
    if cfg.min_prefix_chars and len(rest) >= cfg.min_prefix_chars and SETTINGS_WORD.startswith(rest):
        return LOW
    return None


def find_matches(cfg: Config, query: str) -> list[tuple[dict, str, int]]:
    """Return (abbr, arg, category) for every abbreviation matching query."""
    query = query.strip()
    if cfg.prefix:
        query = strip_trigger(query, cfg.prefix)
        if query is None:
            return []
        if not query:  # just the keyword: list everything
            return [(abbr, "", LOW) for _, abbr in sorted(cfg.abbrs.items())]
    if not query:
        return []
    key, _, arg = query.partition(" ")
    key, arg = key.lower(), arg.strip()

    matches = []
    exact = cfg.abbrs.get(key)
    if exact and (not arg or uses_arg(exact["text"])):
        matches.append((exact, arg, HIGHEST))
    if not arg and cfg.min_prefix_chars and len(key) >= cfg.min_prefix_chars:
        for k, abbr in sorted(cfg.abbrs.items()):
            if k != key and k.startswith(key):
                matches.append((abbr, "", LOW))
    return matches


def one_line(text: str, limit: int) -> str:
    text = text.replace("\n", " ⏎ ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- D-Bus service


def run_service() -> None:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    cfg = Config(CONFIG_FILE)

    class Runner(dbus.service.Object):
        def __init__(self):
            DBusGMainLoop(set_as_default=True)
            bus = dbus.SessionBus()
            self._name = dbus.service.BusName(SERVICE, bus)
            super().__init__(bus, OBJPATH)
            # expansions shown in the last query, so Run copies exactly what was displayed
            self.shown: dict[str, str] = {}

        @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})")
        def Match(self, query: str):
            cfg.reload_if_changed()
            self.shown = {}
            results = []
            if (category := settings_match(cfg, query)) is not None:
                results.append((
                    SETTINGS_ID, "Abbreviations Settings", "configure", category,
                    1.0 if category == HIGHEST else 0.4,
                    {"subtext": "Open the settings dialog", "category": "Abbreviations"},
                ))
            for abbr, arg, category in find_matches(cfg, query):
                match_id = f"{abbr['key']}\x1f{arg}"
                try:
                    text = expand(abbr["text"], arg, cfg.cmd_timeout)
                except Exception as e:
                    results.append((
                        match_id, f"{abbr['key']}: {e}", "dialog-error", category, 0.1,
                        {"subtext": "abbreviation error"},
                    ))
                    continue
                self.shown[match_id] = text
                subtext = f"{abbr['key']} · {abbr['description']}" if abbr.get("description") \
                    else f"{abbr['key']} → copy to clipboard"
                relevance = 1.0 if category == HIGHEST else 0.5
                results.append((
                    match_id, one_line(text, 200), abbr.get("icon", "edit-paste"), category,
                    relevance, {"subtext": subtext, "category": "Abbreviations"},
                ))
            return results

        @dbus.service.method(IFACE, out_signature="a(sss)")
        def Actions(self):
            return []

        @dbus.service.method(IFACE, in_signature="ss")
        def Run(self, match_id: str, action_id: str):
            if match_id == SETTINGS_ID:
                subprocess.Popen([sys.executable, str(SETTINGS_APP)], start_new_session=True)
                return
            text = self.shown.get(match_id)
            if text is None:  # stale id: recompute
                key, _, arg = match_id.partition("\x1f")
                abbr = cfg.abbrs.get(key.lower())
                if abbr is None:
                    return
                text = expand(abbr["text"], arg, cfg.cmd_timeout)
            set_clipboard(text)

        @dbus.service.method(IFACE)
        def Teardown(self):
            self.shown = {}

    Runner()
    GLib.MainLoop().run()


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expand", nargs="+", metavar=("QUERY", "ARG"),
                    help="print the matches for QUERY (as typed in KRunner) and exit")
    ap.add_argument("--list", action="store_true", help="list all abbreviations with their expansion")
    args = ap.parse_args()

    if args.expand or args.list:
        cfg = Config(CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_CONFIG)
        cfg.reload_if_changed()
        if cfg.error:
            sys.exit(cfg.error)
        if args.list:
            for abbr in cfg.abbrs.values():
                try:
                    text = expand(abbr["text"], timeout=cfg.cmd_timeout)
                except Exception as e:
                    text = f"ERROR: {e}"
                print(f"{abbr['key']:<12} {one_line(text, SUBTEXT_MAX)}")
            return
        query = " ".join(args.expand)
        if settings_match(cfg, query) is not None:
            print("[settings] opens the settings dialog")
        for abbr, arg, category in find_matches(cfg, query):
            tag = "exact " if category == HIGHEST else "prefix"
            print(f"[{tag}] {abbr['key']}: {expand(abbr['text'], arg, cfg.cmd_timeout)}")
        return

    run_service()


if __name__ == "__main__":
    main()
