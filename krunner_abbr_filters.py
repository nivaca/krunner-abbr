# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Text filters for |filter chains: case styles, quotes, Latin, sorting, calc…"""

import ast
import base64
import csv
import html
import locale
import random
import re
import subprocess
import threading
import unicodedata
import urllib.parse
from datetime import datetime


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
    # changes nothing: in KRunner, it makes Enter paste the result (see split_paste)
    "paste": (lambda s: s, 0, 0, "paste the result into the previous window (KRunner only)"),
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


def one_line(text: str, limit: int) -> str:
    text = text.replace("\n", " ⏎ ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
