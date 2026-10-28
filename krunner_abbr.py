#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""KRunner D-Bus plugin that expands abbreviations and copies them to the clipboard.

Abbreviations live in ~/.config/krunner-abbr/abbreviations.toml and may contain
dynamic placeholders such as {date}, {now+1d:%A}, {uuid}, {clipboard},
{cmd:...} or {arg}. See abbreviations.toml for the full syntax.

Run with --expand KEY [ARG...] to test an abbreviation from the shell.
"""

import argparse
import ast
import base64
import calendar
import csv
import html
import json
import locale
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import unicodedata
import urllib.parse
import uuid
from datetime import datetime, timedelta
from pathlib import Path

__version__ = "0.1"

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
# other words that run a built-in command
BUILTIN_ALIASES = {"?": "help"}
BUILTIN_ID = "\x1e"
CASE_WORD = "case"  # "<keyword> case [text]" lists every case style of the text or clipboard
CASE_ID = "\x1d"  # case-picker match ids are "\x1d" + filter + "\x1f" + typed text
CASE_FILTERS = [
    "upper", "lower", "title", "sentence", "capitalize", "swapcase", "alternating",
    "snake", "kebab", "camel", "pascal", "constant", "dot", "train", "ada", "cobol",
    "path", "flat", "space", "start",
]
CLIP_WORD = "clip"  # "<keyword> clip [search]" lists Klipper's history
CLIP_ID = "\x1b"  # clipboard-history match ids are "\x1b" + position
CLIP_MAX = 30  # history entries listed
FILTER_ID = "\x1c"  # filter-chain match ids are "\x1c" + chain + "\x1f" + typed text
FILTER_ICON = "view-filter"
# buttons on each match: id -> (text, icon)
ACTIONS = {
    "paste": ("Copy and paste into the previous window", "edit-paste"),
    "copy": ("Copy without pasting", "edit-copy"),  # replaces "paste" when Enter pastes
    "template": ("Copy the template instead of the expansion", "text-x-generic"),
    "edit": ("Edit this abbreviation", "document-edit"),
}
# applications (KWin resource class or desktop file name, lowercase) that paste with other keys;
# [paste_keys] in the config adds to and overrides these
TERMINAL_PASTE_KEYS = {app: "ctrl+shift+v" for app in (
    "konsole", "org.kde.konsole", "yakuake", "org.kde.yakuake", "kitty", "alacritty",
    "foot", "footclient", "wezterm", "org.wezfurlong.wezterm", "ghostty", "com.mitchellh.ghostty",
    "gnome-terminal-server", "org.gnome.terminal", "org.gnome.console", "kgx", "ptyxis",
    "org.gnome.ptyxis", "tilix", "com.gexperts.tilix", "terminator", "xterm", "hyper",
    "cool-retro-term", "contour", "rio",
)}
PASTE_DELAY = 0.35
WINDOW_SCRIPT_NAME = "krunner-abbr-active-window"
WINDOW_SCRIPT = Path.home() / ".cache" / "krunner-abbr" / "active-window.js"
WINDOW_JS = f"""const w = workspace.activeWindow;
callDBus("{SERVICE}", "{OBJPATH}", "{SERVICE}", "ReportWindow",
         w ? w.resourceClass + "|" + w.desktopFileName : "");
"""  # seconds for KRunner to close and the previous window to get focus

# KRunner match categories (Plasma 6 QueryMatch::CategoryRelevance)
HIGHEST = 100
LOW = 30
HIGH = 70  # the keyword alone: abbreviations whose `when` matches the clipboard
LOWEST = 0  # matched by description or keywords

CMD_TIMEOUT = 2.0  # default seconds for {cmd:...} placeholders
LAZY_TIMEOUT = 20.0  # seconds for abbreviations computed only when Enter is pressed
SUBTEXT_MAX = 80

# {name}, {name+1d-2h}, {name:spec}, {name+1w:spec}, {name@es:spec}, each optionally
# followed by |filters
PLACEHOLDER_RE = re.compile(
    r"\{(?P<name>[a-z_]+\d*)(?P<offsets>(?:[+-]\d+(?:mo|[smhdwMy]))*)(?:@(?P<lang>[A-Za-z_]+))?"
    r"(?P<rest>[:|][^{}]*)?\}"
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


def get_selection() -> str:
    """The selected (highlighted) text: the primary selection."""
    for cmd in (["wl-paste", "--primary", "--no-newline"], ["xclip", "-o", "-selection", "primary"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout
            except Exception:
                pass
    return ""


def get_clipboard_entry(pos: int) -> str:
    """Entry pos of Klipper's history (1 = current clipboard); "" if there is none."""
    try:
        return str(_dbus_klipper().getClipboardHistoryItem(pos - 1))
    except Exception:
        return get_clipboard() if pos == 1 else ""


def get_clipboard_history() -> list[str]:
    """Klipper's history, most recent (the current clipboard) first."""
    try:
        return [str(t) for t in _dbus_klipper().getClipboardHistoryMenu()]
    except Exception:
        current = get_clipboard()
        return [current] if current else []


def set_clipboard(text: str, html_text: str | None = None) -> None:
    """Copy text; with html_text, copy that as text/html instead (rich-text targets)."""
    if html_text is not None:
        for cmd in (["wl-copy", "--type", "text/html"],
                    ["xclip", "-selection", "clipboard", "-t", "text/html"]):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True,
                                 start_new_session=True).communicate(html_text)
                return
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


# key name -> (evdev code for ydotool, wtype name, xdotool name)
KEYS = {
    "ctrl": (29, "ctrl", "ctrl"), "shift": (42, "shift", "shift"), "alt": (56, "alt", "alt"),
    "super": (125, "logo", "super"), "v": (47, "v", "v"), "insert": (110, "Insert", "Insert"),
    "left": (105, "Left", "Left"),
}
MODIFIERS = ("ctrl", "shift", "alt", "super")


def parse_keys(combo: str) -> tuple[list[str], str]:
    """'ctrl+shift+v' -> (['ctrl', 'shift'], 'v'). Raises ValueError."""
    *mods, key = [k.strip().lower() for k in combo.split("+")]
    if key not in KEYS or key in MODIFIERS or any(m not in MODIFIERS for m in mods):
        raise ValueError(f"unsupported paste keys “{combo}”: use ctrl+v, ctrl+shift+v or "
                         "shift+insert")
    return mods, key


def paste_commands(combo: str = "ctrl+v", back: int = 0) -> list[list[str]]:
    """Commands that press combo in the focused window, then Left back times; best first."""
    mods, key = parse_keys(combo)
    code = {k: v[0] for k, v in KEYS.items()}
    ydotool = ["ydotool", "key", *[f"{code[m]}:1" for m in mods], f"{code[key]}:1",
               f"{code[key]}:0", *[f"{code[m]}:0" for m in reversed(mods)],
               *["105:1", "105:0"] * back]
    wkey = KEYS[key][1]
    wtype = ["wtype", *[a for m in mods for a in ("-M", KEYS[m][1])],
             *(["-k", wkey] if len(wkey) > 1 else [wkey]),
             *[a for m in reversed(mods) for a in ("-m", KEYS[m][1])], *["-k", "Left"] * back]
    cmds = [ydotool, wtype]
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":  # on Wayland it reaches only XWayland
        cmds.append(["xdotool", "key", "--clearmodifiers",
                     "+".join([KEYS[m][2] for m in mods] + [KEYS[key][2]]), *["Left"] * back])
    return [c for c in cmds if shutil.which(c[0])]


def paste_clipboard(combo: str = "ctrl+v", back: int = 0) -> None:
    """Press combo (Ctrl+V) in the focused window, then move the cursor back characters.

    Raises RuntimeError if no tool can.
    """
    errors = []
    for cmd in paste_commands(combo, back):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return
        errors.append(f"{cmd[0]}: {(r.stderr or r.stdout).strip() or r.returncode}")
    raise RuntimeError("; ".join(errors) or
                       "install ydotool (and enable its service) or wtype to paste automatically")


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


def spanish_title(text: str) -> str:
    """Spanish title (RAE): capital on the first word only, keeping proper nouns and acronyms.

    Proper nouns are recognised only in otherwise lowercase input: in Every Word
    Capitalized input they can't be told apart, so all but acronyms are lowercased.
    """
    if text.isupper():
        text = text.lower()
    rest = [w for w in re.findall(r"[^\W\d_]+", text)][1:]
    titled = sum(w[0].isupper() for w in rest) > len(rest) / 2

    def word(m: re.Match) -> str:
        w = m[0]
        if len(w) > 1 and (w.isupper() or any(c.isupper() for c in w[1:])):
            return w  # ONU, McCarthy
        return w.lower() if titled else w

    text = re.sub(r"[^\W\d_]+", word, text)
    return re.sub(r"(^\s*|[.!?]\s+)([¿¡«“\"'(\[]*)([^\W\d_])",
                  lambda m: m[1] + m[2] + m[3].upper(), text, flags=re.M)


def _md_inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m[1] or m[2]}</b>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)",
                  lambda m: f"<i>{m[1] or m[2]}</i>", text)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                  lambda m: f'<a href="{m[2].replace(chr(34), "%22")}">{m[1]}</a>', text)


def markdown_to_html(md: str) -> str:
    """A small Markdown subset: paragraphs, line breaks, # headings, - and 1. lists,
    **bold**, *italic*, `code` and [links](url)."""
    out = []
    for block in re.split(r"\n\s*\n", md.strip()):
        lines = block.splitlines()
        if all(re.match(r"\s*[-*+] ", l) for l in lines):
            items = "".join(f"<li>{_md_inline(re.sub(r'^\s*[-*+] ', '', l))}</li>" for l in lines)
            out.append(f"<ul>{items}</ul>")
        elif all(re.match(r"\s*\d+[.)] ", l) for l in lines):
            items = "".join(f"<li>{_md_inline(re.sub(r'^\s*\d+[.)] ', '', l))}</li>" for l in lines)
            out.append(f"<ol>{items}</ol>")
        elif len(lines) == 1 and (m := re.match(r"(#{1,6})\s+(.*)", block)):
            out.append(f"<h{len(m[1])}>{_md_inline(m[2])}</h{len(m[1])}>")
        else:
            out.append(f"<p>{'<br>'.join(_md_inline(l) for l in lines)}</p>")
    return "\n".join(out)


def deaccent(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", deaccent(text))


def _ident_words(text: str) -> list[str]:
    """Words for identifier styles, also splitting camelCase: parseHTTPResponse2 → parse HTTP Response2."""
    return [p for w in _words(text)
            for p in re.findall(r"[A-Z]+(?![a-z])\d*|[A-Z]?[a-z]+\d*|\d+[a-z]*|[^\W_]+", w)]


def capitalize_words(text: str) -> str:
    """Capitalize Every Word, without title-case rules for small words."""
    return re.sub(r"\S+", lambda m: _cap(m[0].lower()), text)


def alternating_case(text: str) -> str:
    """aLtErNaTiNg cAsE, counting letters only so spaces and punctuation don't break the rhythm."""
    n = 0
    out = []
    for c in text:
        if c.isalpha():
            out.append(c.upper() if n % 2 else c.lower())
            n += 1
        else:
            out.append(c)
    return "".join(out)


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


def no_ligatures(text: str) -> str:
    """æ œ → ae oe (AE OE before a capital, Ae Oe otherwise)."""
    def split(m: re.Match) -> str:
        base = {"æ": "ae", "œ": "oe", "Æ": "Ae", "Œ": "Oe"}[m[1]]
        return (base.upper() if m[2].isupper() else base) + m[2]
    return re.sub(r"([æœÆŒ])(.?)", split, text)


def latinize(text: str) -> str:
    """Normalize Latin for searching and comparing: ligatures, macrons, breves, diaereses, v, j."""
    decomposed = unicodedata.normalize("NFD", no_ligatures(text))
    text = unicodedata.normalize("NFC", re.sub("[\u0304\u0306\u0308]", "", decomposed))
    return text.translate(str.maketrans("vVjJ", "uUiI"))


_CALC_OPS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}


def calc(expr: str) -> str:
    """Arithmetic: + - * / // % ** ( ), round(x, n), abs(x). 1,5 is read as 1.5."""
    text = expr.strip().replace("×", "*").replace("÷", "/").replace("^", "**")
    if "." not in text:
        text = re.sub(r"(\d),(\d)", r"\1.\2", text)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return ev(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(b) > 100:
                raise ValueError("exponent too large")
            return _CALC_OPS[type(node.op)](a, b)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("round", "abs") and not node.keywords):
            return {"round": round, "abs": abs}[node.func.id](*map(ev, node.args))
        raise ValueError(f"can't calculate “{expr.strip()}”")

    try:
        value = ev(ast.parse(text, mode="eval"))
    except (SyntaxError, ZeroDivisionError, TypeError) as e:
        raise ValueError(f"can't calculate “{expr.strip()}”: {e}") from None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return f"{value:.10g}" if isinstance(value, float) else str(value)


def date_format(text: str, fmt: str, lang: str = "") -> str:
    """Reformat an ISO date or date-time: datefmt(%A %d de %B, es)."""
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"datefmt needs an ISO date such as 2026-10-28, got “{text.strip()}”")
    return strftime_in(dt, fmt, lang or None)


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
    "titulo": (spanish_title, 0, 0, "Spanish title: Cien años de soledad"),
    "sentence": (sentence_case, 0, 0, "Sentence case"),
    "swapcase": (str.swapcase, 0, 0, "sWAP cASE"),
    "capitalize": (capitalize_words, 0, 0, "Capitalize Every Word"),
    "alternating": (alternating_case, 0, 0, "aLtErNaTiNg cAsE"),
    "randomcase": (lambda s: "".join(random.choice((c.upper(), c.lower())) for c in s), 0, 0,
                   "rAnDOm CasE"),
    "join": (join_lines, 0, 1, "join lines with a space, or join(sep)"),
    "unhyphen": (unhyphen, 0, 0, "rejoin words hyphenated across lines"),
    "squeeze": (lambda s: re.sub(r"[ \t ]+", " ", s), 0, 0, "collapse runs of spaces"),
    "strip": (str.strip, 0, 0, "trim surrounding whitespace"),
    "replace": (lambda s, old, new="": s.replace(old, new), 1, 2, "replace(old, new)"),
    "sub": (regex_sub, 1, 2, "regex substitution: sub(pattern, repl)"),
    "deaccent": (deaccent, 0, 0, "remove diacritics: café → cafe"),
    "slug": (lambda s: "-".join(_words(s)).lower(), 0, 0, "url-slug"),
    "snake": (lambda s: "_".join(_ident_words(s)).lower(), 0, 0, "snake_case"),
    "kebab": (lambda s: "-".join(_ident_words(s)).lower(), 0, 0, "kebab-case"),
    "constant": (lambda s: "_".join(_ident_words(s)).upper(), 0, 0, "CONSTANT_CASE"),
    "dot": (lambda s: ".".join(_ident_words(s)).lower(), 0, 0, "dot.case"),
    "camel": (lambda s: "".join(w.lower() if i == 0 else w.capitalize()
                                for i, w in enumerate(_ident_words(s))), 0, 0, "camelCase"),
    "pascal": (lambda s: "".join(w.capitalize() for w in _ident_words(s)), 0, 0, "PascalCase"),
    "train": (lambda s: "-".join(w.capitalize() for w in _ident_words(s)), 0, 0, "Train-Case"),
    "ada": (lambda s: "_".join(w.capitalize() for w in _ident_words(s)), 0, 0, "Ada_Case"),
    "cobol": (lambda s: "-".join(_ident_words(s)).upper(), 0, 0, "COBOL-CASE"),
    "path": (lambda s: "/".join(_ident_words(s)).lower(), 0, 0, "path/case"),
    "flat": (lambda s: "".join(_ident_words(s)).lower(), 0, 0, "flatcase"),
    "space": (lambda s: " ".join(_ident_words(s)).lower(), 0, 0, "identifier → lowercase words"),
    "start": (lambda s: " ".join(_cap(w) for w in _ident_words(s)), 0, 0,
              "identifier → Start Case, keeping acronyms: parseHTTP → Parse HTTP"),
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
    "noligatures": (no_ligatures, 0, 0, "Latin ligatures: æ œ → ae oe"),
    "latinize": (latinize, 0, 0, "normalize Latin: æ → ae, ā ă ë → a a e, v → u, j → i"),
    "vtou": (lambda s: s.replace("v", "u").replace("V", "U"), 0, 0, "Latin orthography: v → u"),
    "jtoi": (lambda s: s.replace("j", "i").replace("J", "I"), 0, 0, "Latin orthography: j → i"),
    "latex": (lambda s: "".join(LATEX_ESCAPES.get(c, c) for c in s), 0, 0,
              "escape LaTeX special characters: & % $ # _ { } ~ ^ \\"),
    "quotes": (quotes_filter, 2, 8, "convert quote styles: quotes(from, to)"),
    "smartquotes": (smart_quotes, 0, 0, "straight → typographic quotes and apostrophes"),
    "straightquotes": (straight_quotes, 0, 0, "typographic quotes and guillemets → straight"),
    "stats": (text_stats, 0, 0, "word, character and line counts"),
    "calc": (calc, 0, 0, "calculate: 2*(3+4) → 14; round(x, n), abs(x)"),
    "datefmt": (date_format, 1, 2, "reformat an ISO date: datefmt(%A %d de %B, es)"),
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


def notify_copied(abbr: dict, text: str, verb: str = "Copied") -> None:
    body = one_line(text, 300) if text.strip() else abbr.get("description", "")
    notify(f"{verb} “{abbr['key']}”", body, abbr.get("icon", "edit-paste"))


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


_LOCALE_LOCK = threading.Lock()  # setlocale is process-wide; lazy abbreviations run in threads


def strftime_in(dt: datetime, fmt: str, lang: str | None) -> str:
    """dt.strftime(fmt) with day and month names in lang (es, es_CO, de…), if given."""
    if not lang:
        return dt.strftime(fmt)
    base = locale.normalize(lang).split(".")[0]
    with _LOCALE_LOCK:
        old = locale.setlocale(locale.LC_TIME)
        try:
            for name in (f"{base}.UTF-8", f"{base}.utf8", lang):
                try:
                    locale.setlocale(locale.LC_TIME, name)
                    break
                except locale.Error:
                    continue
            else:
                raise ValueError(f"locale “{lang}” is not installed")
            return dt.strftime(fmt)
        finally:
            locale.setlocale(locale.LC_TIME, old)


DATE_DEFAULTS = {
    "date": "%Y-%m-%d",
    "time": "%H:%M",
    "now": "%Y-%m-%dT%H:%M:%S",
}


# {ask:Label=default} one line, {asktext:Label} multi-line, {choose:Label=a, b, c} a list,
# {opt:Label}…{/opt} a checkbox that keeps or drops the text between the tags,
# {askdate:Label} a date picker giving YYYY-MM-DD (reformat it with |datefmt)
ASK_NAMES = ("ask", "asktext", "choose", "opt", "askdate")
OPT_RE = re.compile(r"\{opt:([^{}]*)\}(.*?)\{/opt\}", re.S)
YES = ("yes", "y", "true", "on", "1", "sí", "si")
CURSOR_MARK = "\ue000"  # where {cursor} was; removed before copying
MAX_INCLUDE_DEPTH = 8
FORM_APP = Path(__file__).resolve().with_name("krunner_abbr_form.py")
# every abbreviation's template by lowercase key, for {abbr:key}; kept current by Config
ABBR_TEXTS: dict[str, str] = {}


ARG_TOKEN_RE = re.compile(r'\s*(?:"([^"]*)"|(\S+))')


def split_args(arg: str, n: int) -> list[str]:
    """arg in n parts for {arg1}…{argN}: words, or "quoted text"; the last takes the rest."""
    parts, pos = [], 0
    for _ in range(n - 1):
        m = ARG_TOKEN_RE.match(arg, pos)
        if not m:
            break
        parts.append(m[1] if m[1] is not None else m[2])
        pos = m.end()
    rest = arg[pos:].strip()
    if rest:
        quoted = len(rest) > 1 and rest[0] == rest[-1] == '"' and '"' not in rest[1:-1]
        parts.append(rest[1:-1] if quoted else rest)
    return parts + [""] * (n - len(parts))


def arg_count(template: str) -> int:
    """The highest N of the {argN} placeholders in template (and what it includes)."""
    return max((int(m["name"][3:]) for m in PLACEHOLDER_RE.finditer(with_includes(template))
                if re.fullmatch(r"arg\d+", m["name"])), default=0)


def placeholder_spec(m: re.Match) -> str | None:
    rest = m["rest"] or ""
    return split_spec(rest[1:])[0] if rest.startswith(":") else None


def with_includes(template: str, depth: int = 0) -> str:
    """template followed by the templates it includes with {abbr:key}, for scanning."""
    parts = [template]
    if depth < MAX_INCLUDE_DEPTH:
        for m in PLACEHOLDER_RE.finditer(template):
            if m["name"] == "abbr" and (spec := placeholder_spec(m)):
                included = ABBR_TEXTS.get(spec.strip().lower())
                if included is not None:
                    parts.append(with_includes(included, depth + 1))
    return "\n".join(parts)


def split_cursor(text: str) -> tuple[str, int]:
    """Text without {cursor} marks, and how many characters follow the first one."""
    i = text.find(CURSOR_MARK)
    clean = text.replace(CURSOR_MARK, "")
    return clean, (0 if i < 0 else len(text[i:].replace(CURSOR_MARK, "")))


def parse_ask(spec: str | None) -> tuple[str, str]:
    label, _, default = (spec or "").partition("=")
    return label.strip() or "Input", default


def ask_prompts(template: str) -> list[tuple[str, str, str]]:
    """(name, label, default) for each prompt in template, in order; repeats ask once."""
    prompts, seen = [], set()
    for m in PLACEHOLDER_RE.finditer(with_includes(template)):
        if m["name"] not in ASK_NAMES:
            continue
        label, default = parse_ask(placeholder_spec(m))
        if (m["name"], label) not in seen:
            seen.add((m["name"], label))
            prompts.append((m["name"], label, default))
    return prompts


def needs_input(template: str) -> bool:
    return any(m["name"] in ASK_NAMES for m in PLACEHOLDER_RE.finditer(with_includes(template)))


def choices(default: str) -> list[str]:
    """The options of {choose:Label=a, b, "c, d"}; quote one to keep a comma."""
    options = [o for o in parse_filter_args(default) if o]
    if not options:
        raise ValueError("needs options: {choose:Label=a, b, c} or {random:a, b, c}")
    return options


def kdialog_prompt(name: str, label: str, default: str) -> str | None:
    """Ask with a KDE dialog; None if cancelled."""
    if name == "opt":
        res = subprocess.run(["kdialog", "--title", "Abbreviations", "--yesnocancel", label])
        return {0: "1", 1: ""}.get(res.returncode)
    if name == "askdate":
        res = subprocess.run(["kdialog", "--title", "Abbreviations", "--calendar", label,
                              "--dateformat", "yyyy-MM-dd"], capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else None
    if name == "choose":
        options = choices(default)
        cmd = ["kdialog", "--title", "Abbreviations", "--combobox", label, *options,
               "--default", options[0]]
    else:
        box = "--textinputbox" if name == "asktext" else "--inputbox"
        cmd = ["kdialog", "--title", "Abbreviations", box, label, default]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.removesuffix("\n") if res.returncode == 0 else None


def terminal_prompt(name: str, label: str, default: str) -> str | None:
    """Ask on the terminal (for --expand); {asktext} reads until an empty line."""
    try:
        if name == "askdate":
            today = datetime.now().date().isoformat()
            return input(f"{label} (YYYY-MM-DD) [{today}]: ").strip() or today
        if name == "opt":
            on = default.strip().lower() in YES
            answer = input(f"{label}? [{'Y/n' if on else 'y/N'}]: ").strip().lower()
            return "1" if (answer in YES if answer else on) else ""
        if name == "choose":
            options = choices(default)
            for i, o in enumerate(options, 1):
                print(f"  {i}. {o}")
            answer = input(f"{label} [1]: ").strip() or "1"
            return options[int(answer) - 1] if answer.isdigit() and 0 < int(answer) <= len(options) \
                else answer
        if name == "asktext":
            print(f"{label} (end with an empty line):")
            lines = iter(input, "")
            return "\n".join(lines) or default
        return input(f"{label}{f' [{default}]' if default else ''}: ") or default
    except EOFError:
        return None


def form_prompt(prompts: list[tuple[str, str, str]], title: str) -> dict | None:
    """Ask everything in one Qt form; one kdialog per prompt if PySide6 is missing."""
    try:
        res = subprocess.run([sys.executable, str(FORM_APP), title], input=json.dumps(prompts),
                             capture_output=True, text=True)
        if res.returncode == 1:
            return None  # cancelled
        if res.returncode == 0:
            return {(n, l): v for (n, l, _), v in zip(prompts, json.loads(res.stdout))}
        print(f"form failed, using kdialog: {res.stderr.strip()}", file=sys.stderr)
    except (OSError, ValueError) as e:
        print(f"form failed, using kdialog: {e}", file=sys.stderr)
    answers = {}
    for name, label, default in prompts:
        value = kdialog_prompt(name, label, default)
        if value is None:
            return None
        answers[(name, label)] = value
    return answers


def collect_answers(template: str, prompt=None, title: str = "Abbreviations") -> dict | None:
    """Ask for every {ask}/{asktext}/{choose}/{opt} in template: all in one form, or one at
    a time with prompt(name, label, default). None if the user cancels."""
    prompts = ask_prompts(template)
    if prompt is None:
        return form_prompt(prompts, title) if prompts else {}
    answers = {}
    for name, label, default in prompts:
        value = prompt(name, label, default)
        if value is None:
            return None
        answers[(name, label)] = value
    return answers


def expand(template: str, arg: str = "", timeout: float = CMD_TIMEOUT,
           clipboard: str | None = None, answers: dict | None = None,
           keep_cursor: bool = False, _depth: int = 0, _args: list[str] | None = None) -> str:
    """Expand all placeholders in template. Raises ValueError on bad input.

    Pass clipboard to use a pre-read value instead of querying the clipboard, and
    answers (from collect_answers) to fill {ask}, {asktext}, {choose} and {opt} prompts.
    With keep_cursor, {cursor} leaves CURSOR_MARK in the result (see split_cursor).
    """
    now = datetime.now().astimezone()

    def optional(m: re.Match) -> str:
        key = ("opt", parse_ask(m[1])[0])
        if answers is None or key not in answers:
            raise ValueError(f"{{opt:{m[1]}}} asks when Enter is pressed")
        return m[2] if answers[key] else ""

    template = OPT_RE.sub(optional, template)
    if _args is None:
        _args = split_args(arg, arg_count(template))

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
            return strftime_in(apply_offsets(now, offsets), spec or DATE_DEFAULTS[name], m["lang"])
        if offsets or m["lang"]:
            raise ValueError(f"offsets and @language only apply to date/time/now: {m[0]}")
        if name == "arg":
            return arg or (spec or "")
        if re.fullmatch(r"arg\d+", name):
            n = int(name[3:])
            return (_args[n - 1] if 0 < n <= len(_args) else "") or (spec or "")
        if name == "selection":
            return get_selection()
        if name == "random":
            return random.choice(choices(spec or ""))
        if name == "calc":
            return calc(spec or "")
        if name == "uuid":
            return str(uuid.uuid4())
        if name == "cursor":
            return CURSOR_MARK
        if name == "opt":
            raise ValueError(f"{m[0]} needs a closing {{/opt}}")
        if name == "abbr":
            included = ABBR_TEXTS.get((spec or "").strip().lower())
            if included is None:
                raise ValueError(f"{m[0]}: no abbreviation “{spec}”")
            if _depth >= MAX_INCLUDE_DEPTH:
                raise ValueError(f"{m[0]}: abbreviations include each other in a loop")
            return expand(included, arg, timeout, clipboard, answers, True, _depth + 1, _args)
        if name == "env":
            return os.environ.get(spec or "", "")
        if name == "clipboard":
            if not spec:
                return clip()
            if not spec.isdigit() or int(spec) < 1:
                raise ValueError(f"{m[0]}: give a history position, 1 = current, 2 = previous…")
            return get_clipboard_entry(int(spec))
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
    text = "".join(out)
    return text if keep_cursor or _depth else text.replace(CURSOR_MARK, "")


def is_lazy(abbr: dict) -> bool:
    """Computed only when Enter is pressed: marked lazy, or prompts for input."""
    return bool(abbr.get("lazy")) or needs_input(abbr["text"])


def uses_arg(template: str) -> bool:
    return any(m["name"] in ("cmd", "input") or re.fullmatch(r"arg\d*", m["name"])
               for m in PLACEHOLDER_RE.finditer(with_includes(template)))


# ---------------------------------------------------------------- config


def as_list(value) -> list[str]:
    """A TOML list, or a string of words separated by spaces or commas."""
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [w for w in re.split(r"[\s,]+", str(value or "")) if w]


STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "krunner-abbr"
USAGE_FILE = STATE_DIR / "usage.json"
USAGE_HALF_LIFE = 30  # days for a use to count half as much


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

    def record(self, key: str) -> None:
        entry = self.load().setdefault(key.lower(), {"count": 0, "last": 0})
        entry["count"] += 1
        entry["last"] = time.time()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))
            self.mtime = self.path.stat().st_mtime
        except OSError as e:
            print(f"cannot save usage: {e}", file=sys.stderr)

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
    if rest in BUILTIN_ALIASES:
        return [(BUILTIN_ALIASES[rest], HIGHEST)]
    matches = []
    for word in BUILTINS:
        if rest == word:
            matches.append((word, HIGHEST))
        elif not rest and cfg.prefix:
            matches.append((word, LOW))  # listed along with all abbreviations
        elif cfg.min_prefix_chars and len(rest) >= cfg.min_prefix_chars and word.startswith(rest):
            matches.append((word, LOW))
    return matches


def word_query(cfg: Config, query: str, word: str) -> str | None:
    """The text after "<keyword> word", or None if query doesn't start with them."""
    rest = strip_trigger(query.strip(), cfg.prefix or DEFAULT_PREFIX)
    if rest is None:
        return None
    first, _, text = rest.partition(" ")
    return text.strip() if first.lower() == word else None


def case_query(cfg: Config, query: str) -> str | None:
    """The text after "<keyword> case", or None if query isn't a case-picker query."""
    return word_query(cfg, query, CASE_WORD)


def chain_query(cfg: Config, query: str) -> tuple[str, str] | None:
    """(chain, text) for "<keyword> |f1|f2(a, b) text", or None if query isn't one."""
    rest = strip_trigger(query.strip(), cfg.prefix or DEFAULT_PREFIX)
    if not rest or not rest.startswith("|"):
        return None
    depth, quoted = 0, False
    for i, c in enumerate(rest):  # the chain ends at the first space outside ( ) and quotes
        if c == '"':
            quoted = not quoted
        elif not quoted and c == "(":
            depth += 1
        elif not quoted and c == ")":
            depth -= 1
        elif c.isspace() and not quoted and depth <= 0:
            return rest[:i], rest[i:].strip()
    return rest, ""


def complete_chain(chain: str) -> list[str]:
    """chain itself if it parses, else the chains its last, partly typed filter could become.

    Raises ValueError if neither works.
    """
    try:
        parse_filter_chain(chain)
        return [chain]
    except ValueError as e:
        error = e
    m = re.search(r"\|([a-z][a-z0-9_]*)?$", chain)
    if m:
        head, partial = chain[:m.start()], m[1] or ""
        try:
            parse_filter_chain(head)
        except ValueError:
            raise error from None
        names = [n for n, (_, lo, _, _) in FILTERS.items() if n.startswith(partial) and lo == 0]
        if names:
            return [f"{head}|{n}" for n in names]
    raise error


def run_chain(chain: str, text: str) -> str:
    return apply_filters(text, parse_filter_chain(chain), LAZY_TIMEOUT)


def case_variants(text: str) -> list[tuple[str, str]]:
    """(filter, result) for each case style of text, dropping duplicate results."""
    seen, out = set(), []
    for name in CASE_FILTERS:
        result = FILTERS[name][0](text)
        if result.strip() and result not in seen:
            seen.add(result)
            out.append((name, result))
    return out


def run_builtin(cfg: Config, word: str) -> None:
    if word == SETTINGS_WORD:
        open_settings()
        return
    import krunner_abbr_help

    try:
        path = krunner_abbr_help.write_page(word, cfg)
    except Exception as e:
        notify(f"Could not build the {word} page", str(e), "dialog-error")
        return
    subprocess.Popen(["xdg-open", str(path)], start_new_session=True)


def open_settings(key: str = "") -> None:
    """Open the settings dialog, on abbreviation key if given."""
    subprocess.Popen([sys.executable, str(SETTINGS_APP), *([key] if key else [])],
                     start_new_session=True)


def clipboard_matches(abbr: dict, clipboard: str) -> bool:
    """Whether abbr's `when` regex matches the clipboard."""
    try:
        return bool(abbr.get("when")) and re.search(abbr["when"], clipboard) is not None
    except re.error:
        return False


def find_matches(cfg: Config, query: str) -> list[tuple[dict, str, int]]:
    """Return (abbr, arg, category) for every abbreviation matching query."""
    query = query.strip()
    if cfg.prefix:
        query = strip_trigger(query, cfg.prefix)
        if query is None:
            return []
        if not query:  # just the keyword: list everything, clipboard suggestions first
            clipboard = get_clipboard() if any(a.get("when") for a in cfg.abbrs.values()) else ""
            return [(abbr, "", HIGH if clipboard_matches(abbr, clipboard) else LOW)
                    for _, abbr in sorted(cfg.abbrs.items())]
    if not query:
        return []
    key, _, arg = query.partition(" ")
    key, arg = key.lower(), arg.strip()

    matches = []
    exact = cfg.abbrs.get(key) or cfg.abbrs.get(cfg.aliases.get(key, ""))
    if exact and (not arg or uses_arg(exact["text"])):
        matches.append((exact, arg, HIGHEST))
    if not arg and cfg.min_prefix_chars and len(key) >= cfg.min_prefix_chars:
        for k, abbr in sorted(cfg.abbrs.items()):
            names = [k] + [a.lower() for a in as_list(abbr.get("aliases"))]
            if abbr is not exact and any(n.startswith(key) for n in names):
                matches.append((abbr, "", LOW))
        # then words in descriptions and keywords: "ex fecha" finds isod; 3+ letters
        want = deaccent(key)
        if len(want) >= max(3, cfg.min_prefix_chars):
            word = re.compile(rf"\b{re.escape(want)}", re.I)
            for k, abbr in sorted(cfg.abbrs.items()):
                about = f"{abbr.get('description', '')} {' '.join(as_list(abbr.get('keywords')))}"
                listed = any(a is abbr for a, _, _ in matches)
                if not listed and word.search(deaccent(about)):
                    matches.append((abbr, "", LOWEST))
    return matches


def one_line(text: str, limit: int) -> str:
    text = text.replace("\n", " ⏎ ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- D-Bus service


def typed_key(cfg: Config, query: str) -> str:
    """The abbreviation as typed (with its case), without the keyword."""
    rest = strip_trigger(query.strip(), cfg.prefix) if cfg.prefix else query.strip()
    return (rest or "").split(" ", 1)[0]


def case_style(typed: str) -> str:
    """"upper" for SIG, "title" for Sig, "" for sig."""
    letters = [c for c in typed if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return "upper"
    return "title" if letters and letters[0].isupper() else ""


def apply_case(text: str, style: str) -> str:
    return text.upper() if style == "upper" else _cap(text) if style == "title" else text


def open_url(target: str) -> None:
    subprocess.Popen(["xdg-open", target.strip()], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def case_icon(name: str) -> str:
    return "format-text-capitalize" if CASE_FILTERS.index(name) < 7 else "format-text-code"


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


def run_service() -> None:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    ensure_config()
    cfg = Config(CONFIG_FILE)
    usage = Usage()

    def actions(*ids: str):
        """Match property listing which ACTIONS buttons a match shows (none if empty).

        "paste" stands for whichever of paste / copy Enter doesn't already do.
        """
        alt = "copy" if cfg.enter_pastes else "paste"
        return dbus.Array([alt if i == "paste" else i for i in ids], signature="s")

    def abbr_actions():
        return actions("paste", "template", "edit")

    class Runner(dbus.service.Object):
        def __init__(self):
            DBusGMainLoop(set_as_default=True)
            # the Object keeps the BusName alive; storing it ourselves as self._name would be
            # clobbered by Object.__init__, releasing the name immediately
            super().__init__(dbus.service.BusName(SERVICE, dbus.SessionBus()), OBJPATH)
            # expansions shown in the last query, so Run copies exactly what was displayed
            self.shown: dict[str, str] = {}
            self.window_callback = None  # waiting for ReportWindow

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
                    {"subtext": subtext, "category": "Abbreviations", "actions": actions()},
                ))
            case_text = case_query(cfg, query)
            if case_text is not None:
                results.extend(self.case_results(case_text))
            chain = chain_query(cfg, query)
            if chain is not None:
                results.extend(self.chain_results(*chain))
            clip_search = word_query(cfg, query, CLIP_WORD)
            if clip_search is not None:
                results.extend(self.clip_results(clip_search))
            matches = find_matches(cfg, query)
            scores = {a["key"]: usage.score(a["key"]) if cfg.rank_by_use else 0.0
                      for a, _, _ in matches}
            top = max(scores.values(), default=0.0) or 1.0
            typed = typed_key(cfg, query)
            for abbr, arg, category in matches:
                style = case_style(typed) if abbr.get("propagate_case") and category == HIGHEST else ""
                match_id = f"{abbr['key']}\x1f{arg}\x1f{style}"
                # within a category, used more and more recently ranks higher
                relevance = {HIGHEST: 1.0, HIGH: 0.8, LOW: 0.5}.get(category, 0.3) \
                    + 0.09 * scores[abbr["key"]] / top * (category != HIGHEST)
                opens = abbr.get("action") == "open"
                buttons = actions("template", "edit") if opens else abbr_actions()
                group = abbr.get("group") or "Abbreviations"
                note = "matches the clipboard · " if category == HIGH else ""
                if is_lazy(abbr):  # computed in Run: slow commands, or prompts for input
                    label = abbr.get("description") or one_line(abbr["text"], 200)
                    hint = ("press Enter, then fill in the prompt" if needs_input(abbr["text"])
                            else "press Enter to open" if opens else "press Enter to run and copy")
                    results.append((
                        match_id, f"{label}: {arg}" if arg else label,
                        abbr.get("icon", "system-run"), category, relevance,
                        {"subtext": f"{note}{abbr['key']} · {hint}", "category": group,
                         "actions": buttons},
                    ))
                    continue
                try:
                    text = apply_case(expand(abbr["text"], arg, cfg.cmd_timeout, keep_cursor=True),
                                      style)
                except Exception as e:
                    results.append((
                        match_id, f"{abbr['key']}: {e}", "dialog-error", category, 0.1,
                        {"subtext": "abbreviation error", "actions": actions("edit")},
                    ))
                    continue
                self.shown[match_id] = text
                about = abbr.get("description") or ("open" if opens else "copy to clipboard")
                subtext = f"{note}{abbr['key']} · {about}" + (" · Enter opens it" if opens else "")
                results.append((
                    match_id, one_line(split_cursor(text)[0], 200),
                    abbr.get("icon", "internet-web-browser" if opens else "edit-paste"), category,
                    relevance, {"subtext": subtext, "category": group, "actions": buttons},
                ))
            return results

        def case_results(self, text: str) -> list:
            """One match per case style of text, or of the clipboard if text is empty."""
            source = text or get_clipboard()
            if not source.strip():
                return [(CASE_ID, "Type some text after “case”, or copy some first",
                         "format-text-capitalize", HIGHEST, 1.0,
                         {"subtext": "every case style of the text", "category": "Change case",
                          "actions": actions()})]
            results = []
            for i, (name, result) in enumerate(case_variants(source)):
                match_id = f"{CASE_ID}{name}\x1f{text}"
                self.shown[match_id] = result
                subtext = f"{name} · {FILTERS[name][3]}" + ("" if text else " · from clipboard")
                results.append((
                    match_id, one_line(result, 200), case_icon(name), HIGHEST,
                    1.0 - i * 0.01,  # keep CASE_FILTERS order
                    {"subtext": subtext, "category": "Change case", "actions": actions("paste")},
                ))
            return results

        def chain_results(self, chain: str, text: str) -> list:
            """The typed filter chain applied to text or the clipboard, or its completions."""
            def hint(title: str, icon: str = FILTER_ICON) -> list:
                return [(FILTER_ID, title, icon, HIGHEST, 1.0,
                         {"subtext": "|filter|filter(args) [text]; without text, the clipboard",
                          "category": "Filters", "actions": actions()})]

            try:
                chains = complete_chain(chain)
            except ValueError as e:
                return hint(str(e), "dialog-error")
            source = text or get_clipboard()
            if not source.strip():
                return hint("Type some text after the filters, or copy some first")
            results = []
            for i, full in enumerate(chains):
                match_id = f"{FILTER_ID}{full}\x1f{text}"
                last = FILTERS[parse_filter_chain(full)[-1][0]][3]
                subtext = f"{full} · {last}" + ("" if text else " · from clipboard")
                if "|sh" in full:  # never run shell commands while typing
                    title, icon = "Press Enter to run the shell command", "system-run"
                else:
                    try:
                        result = run_chain(full, source)
                    except Exception as e:
                        title, icon = str(e) or type(e).__name__, "dialog-error"
                        results.append((FILTER_ID, title, icon, HIGHEST, 0.9 - i * 0.001,
                                        {"subtext": subtext, "category": "Filters",
                                         "actions": actions()}))
                        continue
                    self.shown[match_id] = result
                    title, icon = one_line(result, 200) or "(empty)", FILTER_ICON
                results.append((
                    match_id, title, icon, HIGHEST, 1.0 - i * 0.001,  # keep FILTERS order
                    {"subtext": subtext, "category": "Filters", "actions": actions("paste")},
                ))
            return results

        def clip_results(self, search: str) -> list:
            """Klipper's history, filtered by search (ignoring case and accents)."""
            want = deaccent(search).lower()
            results = []
            for pos, text in enumerate(get_clipboard_history(), 1):
                if not text.strip() or want not in deaccent(text).lower():
                    continue
                match_id = f"{CLIP_ID}{pos}"
                self.shown[match_id] = text
                lines = text.count("\n") + 1
                subtext = "current clipboard" if pos == 1 else f"#{pos} in the history"
                if lines > 1:
                    subtext += f" · {lines} lines"
                results.append((
                    match_id, one_line(text, 200), "klipper", HIGHEST, 1.0 - pos * 0.001,
                    {"subtext": subtext, "category": "Clipboard history",
                     "actions": actions("paste")},
                ))
                if len(results) == CLIP_MAX:
                    break
            if not results:
                results.append((CLIP_ID, "No clipboard history matches" if search
                                else "The clipboard history is empty", "klipper", HIGHEST, 1.0,
                                {"subtext": "clip [search]", "category": "Clipboard history",
                                 "actions": actions()}))
            return results

        @dbus.service.method(IFACE, out_signature="a(sss)")
        def Actions(self):
            return [(action_id, text, icon) for action_id, (text, icon) in ACTIONS.items()]

        @dbus.service.method(IFACE, in_signature="ss")
        def Run(self, match_id: str, action_id: str):
            if match_id.startswith(BUILTIN_ID):
                run_builtin(cfg, match_id[len(BUILTIN_ID):])
                return
            # Enter (no action) pastes or copies per the setting; the buttons do the other
            paste = action_id == "paste" or (not action_id and cfg.enter_pastes)
            if match_id.startswith(CASE_ID):
                name, sep, typed = match_id[len(CASE_ID):].partition("\x1f")
                if not sep or name not in FILTERS:
                    return  # the "copy some text first" hint
                text = self.shown.get(match_id)
                if text is None:  # stale id: recompute
                    text = FILTERS[name][0](typed or get_clipboard())
                self.deliver({"key": f"{CASE_WORD} {name}", "icon": case_icon(name)}, text, paste)
                return
            if match_id.startswith(CLIP_ID):
                text = self.shown.get(match_id)
                if text is not None:  # copying makes it the current clipboard entry again
                    self.deliver({"key": "clipboard history", "icon": "klipper"}, text, paste)
                return
            if match_id.startswith(FILTER_ID):
                chain, sep, typed = match_id[len(FILTER_ID):].partition("\x1f")
                if not sep:
                    return  # a hint or an error
                text = self.shown.get(match_id)
                if text is None:  # shell commands, or a stale id
                    self.run_chain_lazy(chain, typed or get_clipboard(), paste)
                    return
                self.deliver({"key": chain, "icon": FILTER_ICON}, text, paste)
                return
            key, arg, style = (match_id.split("\x1f", 2) + ["", ""])[:3]
            abbr = cfg.abbrs.get(key.lower())
            if action_id == "edit":
                open_settings(abbr["key"] if abbr else key)
                return
            if action_id == "template" and abbr is not None:
                set_clipboard(abbr["text"])
                if cfg.notify:
                    notify(f"Copied the template of “{abbr['key']}”", one_line(abbr["text"], 300),
                           abbr.get("icon", "edit-copy"))
                return
            text = self.shown.get(match_id)
            if abbr is None and text is None:
                return
            if abbr is not None and is_lazy(abbr):
                self.run_lazy(abbr, arg, paste, style)
                return
            if text is None:  # stale id: recompute
                try:
                    text = apply_case(expand(abbr["text"], arg, cfg.cmd_timeout, keep_cursor=True),
                                      style)
                except Exception as e:
                    notify(f"“{abbr['key']}” failed", str(e), "dialog-error")
                    return
            self.deliver(abbr or {"key": key}, text, paste)

        def deliver(self, abbr: dict, text: str, paste: bool, always_notify: bool = False):
            """Copy text, then paste it into the previous window if asked.

            Formatted abbreviations are copied as HTML, except into terminals. Pasting
            uses the keys configured for the window that gets the focus back, then moves
            the cursor to {cursor}.
            """
            text, back = split_cursor(text)
            if "text" in abbr:  # a configured abbreviation, not a case/filter/history result
                usage.record(abbr["key"])
            if abbr.get("action") == "open":
                open_url(text)
                return
            fmt = abbr.get("format", "")
            html_text = markdown_to_html(text) if fmt == "markdown" else \
                text if fmt == "html" else None
            if not paste:
                set_clipboard(text, html_text)
                if cfg.notify or always_notify:
                    notify_copied(abbr, text)
                return

            def with_window(apps: list[str]):
                keys = next((cfg.paste_keys[a] for a in apps if a in cfg.paste_keys), "ctrl+v")
                rich = html_text is not None and keys == "ctrl+v"  # terminals get plain text
                set_clipboard(text, html_text if rich else None)
                threading.Thread(target=work, args=(keys, 0 if rich else back),
                                 daemon=True).start()

            def work(keys: str, back: int):
                time.sleep(0.05)  # let the clipboard owner settle
                try:
                    paste_clipboard(keys, back)
                    error = None
                except Exception as e:
                    error = e
                GLib.idle_add(done, error)

            def done(error):
                if error is not None:
                    notify("Copied, but could not paste", str(error), "dialog-warning")
                elif cfg.notify or always_notify:
                    notify_copied(abbr, text, "Pasted")
                return False

            # wait for KRunner to close, so the previous window has the focus again
            GLib.timeout_add(int(PASTE_DELAY * 1000), lambda: self.active_window(with_window))

        def active_window(self, callback) -> bool:
            """Ask KWin for the focused window; callback gets its lowercase app ids."""
            def finish(apps: list[str]) -> bool:
                if self.window_callback is callback:
                    self.window_callback = None
                    callback(apps)
                return False

            self.window_callback = callback
            try:
                WINDOW_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
                WINDOW_SCRIPT.write_text(WINDOW_JS)
                scripting = dbus.Interface(dbus.SessionBus().get_object("org.kde.KWin", "/Scripting"),
                                           "org.kde.kwin.Scripting")
                scripting.unloadScript(WINDOW_SCRIPT_NAME)
                script_id = scripting.loadScript(str(WINDOW_SCRIPT), WINDOW_SCRIPT_NAME)
                dbus.Interface(dbus.SessionBus().get_object("org.kde.KWin", f"/Scripting/Script{script_id}"),
                               "org.kde.kwin.Script").run()
                GLib.timeout_add(500, finish, [])  # no answer: paste with Ctrl+V
            except Exception as e:  # not KWin: paste with Ctrl+V
                print(f"active window unknown: {e}", file=sys.stderr)
                finish([])
            return False

        @dbus.service.method(SERVICE, in_signature="s")
        def ReportWindow(self, ids: str):
            """Called back by WINDOW_JS with "resourceClass|desktopFileName"."""
            try:
                dbus.Interface(dbus.SessionBus().get_object("org.kde.KWin", "/Scripting"),
                               "org.kde.kwin.Scripting").unloadScript(WINDOW_SCRIPT_NAME)
            except Exception:
                pass
            if self.window_callback is not None:
                callback, self.window_callback = self.window_callback, None
                callback([i.lower() for i in str(ids).split("|") if i])

        def run_chain_lazy(self, chain: str, source: str, paste: bool):
            """Apply chain in a thread, so shell commands don't block KRunner's queries."""
            def work():
                try:
                    text, error = run_chain(chain, source), None
                except Exception as e:
                    text, error = None, e
                GLib.idle_add(finish, text, error)

            def finish(text, error):
                if error is not None:
                    notify(f"“{chain}” failed", str(error), "dialog-error")
                else:  # always notify: the result wasn't shown in KRunner
                    self.deliver({"key": chain, "icon": FILTER_ICON}, text, paste,
                                 always_notify=True)
                return False

            threading.Thread(target=work, daemon=True).start()

        def run_lazy(self, abbr: dict, arg: str, paste: bool = False, style: str = ""):
            """Expand in a thread so slow commands don't block KRunner's queries."""
            clipboard = get_clipboard()  # D-Bus calls stay on the main thread

            def work():
                try:
                    answers = collect_answers(abbr["text"],
                                              title=f"Abbreviations · {abbr['key']}")
                    if answers is None:
                        return  # prompt cancelled: copy nothing
                    text, error = apply_case(expand(abbr["text"], arg, LAZY_TIMEOUT, clipboard,
                                                    answers, keep_cursor=True), style), None
                except Exception as e:
                    text, error = None, e
                GLib.idle_add(finish, text, error)

            def finish(text, error):
                if error is not None:
                    notify(f"“{abbr['key']}” failed", str(error), "dialog-error")
                elif not text.strip():
                    notify(f"“{abbr['key']}” produced no output", "Nothing was copied.",
                           "dialog-warning")
                else:  # always notify: the result wasn't shown in KRunner
                    self.deliver(abbr, text, paste, always_notify=True)
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
    ap.add_argument("--import-espanso", metavar="FILE",
                    help="convert an espanso match file and print the abbreviations")
    ap.add_argument("--write", action="store_true",
                    help="with --import-espanso: append them to your config instead")
    args = ap.parse_args()

    if args.import_espanso:
        import krunner_abbr_import

        krunner_abbr_import.main(args.import_espanso, args.write)
        return

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
