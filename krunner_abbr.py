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
import random
import re
import shutil
import subprocess
import sys
import threading
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

DEFAULT_PREFIX = "ex"
SETTINGS_WORD = "settings"  # "<keyword> settings" opens the settings dialog
# built-in commands: word -> (title, subtext, icon); match ids are "\x1e" + word
BUILTINS = {
    SETTINGS_WORD: ("Abbreviations Settings", "Open the settings dialog", "configure"),
    "help": ("Abbreviations Help", "Open the reference: placeholders, filters, quote styles",
             "help-contents"),
    "list": ("All Abbreviations", "Open a table of every abbreviation and its expansion",
             "view-list-details"),
}
BUILTIN_ID = "\x1e"

# KRunner match categories (Plasma 6 QueryMatch::CategoryRelevance)
HIGHEST = 100
LOW = 30

CMD_TIMEOUT = 2.0  # default seconds for {cmd:...} placeholders
LAZY_TIMEOUT = 20.0  # seconds for abbreviations computed only when Enter is pressed
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


ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
         (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def quoted(text: str, limit: int = 40) -> str:
    """Short quoted excerpt of input for error messages."""
    return f"“{one_line(text.strip(), limit)}”"


def to_roman(text: str) -> str:
    try:
        n = int(text.strip())
    except ValueError:
        raise ValueError(f"|roman needs a whole number, got {quoted(text)}") from None
    if not 0 < n < 4000:
        raise ValueError(f"|roman works for 1–3999, got {n}")
    out = ""
    for value, numeral in ROMAN:
        count, n = divmod(n, value)
        out += numeral * count
    return out


def from_roman(text: str) -> str:
    numeral = text.strip().upper()
    values = {s: v for v, s in ROMAN if len(s) == 1}
    if not numeral or any(c not in values for c in numeral):
        raise ValueError(f"not a Roman numeral: {quoted(text)}")
    total = 0
    for i, c in enumerate(numeral):
        v = values[c]
        total += -v if i + 1 < len(numeral) and values[numeral[i + 1]] > v else v
    if not 0 < total < 4000 or to_roman(str(total)) != numeral:
        raise ValueError(f"not a valid Roman numeral: {quoted(text)}")
    return str(total)


def no_macrons(text: str) -> str:
    """Remove macrons and breves (Latin vowel quantity), keeping other accents."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", re.sub("[\u0304\u0306]", "", decomposed))


LATEX_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


# open, close
QUOTE_STYLES = {
    "straight": ('"', '"'),
    "straight-single": ("'", "'"),
    "double": ("“", "”"),
    "single": ("‘", "’"),
    "guillemets": ("«", "»"),
    "single-guillemets": ("‹", "›"),
    "french": ("« ", " »"),  # guillemets with narrow no-break spaces inside
    "low": ("„", "“"),
    "low-single": ("‚", "‘"),
}
QUOTE_OPENERS = "“‘«‹„‚"  # straight quotes are ambiguous; see opened_last below
GUILLEMET_PADDING = "   "


def quote_style(name: str) -> tuple[str, str]:
    """A style name, or the literal pair itself: «», “”, or one straight character."""
    if name in QUOTE_STYLES:
        return QUOTE_STYLES[name]
    if len(name) == 2:
        return name[0], name[1]
    if len(name) == 1:
        return name, name
    raise ValueError(f"unknown quote style {name!r}; use {', '.join(QUOTE_STYLES)} or a pair like «»")


def convert_quotes(text: str, mapping: list[tuple[tuple[str, str], tuple[str, str]]],
                   apostrophe: str | None = None) -> str:
    """Convert quote pairs in one pass, so nested styles can be swapped safely.

    mapping holds (source, target) styles. Pairs are matched open/close; a quote
    character between two letters is an apostrophe and is left alone (or replaced
    by `apostrophe`). Spaces just inside source guillemets are dropped.
    """
    depth = [0] * len(mapping)
    out: list[str] = []
    opened_last = False  # the previous character was converted into an opening quote
    i = 0
    while i < len(text):
        c = text[i]
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if c in "'’" and prev.isalnum() and nxt.isalpha():
            out.append(apostrophe or c)
            opened_last = False
            i += 1
            continue
        opening_context = (opened_last or not prev or prev.isspace()
                           or prev in "([{<—–-/" + QUOTE_OPENERS)
        handled = opened_last = False
        # closing a pending pair wins over opening a new one („…“ vs “…”)
        for k, ((so, sc), (_, dc)) in enumerate(mapping):
            if c == sc[-1] and depth[k] and not (so == sc and opening_context):
                if sc[-1] in "»›":
                    while out and out[-1] in GUILLEMET_PADDING:
                        out.pop()
                out.append(dc)
                depth[k] -= 1
                handled = True
                break
        if not handled:
            for k, ((so, sc), (do, _)) in enumerate(mapping):
                if c == so[0] and (so != sc or opening_context):
                    out.append(do)
                    depth[k] += 1
                    handled = opened_last = True
                    if so[0] in "«‹":
                        while i + 1 < len(text) and text[i + 1] in GUILLEMET_PADDING:
                            i += 1
                    break
        if not handled:
            out.append(apostrophe if apostrophe and c == "’" else c)
        i += 1
    return "".join(out)


def quotes_filter(text: str, *styles: str) -> str:
    if len(styles) % 2:
        raise ValueError("|quotes takes pairs of styles: quotes(from, to, from, to, …)")
    mapping = [(quote_style(styles[j]), quote_style(styles[j + 1]))
               for j in range(0, len(styles), 2)]
    return convert_quotes(text, mapping)


def smart_quotes(text: str) -> str:
    return convert_quotes(text, [(QUOTE_STYLES["straight"], QUOTE_STYLES["double"]),
                                 (QUOTE_STYLES["straight-single"], QUOTE_STYLES["single"])],
                          apostrophe="’")


def straight_quotes(text: str) -> str:
    double, single = QUOTE_STYLES["straight"], QUOTE_STYLES["straight-single"]
    return convert_quotes(text, [
        (QUOTE_STYLES["double"], double), (QUOTE_STYLES["guillemets"], double),
        (QUOTE_STYLES["low"], double), (QUOTE_STYLES["single"], single),
        (QUOTE_STYLES["single-guillemets"], single), (QUOTE_STYLES["low-single"], single),
    ], apostrophe="'")


def text_stats(text: str) -> str:
    def count(n: int, noun: str) -> str:
        return f"{n:,} {noun}" + ("" if n == 1 else "s")

    return " · ".join([count(len(text.split()), "word"), count(len(text), "character"),
                       count(len(text.splitlines()), "line")])


def _natural_key(item: str) -> list:
    """Case- and accent-insensitive, locale-aware, numbers by value: item2 < item10."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, locale.strxfrm(p.casefold()))
            for p in re.split(r"(\d+)", item.strip()) if p]


def _sorted(items: list[str], options: tuple[str, ...]) -> list[str]:
    unknown = set(options) - {"reverse"}
    if unknown:
        raise ValueError(f"unknown sort option {', '.join(sorted(unknown))}; use reverse")
    return sorted(items, key=_natural_key, reverse="reverse" in options)


def sort_lines(text: str, *options: str) -> str:
    return "\n".join(_sorted([l for l in text.splitlines() if l.strip()], options))


def sort_items(text: str, sep: str = ", ", *options: str) -> str:
    """Sort a one-line list such as "pear, apple, fig" (split on sep, rejoined with sep)."""
    items = [i.strip() for i in text.split(sep.strip() or sep) if i.strip()]
    return sep.join(_sorted(items, options))


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
    "sort": (sort_lines, 0, 1, "sort lines: natural order (2 < 10), ignoring case and accents; sort(reverse)"),
    "sortitems": (sort_items, 0, 2, 'sort a one-line list: sortitems(", ") or sortitems(";", reverse)'),
    "uniq": (lambda s: "\n".join(dict.fromkeys(s.splitlines())), 0, 0, "drop duplicate lines"),
    "reverse": (lambda s: "\n".join(reversed(s.splitlines())), 0, 0, "reverse the order of lines"),
    "shuffle": (lambda s: "\n".join(random.sample(s.splitlines(), len(s.splitlines()))), 0, 0,
                "shuffle lines"),
    "number": (lambda s: "\n".join(f"{i}. {l}" for i, l in enumerate(
        (l for l in s.splitlines() if l.strip()), 1)), 0, 0, "number lines: 1. 2. 3."),
    "urlencode": (lambda s: urllib.parse.quote(s, safe=""), 0, 0, "percent-encode"),
    "urldecode": (urllib.parse.unquote, 0, 0, "percent-decode"),
    "base64": (lambda s: base64.b64encode(s.encode()).decode(), 0, 0, "base64-encode"),
    "unbase64": (lambda s: base64.b64decode(s).decode(errors="replace"), 0, 0, "base64-decode"),
    "roman": (to_roman, 0, 0, "number → Roman numeral: 2026 → MMXXVI"),
    "unroman": (from_roman, 0, 0, "Roman numeral → number: xiv → 14"),
    "nomacrons": (no_macrons, 0, 0, "remove macrons and breves: ā ă → a"),
    "vtou": (lambda s: s.replace("v", "u").replace("V", "U"), 0, 0, "Latin orthography: v → u"),
    "jtoi": (lambda s: s.replace("j", "i").replace("J", "I"), 0, 0, "Latin orthography: j → i"),
    "latex": (lambda s: "".join(LATEX_ESCAPES.get(c, c) for c in s), 0, 0,
              "escape LaTeX special characters: & % $ # _ { } ~ ^ \\"),
    "quotes": (quotes_filter, 2, 8, "convert quote styles: quotes(from, to)"),
    "smartquotes": (smart_quotes, 0, 0, "straight → typographic quotes and apostrophes"),
    "straightquotes": (straight_quotes, 0, 0, "typographic quotes and guillemets → straight"),
    "stats": (text_stats, 0, 0, "word, character and line counts"),
    "words": (lambda s: str(len(s.split())), 0, 0, "word count"),
    "silent": (lambda s: "", 0, 0, "discard the value, e.g. an {ask} used only by a later {cmd}"),
    "chars": (lambda s: str(len(s)), 0, 0, "character count"),
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


# ---------------------------------------------------------------- notifications


_last_notification = 0  # replaced by the next notification instead of stacking up


def notify(summary: str, body: str = "", icon: str = "edit-paste") -> None:
    global _last_notification
    try:
        import dbus

        _last_notification = int(dbus.Interface(
            dbus.SessionBus().get_object(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
            ),
            "org.freedesktop.Notifications",
        ).Notify("Abbreviations", dbus.UInt32(_last_notification), icon, summary, body,
                 dbus.Array([], "s"), dbus.Dictionary({"transient": dbus.Boolean(True)}, "sv"),
                 5000))
    except Exception as e:
        print(f"notification failed: {e}", file=sys.stderr)


def notify_copied(abbr: dict, text: str) -> None:
    body = one_line(text, 300) if text.strip() else abbr.get("description", "")
    notify(f"Copied “{abbr['key']}”", body, abbr.get("icon", "edit-paste"))


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


ASK_NAMES = ("ask", "asktext")  # {ask:Label=default} one line, {asktext:Label} multi-line


def parse_ask(spec: str | None) -> tuple[str, str]:
    label, _, default = (spec or "").partition("=")
    return label.strip() or "Input", default


def ask_prompts(template: str) -> list[tuple[str, str, str]]:
    """(name, label, default) for each prompt in template, in order; repeats ask once."""
    prompts, seen = [], set()
    for m in PLACEHOLDER_RE.finditer(template):
        if m["name"] not in ASK_NAMES:
            continue
        rest = m["rest"] or ""
        label, default = parse_ask(split_spec(rest[1:])[0] if rest.startswith(":") else None)
        if (m["name"], label) not in seen:
            seen.add((m["name"], label))
            prompts.append((m["name"], label, default))
    return prompts


def needs_input(template: str) -> bool:
    return any(m["name"] in ASK_NAMES for m in PLACEHOLDER_RE.finditer(template))


def kdialog_prompt(name: str, label: str, default: str) -> str | None:
    """Ask with a KDE dialog; None if cancelled."""
    box = "--textinputbox" if name == "asktext" else "--inputbox"
    res = subprocess.run(["kdialog", "--title", "Abbreviations", box, label, default],
                         capture_output=True, text=True)
    return res.stdout.removesuffix("\n") if res.returncode == 0 else None


def terminal_prompt(name: str, label: str, default: str) -> str | None:
    """Ask on the terminal (for --expand); {asktext} reads until an empty line."""
    try:
        if name == "asktext":
            print(f"{label} (end with an empty line):")
            lines = iter(input, "")
            return "\n".join(lines) or default
        return input(f"{label}{f' [{default}]' if default else ''}: ") or default
    except EOFError:
        return None


def collect_answers(template: str, prompt=kdialog_prompt) -> dict | None:
    """Prompt for every {ask}/{asktext} in template; None if the user cancels."""
    answers = {}
    for name, label, default in ask_prompts(template):
        value = prompt(name, label, default)
        if value is None:
            return None
        answers[(name, label)] = value
    return answers


def expand(template: str, arg: str = "", timeout: float = CMD_TIMEOUT,
           clipboard: str | None = None, answers: dict | None = None) -> str:
    """Expand all placeholders in template. Raises ValueError on bad input.

    Pass clipboard to use a pre-read value instead of querying the clipboard, and
    answers (from collect_answers) to fill {ask} and {asktext} prompts.
    """
    now = datetime.now().astimezone()

    def clip() -> str:
        return get_clipboard() if clipboard is None else clipboard

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
            return clip()
        if name == "input":  # text typed after the abbreviation, else the clipboard
            return arg or clip()
        if name in ASK_NAMES:
            key = (name, parse_ask(spec)[0])
            if answers is None or key not in answers:
                raise ValueError(f"{m[0]} asks for input when Enter is pressed")
            return answers[key]
        if name == "cmd":
            if not spec:
                raise ValueError("{cmd:...} needs a command")
            env = dict(os.environ, ABBR_ARG=arg)
            for i, value in enumerate((answers or {}).values(), 1):
                env[f"ABBR_ASK{i}"] = value
                env.setdefault("ABBR_ASK", value)
            if "ABBR_INPUT" in spec:
                env["ABBR_INPUT"] = (arg or clip()).strip()
            res = subprocess.run(
                spec, shell=True, capture_output=True, text=True, timeout=timeout, env=env
            )
            return res.stdout.rstrip("\n")
        raise ValueError(f"unknown placeholder {m[0]}")

    # scan left to right like str.format: {{ and }} are literal braces, so
    # \emph{{{clipboard}}} is "\emph{" + placeholder + "}"
    out, i = [], 0
    while i < len(template):
        c = template[i]
        if c in "{}" and template.startswith(c * 2, i):
            out.append(c)
            i += 2
        elif c == "{" and (m := PLACEHOLDER_RE.match(template, i)):
            out.append(repl(m))
            i = m.end()
        else:
            out.append(c)
            i += 1
    return "".join(out)


def is_lazy(abbr: dict) -> bool:
    """Computed only when Enter is pressed: marked lazy, or prompts for input."""
    return bool(abbr.get("lazy")) or needs_input(abbr["text"])


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
        self.notify = False
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
        self.notify = bool(settings.get("notify", False))
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
    # a word keyword ("ex") needs a space after it; a symbol (";") does not
    if trigger[-1].isalnum() and rest and not rest[0].isspace():
        return None
    return rest.lstrip()


def builtin_matches(cfg: Config, query: str) -> list[tuple[str, int]]:
    """(word, category) for each built-in command (settings, help, list) matching query."""
    # without a configured keyword, "ex settings" etc. still work
    rest = strip_trigger(query.strip(), cfg.prefix or DEFAULT_PREFIX)
    if rest is None:
        return []
    rest = rest.lower()
    matches = []
    for word in BUILTINS:
        if rest == word:
            matches.append((word, HIGHEST))
        elif not rest and cfg.prefix:
            matches.append((word, LOW))  # listed along with all abbreviations
        elif cfg.min_prefix_chars and len(rest) >= cfg.min_prefix_chars and word.startswith(rest):
            matches.append((word, LOW))
    return matches


def run_builtin(cfg: Config, word: str) -> None:
    if word == SETTINGS_WORD:
        subprocess.Popen([sys.executable, str(SETTINGS_APP)], start_new_session=True)
        return
    import krunner_abbr_help

    try:
        path = krunner_abbr_help.write_page(word, cfg)
    except Exception as e:
        notify(f"Could not build the {word} page", str(e), "dialog-error")
        return
    subprocess.Popen(["xdg-open", str(path)], start_new_session=True)


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
            for word, category in builtin_matches(cfg, query):
                title, subtext, icon = BUILTINS[word]
                results.append((
                    BUILTIN_ID + word, title, icon, category,
                    1.0 if category == HIGHEST else 0.4,
                    {"subtext": subtext, "category": "Abbreviations"},
                ))
            for abbr, arg, category in find_matches(cfg, query):
                match_id = f"{abbr['key']}\x1f{arg}"
                relevance = 1.0 if category == HIGHEST else 0.5
                if is_lazy(abbr):  # computed in Run: slow commands, or prompts for input
                    label = abbr.get("description") or one_line(abbr["text"], 200)
                    hint = ("press Enter, then fill in the prompt" if needs_input(abbr["text"])
                            else "press Enter to run and copy")
                    results.append((
                        match_id, f"{label}: {arg}" if arg else label,
                        abbr.get("icon", "system-run"), category, relevance,
                        {"subtext": f"{abbr['key']} · {hint}", "category": "Abbreviations"},
                    ))
                    continue
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
            if match_id.startswith(BUILTIN_ID):
                run_builtin(cfg, match_id[len(BUILTIN_ID):])
                return
            key, _, arg = match_id.partition("\x1f")
            abbr = cfg.abbrs.get(key.lower())
            text = self.shown.get(match_id)
            if abbr is None and text is None:
                return
            if abbr is not None and is_lazy(abbr):
                self.run_lazy(abbr, arg)
                return
            if text is None:  # stale id: recompute
                try:
                    text = expand(abbr["text"], arg, cfg.cmd_timeout)
                except Exception as e:
                    notify(f"“{abbr['key']}” failed", str(e), "dialog-error")
                    return
            set_clipboard(text)
            if cfg.notify:
                notify_copied(abbr or {"key": key}, text)

        def run_lazy(self, abbr: dict, arg: str):
            """Expand in a thread so slow commands don't block KRunner's queries."""
            clipboard = get_clipboard()  # D-Bus calls stay on the main thread

            def work():
                try:
                    answers = collect_answers(abbr["text"])
                    if answers is None:
                        return  # prompt cancelled: copy nothing
                    text, error = expand(abbr["text"], arg, LAZY_TIMEOUT, clipboard, answers), None
                except Exception as e:
                    text, error = None, e
                GLib.idle_add(finish, text, error)

            def finish(text, error):
                if error is not None:
                    notify(f"“{abbr['key']}” failed", str(error), "dialog-error")
                elif not text.strip():
                    notify(f"“{abbr['key']}” produced no output", "Nothing was copied.",
                           "dialog-warning")
                else:
                    set_clipboard(text)
                    notify_copied(abbr, text)  # always: the result wasn't shown in KRunner
                return False

            threading.Thread(target=work, daemon=True).start()

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
    ap.add_argument("--page", choices=["help", "list"],
                    help="write the help or list page and print its path")
    args = ap.parse_args()

    if args.page:
        import krunner_abbr_help

        cfg = Config(CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_CONFIG)
        cfg.reload_if_changed()
        print(krunner_abbr_help.write_page(args.page, cfg))
        return

    if args.expand or args.list:
        cfg = Config(CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_CONFIG)
        cfg.reload_if_changed()
        if cfg.error:
            sys.exit(cfg.error)
        if args.list:
            for abbr in cfg.abbrs.values():
                if needs_input(abbr["text"]):
                    labels = ", ".join(label for _, label, _ in ask_prompts(abbr["text"]))
                    print(f"{abbr['key']:<12} (asks for: {labels})")
                    continue
                try:
                    text = expand(abbr["text"], timeout=cfg.cmd_timeout)
                except Exception as e:
                    text = f"ERROR: {e}"
                print(f"{abbr['key']:<12} {one_line(text, SUBTEXT_MAX)}")
            return
        query = " ".join(args.expand)
        for word, _ in builtin_matches(cfg, query):
            print(f"[{word}] {BUILTINS[word][1]}")
        for abbr, arg, category in find_matches(cfg, query):
            tag = "exact " if category == HIGHEST else "prefix"
            timeout = LAZY_TIMEOUT if is_lazy(abbr) else cfg.cmd_timeout
            answers = None
            if needs_input(abbr["text"]):
                if category != HIGHEST or not sys.stdin.isatty():
                    print(f"[{tag}] {abbr['key']}: (asks for input)")
                    continue
                answers = collect_answers(abbr["text"], terminal_prompt)
            try:
                text = expand(abbr["text"], arg, timeout, answers=answers)
            except Exception as e:
                text = f"ERROR: {e}"
            print(f"[{tag}] {abbr['key']}: {text}")
        return

    run_service()


if __name__ == "__main__":
    main()
