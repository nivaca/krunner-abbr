# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Templates: expanding {placeholders}, and asking for {ask}-style input."""

import calendar
import json
import os
import random
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from krunner_abbr_desktop import get_clipboard, get_clipboard_entry, get_selection
from krunner_abbr_filters import (apply_filters, calc, parse_filter_args, parse_filter_chain,
                                  split_spec, strftime_in)


CMD_TIMEOUT = 2.0  # default seconds for {cmd:...} placeholders

# {name}, {name+1d-2h}, {name:spec}, {name+1w:spec}, {name@es:spec}, each optionally
# followed by |filters
PLACEHOLDER_RE = re.compile(
    r"\{(?P<name>[a-z_]+\d*)(?P<offsets>(?:[+-]\d+(?:mo|[smhdwMy]))*)(?:@(?P<lang>[A-Za-z_]+))?"
    r"(?P<rest>[:|][^{}]*)?\}"
)
OFFSET_RE = re.compile(r"([+-])(\d+)(mo|[smhdwMy])")


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
