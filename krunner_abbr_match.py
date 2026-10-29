# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Matching KRunner queries: abbreviations, built-in commands, case picker, filters."""

import re
import subprocess
import sys
from pathlib import Path

from krunner_abbr_config import DEFAULT_PREFIX, Config, as_list
from krunner_abbr_desktop import get_clipboard, notify
from krunner_abbr_filters import (FILTERS, _cap, apply_filters, deaccent,
                                  parse_filter_chain)
from krunner_abbr_template import uses_arg


SETTINGS_APP = Path(__file__).resolve().with_name("krunner_abbr_settings.py")

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

# KRunner match categories (Plasma 6 QueryMatch::CategoryRelevance)
HIGHEST = 100
LOW = 30
HIGH = 70  # the keyword alone: abbreviations whose `when` matches the clipboard
LOWEST = 0  # matched by description or keywords
TYPO_MIN_CHARS = 4  # typed names this long also find names one typo away
LAZY_TIMEOUT = 20.0  # seconds for abbreviations computed only when Enter is pressed


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


def names_of(abbr: dict) -> list[str]:
    """The lowercase name of abbr and its aliases."""
    return [abbr["key"].lower()] + [a.lower() for a in as_list(abbr.get("aliases"))]


def one_edit_apart(a: str, b: str) -> bool:
    """Whether b is a with one letter changed, added or removed, or two neighbours swapped."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long = sorted((a, b), key=len)
    i = next((i for i, (x, y) in enumerate(zip(short, long)) if x != y), len(short))
    return short[i:] == long[i + 1:]


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
            if abbr is not exact and any(n.startswith(key) for n in names_of(abbr)):
                matches.append((abbr, "", LOW))
        # then names one typo away: "ex sgi" finds sig; 4+ letters
        if len(key) >= TYPO_MIN_CHARS:
            for k, abbr in sorted(cfg.abbrs.items()):
                listed = any(a is abbr for a, _, _ in matches)
                if not listed and any(one_edit_apart(key, n) for n in names_of(abbr)):
                    matches.append((abbr, "", LOWEST))
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


def typed_key(cfg: Config, query: str) -> str:
    """The abbreviation as typed (with its case), without the keyword."""
    rest = strip_trigger(query.strip(), cfg.prefix) if cfg.prefix else query.strip()
    return (rest or "").split(" ", 1)[0]


def match_reason(cfg: Config, abbr: dict, query: str) -> str:
    """Why abbr matches query when its own name doesn't explain it: an alias, a typo,
    a keyword or the description. "" otherwise."""
    key = typed_key(cfg, query).lower()
    if not key or abbr["key"].lower().startswith(key):
        return ""
    for alias in as_list(abbr.get("aliases")):
        if alias.lower().startswith(key):
            return f"via alias {alias}"
    if any(one_edit_apart(key, n) for n in names_of(abbr)):
        return f"similar to “{key}”"
    word = re.compile(rf"\b{re.escape(deaccent(key))}", re.I)
    for keyword in as_list(abbr.get("keywords")):
        if word.search(deaccent(keyword)):
            return f"keyword “{keyword}”"
    return "found in the description"


def case_style(typed: str) -> str:
    """"upper" for SIG, "title" for Sig, "" for sig."""
    letters = [c for c in typed if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return "upper"
    return "title" if letters and letters[0].isupper() else ""


def apply_case(text: str, style: str) -> str:
    return text.upper() if style == "upper" else _cap(text) if style == "title" else text


def case_icon(name: str) -> str:
    return "format-text-capitalize" if CASE_FILTERS.index(name) < 7 else "format-text-code"
