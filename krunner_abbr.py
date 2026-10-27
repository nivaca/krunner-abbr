#!/usr/bin/env python3
"""KRunner D-Bus plugin that expands abbreviations and copies them to the clipboard.

Abbreviations live in ~/.config/krunner-abbr/abbreviations.toml and may contain
dynamic placeholders such as {date}, {now+1d:%A}, {uuid}, {clipboard},
{cmd:...} or {arg}. See abbreviations.toml for the full syntax.

Run with --expand KEY [ARG...] to test an abbreviation from the shell.
"""

import argparse
import base64
import calendar
import csv
import locale
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unicodedata
import urllib.parse
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

# {name}, {name+1d-2h}, {name:spec}, {name+1w:spec}, each optionally followed by |filters
PLACEHOLDER_RE = re.compile(
    r"\{(?P<name>[a-z_]+)(?P<offsets>(?:[+-]\d+(?:mo|[smhdwMy]))*)(?P<rest>[:|][^{}]*)?\}"
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


# ---------------------------------------------------------------- filters

# words kept lowercase in English title case unless first or last (Chicago-style)
SMALL_WORDS = set(
    "a an and as at but by en for from if in into nor of off on onto or over per so the "
    "to up upon v v. vs vs. via with yet".split()
)
LINEBREAK_RE = re.compile(r"\r\n|\r|\n| | |\x0b|\x0c|\x85")


def _cap(word: str) -> str:
    """Uppercase the first letter, leaving leading punctuation and the rest untouched."""
    for i, c in enumerate(word):
        if c.isalpha():
            return word[:i] + c.upper() + word[i + 1:]
    return word


def title_case(text: str) -> str:
    if text.isupper():  # ALL CAPS input carries no case information worth keeping
        text = text.lower()

    def line_title(line: str) -> str:
        tokens = re.split(r"(\s+)", line)
        words = [i for i, t in enumerate(tokens) if t and not t.isspace()]
        for n, i in enumerate(words):
            word = tokens[i]
            core = re.sub(r"^\W+|\W+$", "", word).lower()
            first = n == 0 or re.search(r"[:.!?—–]$", tokens[words[n - 1]])
            last = n == len(words) - 1
            letters = [c for c in word if c.isalpha()]
            if any(c.isupper() for c in letters[1:]) or re.search(r"\w[./@]\w", word):
                continue  # iPhone, NASA, e.g., URLs, emails: keep as typed
            if core in SMALL_WORDS and not first and not last:
                tokens[i] = word.lower()
            else:
                parts = word.split("-")
                tokens[i] = "-".join(
                    _cap(p) if j == 0 or p.lower() not in SMALL_WORDS else p.lower()
                    for j, p in enumerate(parts)
                )
        return "".join(tokens)

    return "".join(
        line_title(part) if k % 2 == 0 else part
        for k, part in enumerate(re.split(r"(\r\n|\r|\n)", text))
    )


def sentence_case(text: str) -> str:
    text = text.lower()
    return re.sub(r"(^\s*|[.!?]\s+)(\w)", lambda m: m[1] + m[2].upper(), text)


def deaccent(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", deaccent(text))


def join_lines(text: str, sep: str = " ") -> str:
    return sep.join(p.strip() for p in LINEBREAK_RE.split(text) if p.strip())


def unhyphen(text: str) -> str:
    """Rejoin words hyphenated across line breaks (typical of text copied from PDFs)."""
    text = text.replace("­", "")
    return re.sub(r"(\w)[-‐]\s*(?:\r\n|\r|\n)\s*(\w)", r"\1\2", text)


def regex_sub(text: str, pattern: str, repl: str = "") -> str:
    try:
        return re.sub(pattern, repl, text)
    except re.error as e:
        raise ValueError(f"bad regex {pattern!r}: {e}") from None


def shell_filter(text: str, command: str, *, timeout: float) -> str:
    res = subprocess.run(command, shell=True, input=text, capture_output=True, text=True,
                         timeout=timeout)
    return res.stdout.rstrip("\n")


# name -> (function, min args, max args, description)
FILTERS = {
    "upper": (str.upper, 0, 0, "UPPERCASE"),
    "lower": (str.lower, 0, 0, "lowercase"),
    "title": (title_case, 0, 0, "English Title Case"),
    "sentence": (sentence_case, 0, 0, "Sentence case"),
    "swapcase": (str.swapcase, 0, 0, "sWAP cASE"),
    "join": (join_lines, 0, 1, "join lines with a space, or join(sep)"),
    "unhyphen": (unhyphen, 0, 0, "rejoin words hyphenated across lines"),
    "squeeze": (lambda s: re.sub(r"[ \t ]+", " ", s), 0, 0, "collapse runs of spaces"),
    "strip": (str.strip, 0, 0, "trim surrounding whitespace"),
    "replace": (lambda s, old, new="": s.replace(old, new), 1, 2, "replace(old, new)"),
    "sub": (regex_sub, 1, 2, "regex substitution: sub(pattern, repl)"),
    "deaccent": (deaccent, 0, 0, "remove diacritics: café → cafe"),
    "slug": (lambda s: "-".join(_words(s)).lower(), 0, 0, "url-slug"),
    "snake": (lambda s: "_".join(_words(s)).lower(), 0, 0, "snake_case"),
    "camel": (lambda s: "".join(w.lower() if i == 0 else w.capitalize()
                                for i, w in enumerate(_words(s))), 0, 0, "camelCase"),
    "sort": (lambda s: "\n".join(sorted(s.splitlines(), key=str.casefold)), 0, 0, "sort lines"),
    "uniq": (lambda s: "\n".join(dict.fromkeys(s.splitlines())), 0, 0, "drop duplicate lines"),
    "urlencode": (lambda s: urllib.parse.quote(s, safe=""), 0, 0, "percent-encode"),
    "urldecode": (urllib.parse.unquote, 0, 0, "percent-decode"),
    "base64": (lambda s: base64.b64encode(s.encode()).decode(), 0, 0, "base64-encode"),
    "unbase64": (lambda s: base64.b64decode(s).decode(errors="replace"), 0, 0, "base64-decode"),
    "sh": (shell_filter, 1, 1, "pipe through a shell command: sh(sort -u)"),
}

FILTER_NAME_RE = re.compile(r"\|([a-z][a-z0-9_]*)")


def parse_filter_args(raw: str) -> list[str]:
    """Comma-separated arguments; wrap one in double quotes to keep spaces or commas."""
    if not raw.strip():
        return []
    row = next(csv.reader([raw], skipinitialspace=True))
    return row


def parse_filter_chain(chain: str) -> list[tuple[str, list[str]]]:
    """Parse '|f1|f2(a, b)' into [(f1, []), (f2, [a, b])]. Raises ValueError."""
    filters, pos = [], 0
    while pos < len(chain):
        m = FILTER_NAME_RE.match(chain, pos)
        if not m:
            raise ValueError(f"expected |filter at {chain[pos:]!r}")
        name, pos = m[1], m.end()
        if name not in FILTERS:
            raise ValueError(f"unknown filter |{name}")
        args = []
        if pos < len(chain) and chain[pos] == "(":
            depth, quoted, end = 0, False, None
            for i in range(pos, len(chain)):
                c = chain[i]
                if c == '"':
                    quoted = not quoted
                elif not quoted and c == "(":
                    depth += 1
                elif not quoted and c == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end is None:
                raise ValueError(f"unclosed ( in |{name}")
            args, pos = parse_filter_args(chain[pos + 1:end]), end + 1
        _, lo, hi, _ = FILTERS[name]
        if not lo <= len(args) <= hi:
            want = f"{lo}" if lo == hi else f"{lo}–{hi}"
            raise ValueError(f"|{name} takes {want} argument(s), got {len(args)}")
        filters.append((name, args))
    return filters


def split_spec(spec: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Split 'spec|f1|f2' into spec and filters.

    A '|' only starts the filter chain if everything after it parses as filters,
    so shell pipes inside {cmd:...} keep working.
    """
    i = spec.find("|")
    while i >= 0:
        m = FILTER_NAME_RE.match(spec, i)
        if m and m[1] in FILTERS:
            try:
                return spec[:i], parse_filter_chain(spec[i:])
            except ValueError:
                pass
        i = spec.find("|", i + 1)
    return spec, []


def apply_filters(value: str, filters, timeout: float) -> str:
    for name, args in filters:
        func = FILTERS[name][0]
        value = func(value, *args, timeout=timeout) if name == "sh" else func(value, *args)
    return value


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
        rest = m["rest"] or ""
        if rest.startswith(":"):
            spec, filters = split_spec(rest[1:])
        else:
            spec, filters = None, parse_filter_chain(rest)
        return apply_filters(value(m, spec), filters, timeout)

    def value(m: re.Match, spec: str | None) -> str:
        name, offsets = m["name"], m["offsets"]
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
        if name == "input":  # text typed after the abbreviation, else the clipboard
            return arg or get_clipboard()
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
    return any(m["name"] in ("arg", "cmd", "input") for m in PLACEHOLDER_RE.finditer(template))


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
            # the Object keeps the BusName alive; storing it ourselves as self._name would be
            # clobbered by Object.__init__, releasing the name immediately
            super().__init__(dbus.service.BusName(SERVICE, dbus.SessionBus()), OBJPATH)
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
