"""HTML pages behind "<keyword> help" and "<keyword> list".

Both pages are generated from the code and the current configuration each time they
are opened, so they always match what the runner actually does.
"""

import os
import re
from datetime import datetime
from html import escape
from pathlib import Path

import krunner_abbr as k

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "krunner-abbr"

LIST_RESULT_MAX = 300  # characters of each expansion shown on the list page
SAMPLE_TEXT = "Hello World"  # typed text for the list page's "with text" examples

# filter -> (input, chain) for the help page's examples; other filters without required
# arguments are shown on FILTER_SAMPLE
FILTER_SAMPLE = "the name of the rose"
IDENT_SAMPLE = "parseHTTP response"
FILTER_EXAMPLES = {
    "titulo": ("El Nombre De La Rosa", "|titulo"),
    "swapcase": ("Hello World", "|swapcase"),
    **{n: (IDENT_SAMPLE, f"|{n}") for n in (
        "snake", "kebab", "constant", "dot", "camel", "pascal", "train", "ada", "cobol", "path",
        "flat", "space", "start")},
    "join": ("first line\nsecond line", "|join"),
    "unhyphen": ("hyphen-\nated words", "|unhyphen"),
    "squeeze": ("too    many   spaces", "|squeeze"),
    "strip": ("  padded  ", "|strip"),
    "replace": ("a b c", '|replace(" ", "_")'),
    "sub": ("call 555-1234", r'|sub("\d", "#")'),
    "deaccent": ("café crème", "|deaccent"),
    "slug": ("Café: the Menu!", "|slug"),
    "sort": ("pear\napple\nitem 10\nitem 2", "|sort"),
    "sortitems": ("pear, apple, fig", '|sortitems(", ")'),
    "uniq": ("a\nb\na", "|uniq"),
    "reverse": ("one\ntwo\nthree", "|reverse"),
    "shuffle": ("one\ntwo\nthree", "|shuffle"),
    "number": ("first\nsecond", "|number"),
    "urlencode": ("a b&c", "|urlencode"),
    "urldecode": ("a%20b%26c", "|urldecode"),
    "base64": ("hello", "|base64"),
    "unbase64": ("aGVsbG8=", "|unbase64"),
    "roman": ("2026", "|roman"),
    "unroman": ("MMXXVI", "|unroman"),
    "nomacrons": ("āmīcus", "|nomacrons"),
    "noligatures": ("Cæsar", "|noligatures"),
    "latinize": ("poëta Iūlius vīvit", "|latinize"),
    "vtou": ("vivit", "|vtou"),
    "jtoi": ("iam jam", "|jtoi"),
    "latex": ("50% & $5", "|latex"),
    "quotes": ("«a “b” c»", "|quotes(guillemets, double, double, single)"),
    "smartquotes": ('"it\'s"', "|smartquotes"),
    "straightquotes": ("“it’s”", "|straightquotes"),
    "sh": ("pear\napple", "|sh(sort)"),
}

# (syntax, description, template whose current expansion is shown, or None)
PLACEHOLDER_DOCS = [
    ("{date}", "Today, ISO 8601", "{date}"),
    ("{time}", "Current time", "{time}"),
    ("{now}", "ISO date and time", "{now}"),
    ("{date:%d/%m/%Y}", "Any strftime format after the colon", "{date:%d/%m/%Y}"),
    ("{date+1d}", "Offsets: s m h d w mo y, chainable as in {now+1w-2h}", "{date+1d}"),
    ("{date@es:%A %d de %B}", "Day and month names in another language (es, es_CO, de, fr…)",
     "{date@es:%A %d de %B}"),
    ("{arg}", "Text typed after the abbreviation", None),
    ("{arg:default}", "… with a default when nothing is typed", None),
    ("{arg1} {arg2}…", 'Single typed words; the last one used takes the rest. "Quote" words '
     "to keep spaces: ex cit Cicero Off. 1.3 gives Cicero, Off., 1.3", None),
    ("{selection}", "The selected (highlighted) text, without copying it", None),
    ("{clipboard}", "Current clipboard contents", None),
    ("{clipboard:2}", "An earlier clipboard entry from Klipper's history: 2 = the previous one",
     None),
    ("{input}", "Typed text if any, otherwise the clipboard", None),
    ("{ask:Label}", "Opens an input box when Enter is pressed; {ask:Label=default} pre-fills it",
     None),
    ("{asktext:Label}", "Same, with a multi-line box", None),
    ("{opt:Label}…{/opt}", "A checkbox that keeps or drops the text between the tags; "
     "{opt:Label=yes} starts ticked. All prompts of an abbreviation share one form", None),
    ("{cursor}", "Where the cursor ends up after pasting", None),
    ("{abbr:name}", "Another abbreviation's expansion", None),
    ("{choose:Label=a, b, c}", "Pick one option from a list when Enter is pressed; quote an "
     'option to keep a comma: "Dear Sir, Madam"', None),
    ("{askdate:Label}", "A date picker; gives 2026-10-28, which |datefmt(%d/%m/%Y) reformats",
     None),
    ("{random:a, b, c}", "One of the options at random", "{random:heads, tails}"),
    ("{calc:2*(3+4)}", "Arithmetic: + - * / // % ** ( ), round(x, n), abs(x); also as |calc",
     "{calc:2*(3+4)}"),
    ("{uuid}", "Random UUID", "{uuid}"),
    ("{env:USER}", "Environment variable", "{env:USER}"),
    ("{cmd:command}", "Output of a shell command; $ABBR_ARG and $ABBR_INPUT hold {arg} and "
     "{input}, $ABBR_ASK1, $ABBR_ASK2… the answers to prompts ($ABBR_ASK the first)",
     None),
    ("{{  }}", "Literal braces: \\emph{{{input}}} gives \\emph{text}", None),
]

CSS = """
:root {
  --bg: #fbfbfa; --surface: #ffffff; --text: #1d1d1f; --muted: #6b6b70;
  --border: #e3e3e0; --accent: #2f6fdb; --code-bg: #f1f1ee; --error: #c4302b;
  --badge-bg: #eef3fc;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #18181a; --surface: #202023; --text: #ececee; --muted: #9a9aa2;
    --border: #333338; --accent: #7aa7f0; --code-bg: #2a2a2e; --error: #f07a74;
    --badge-bg: #26303f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, "Noto Sans", sans-serif;
}
header {
  position: sticky; top: 0; z-index: 1; background: var(--bg);
  border-bottom: 1px solid var(--border);
}
.bar {
  max-width: 1040px; margin: 0 auto; padding: 14px 24px;
  display: flex; align-items: baseline; gap: 24px; flex-wrap: wrap;
}
h1 { font-size: 18px; margin: 0; font-weight: 650; }
nav { display: flex; gap: 4px; }
nav a {
  color: var(--muted); text-decoration: none; padding: 4px 10px; border-radius: 6px;
}
nav a:hover { color: var(--text); }
nav a.current { color: var(--text); background: var(--code-bg); }
main { max-width: 1040px; margin: 0 auto; padding: 8px 24px 64px; }
h2 { font-size: 16px; margin: 36px 0 10px; font-weight: 650; }
p, li { color: var(--text); }
.muted { color: var(--muted); }
code {
  font: 13px/1.4 ui-monospace, "JetBrains Mono", "Noto Sans Mono", monospace;
  background: var(--code-bg); padding: 1px 5px; border-radius: 4px;
  overflow-wrap: anywhere;
}
table {
  width: 100%; border-collapse: collapse; background: var(--surface);
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
}
th, td { text-align: left; vertical-align: top; padding: 8px 12px; }
th {
  font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase;
  letter-spacing: .04em; border-bottom: 1px solid var(--border);
}
tr + tr td { border-top: 1px solid var(--border); }
td.nowrap { white-space: nowrap; }
.result { white-space: pre-wrap; overflow-wrap: anywhere; }
.error { color: var(--error); }
.badge {
  display: inline-block; font-size: 12px; color: var(--accent); background: var(--badge-bg);
  padding: 0 7px; border-radius: 10px; margin-left: 6px; white-space: nowrap;
}
.search {
  width: 100%; max-width: 360px; padding: 7px 10px; margin: 16px 0 12px;
  font: inherit; color: var(--text); background: var(--surface);
  border: 1px solid var(--border); border-radius: 6px;
}
.search:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.scroll { overflow-x: auto; }
.toc { display: flex; gap: 6px 14px; flex-wrap: wrap; margin: 18px 0 0; font-size: 14px; }
.toc a, td a { color: var(--accent); text-decoration: none; }
.toc a:hover, td a:hover { text-decoration: underline; }
.summary { margin: 18px 0 0; }
.summary b { font-weight: 650; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 12px; }
.chips button {
  font: inherit; font-size: 13px; color: var(--muted); background: var(--surface);
  border: 1px solid var(--border); border-radius: 14px; padding: 2px 10px; cursor: pointer;
}
.chips button:hover { color: var(--text); }
.chips button.on { color: var(--accent); background: var(--badge-bg); border-color: var(--accent); }
.ph { color: var(--accent); }
.ph a { color: inherit; text-decoration: underline dotted; }
.sub { display: block; font-size: 13px; color: var(--muted); margin-top: 3px; }
.sub code { font-size: 12px; }
.badge.warn { color: var(--error); }
.rendered { margin-top: 6px; padding: 6px 10px; border-left: 3px solid var(--border); }
.rendered > :first-child { margin-top: 0; } .rendered > :last-child { margin-bottom: 0; }
td code.key { cursor: copy; }
tr:target td { background: var(--badge-bg); }
tr.group td {
  font-size: 12px; font-weight: 650; color: var(--muted); text-transform: uppercase;
  letter-spacing: .04em; background: var(--bg); padding-top: 14px;
}
.notice { border: 1px solid var(--error); border-radius: 8px; padding: 8px 12px; margin: 14px 0 0; }
@media (max-width: 640px) {
  .bar, main { padding-left: 16px; padding-right: 16px; }
  th, td { padding: 6px 8px; }
}
"""


def typed(cfg, word: str) -> str:
    """What to type in KRunner for word, with the configured keyword."""
    if not cfg.prefix:
        return word
    return cfg.prefix + (" " if cfg.prefix[-1].isalnum() else "") + word


def page(title: str, current: str, body: str) -> str:
    links = "".join(
        f'<a href="{name}.html"{" class=current" if name == current else ""}>{label}</a>'
        for name, label in (("help", "Help"), ("list", "Abbreviations"))
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header><div class="bar"><h1>KRunner Abbreviations</h1><nav>{links}</nav></div></header>
<main>
{body}
</main>
</body>
</html>
"""


def safe_expand(template: str, timeout: float, clipboard: str, arg: str = "") -> tuple[str, bool]:
    """(result, ok) for template, never raising."""
    try:
        return k.expand(template, arg, timeout, clipboard), True
    except Exception as e:
        return str(e), False


def help_page(cfg) -> str:
    c = lambda s: f"<code>{escape(s)}</code>"
    toc = """<nav class="toc"><a href="#using">Using it</a><a href="#placeholders">Placeholders</a>
<a href="#filters">Filters</a><a href="#quotes">Quote styles</a><a href="#config">Configuration file</a>
<a href="list.html">All abbreviations →</a></nav>"""
    usage = toc + f"""
<h2 id="using">Using it</h2>
<ul>
  <li>Type {c(typed(cfg, "isod"))} in KRunner and press <b>Enter</b>: the expansion is copied
      to the clipboard.</li>
  <li>Text after the abbreviation becomes its argument: {c(typed(cfg, "upper hello"))}
      copies <code>HELLO</code>. Clipboard abbreviations work on the clipboard when
      nothing is typed.</li>
  <li>{c(typed(cfg, "") if cfg.prefix else "(any abbreviation)")}
      {"on its own lists every abbreviation;" if cfg.prefix else "matches directly;"}
      after {cfg.min_prefix_chars or "—"} characters, abbreviations starting with what you
      typed are suggested too.</li>
  <li>{c(typed(cfg if cfg.prefix else _default(cfg), "settings"))} opens the settings dialog,
      {c(typed(cfg if cfg.prefix else _default(cfg), "help"))}
      (or {c(typed(cfg if cfg.prefix else _default(cfg), "?"))}) this page and
      {c(typed(cfg if cfg.prefix else _default(cfg), "list"))} the table of abbreviations.</li>
  <li>{c(typed(cfg if cfg.prefix else _default(cfg), "case Some Text"))} lists the text in every
      case style (snake_case, Train-Case, …); with no text, the clipboard.</li>
  <li>{c(typed(cfg if cfg.prefix else _default(cfg), "clip"))} lists the clipboard history
      (add text to search it); Enter copies the entry again.</li>
  <li>{c(typed(cfg if cfg.prefix else _default(cfg), "|unhyphen|join"))} applies any chain of
      the filters below to the text typed after it or the clipboard; a partly typed name
      lists the filters it could become. Chains with {c("sh(…)")} run only on Enter.</li>
  <li>Enter {"copies the expansion and pastes it into the previous window" if cfg.enter_pastes
      else "copies the expansion"}. Each match has buttons:
      <b>{"copy without pasting" if cfg.enter_pastes else "copy and paste"}</b>,
      <b>copy the template</b> unexpanded, and <b>edit</b> the abbreviation in the settings
      dialog. Pasting needs <code>ydotool</code> or <code>wtype</code> on Wayland.</li>
  <li>Abbreviations marked <span class="badge">on Enter</span> run only when you press Enter
      (slow commands, or ones that ask for input in a dialog first); a notification shows
      what was copied. Cancelling a prompt copies nothing.</li>
</ul>
<p class="muted">Trigger keyword: {c(cfg.prefix) if cfg.prefix else "none"} ·
Configuration: {c(str(k.CONFIG_FILE))}</p>
"""
    rows = []
    for syntax, desc, example in PLACEHOLDER_DOCS:
        now = ""
        if example:
            result, ok = safe_expand(example, cfg.cmd_timeout, "")
            now = f'<span class="result{"" if ok else " error"}">{escape(result)}</span>'
        rows.append(f"<tr><td class=nowrap>{c(syntax)}</td><td>{escape(desc)}</td>"
                    f"<td>{now}</td></tr>")
    placeholders = f"""
<h2 id="placeholders">Placeholders</h2>
<div class="scroll"><table>
<tr><th>Placeholder</th><th>Meaning</th><th>Right now</th></tr>
{"".join(rows)}
</table></div>
"""
    rows = []
    for name, (_, lo, hi, desc) in k.FILTERS.items():
        call = name if hi == 0 else f"{name}(…)" if lo else f"{name}, {name}(…)"
        example = FILTER_EXAMPLES.get(name) or (None if lo else (FILTER_SAMPLE, f"|{name}"))
        shown = ""
        if example:
            source, chain = example
            try:
                result, ok = k.run_chain(chain, source), True
            except Exception as e:
                result, ok = str(e), False
            shown = (f"{c(chain)}<span class=sub><span class=result>{escape(source)}</span> → "
                     f'<span class="result{"" if ok else " error"}">{escape(result)}</span></span>')
        rows.append(f"<tr id=f-{name}><td class=nowrap>{c('|' + call)}</td><td>{escape(desc)}</td>"
                    f"<td>{shown}</td></tr>")
    filters = f"""
<h2 id="filters">Filters</h2>
<p>Filters transform any placeholder and can be chained, left to right:
{c("{clipboard|unhyphen|join}")}, {c("{date:%Y|roman}")}.
Quote arguments that contain spaces or commas: {c('replace(" ", "_")')}.
Inside {c("{cmd:…}")}, write shell pipes with spaces ({c("a | sort")}) so they are not
read as filters. Placeholders cannot contain {c("{")} or {c("}")}.</p>
<div class="scroll"><table>
<tr><th>Filter</th><th>Effect</th><th>Example</th></tr>
{"".join(rows)}
</table></div>
"""
    rows = []
    for name, (opener, closer) in k.QUOTE_STYLES.items():
        rows.append(f"<tr><td class=nowrap>{c(name)}</td>"
                    f"<td class=result>{escape(opener)}text{escape(closer)}</td></tr>")
    quotes = f"""
<h2 id="quotes">Quote styles</h2>
<p>For {c("|quotes(from, to)")}. Give several pairs to convert them in one pass, which keeps
nested quotes right: {c("quotes(guillemets, double, double, single)")} turns
<span class=result>«a “b” c»</span> into <span class=result>“a ‘b’ c”</span>. A literal pair
such as {c("«»")} works too.</p>
<div class="scroll"><table>
<tr><th>Style</th><th>Example</th></tr>
{"".join(rows)}
</table></div>
"""
    config = f"""
<h2 id="config">Configuration file</h2>
<p>{c(str(k.CONFIG_FILE))} is reloaded on the next keystroke after it changes. Under
{c("[settings]")}: {c("prefix")} (trigger keyword), {c("min_prefix_chars")} (0 turns
suggestions off), {c("cmd_timeout")} (seconds), {c("notify")} and {c("enter_pastes")}
(Enter pastes as well as copies). Under {c("[abbr]")}, each abbreviation
is either {c('key = "text"')} or a table with {c("text")}, {c("description")},
{c("icon")}, {c("lazy = true")} (run on Enter, with up to
{k.LAZY_TIMEOUT:.0f}&nbsp;s for commands), {c("keywords")} (extra words that find it) and
{c('format = "markdown"')} or {c('"html"')} (copied as formatted text, plain in terminals),
{c('aliases = ["fh", "hoy"]')} (other names), {c('group = "LaTeX"')} (KRunner category and list
section), {c('action = "open"')} (Enter opens the expansion, a link, file or folder, instead of
copying it), {c('when = "regex"')} (listed first under the bare keyword while the clipboard
matches) and {c("propagate_case = true")} (Sig → Capitalized, SIG → UPPERCASE).
{c("rank_by_use")} under {c("[settings]")} ranks frequent and recent abbreviations higher
(usage is kept in {c(str(k.USAGE_FILE))}); {c('disabled_groups = ["LaTeX"]')} hides groups.
{c("[paste_keys]")} maps applications to the keys that paste in them, e.g.
{c('"org.example.app" = "shift+insert"')}; terminals already use Ctrl+Shift+V.
{c("--import-espanso FILE")} converts espanso match files.</p>
"""
    return page("Abbreviations Help", "help", usage + placeholders + filters + quotes + config)


def _default(cfg):
    """cfg stand-in with the default keyword, for built-ins when no keyword is set."""
    return type("Cfg", (), {"prefix": k.DEFAULT_PREFIX})()


PROMPT_KINDS = {"ask": "text", "asktext": "multi-line text", "choose": "choice", "opt": "checkbox",
                "askdate": "date"}
CHIPS = [  # (data-kind value, label)
    ("", "All"), ("text", "Take typed text"), ("clip", "Use the clipboard"),
    ("asks", "Ask for input"), ("lazy", "Run on Enter"), ("fmt", "Formatted"),
    ("open", "Open links"), ("when", "Clipboard suggestions"), ("shell", "Run commands"),
    ("error", "Errors"),
]


def placeholder_names(template: str) -> set[str]:
    return {m["name"] for m in k.PLACEHOLDER_RE.finditer(template)}


def includes(template: str) -> list[str]:
    """Keys of the abbreviations template includes with {abbr:key}."""
    return [spec.strip() for m in k.PLACEHOLDER_RE.finditer(template)
            if m["name"] == "abbr" and (spec := k.placeholder_spec(m))]


def highlight(template: str, cfg) -> str:
    """Escaped template with placeholders coloured, filters linked to the help page and
    {abbr:key} linked to that abbreviation's row."""
    out, pos = [], 0
    for m in k.PLACEHOLDER_RE.finditer(template):
        out.append(escape(template[pos:m.start()]))
        ph = escape(m[0])
        ph = re.sub(r"\|([a-z][a-z0-9_]*)", lambda f: f'|<a href="help.html#f-{f[1]}">{f[1]}</a>'
                    if f[1] in k.FILTERS else f[0], ph)
        if m["name"] == "abbr" and (spec := k.placeholder_spec(m)) and spec.strip().lower() in cfg.abbrs:
            ph = ph.replace(escape(spec), f'<a href="#a-{escape(spec.strip().lower())}">{escape(spec)}</a>', 1)
        out.append(f'<span class="ph">{ph}</span>')
        pos = m.end()
    out.append(escape(template[pos:]))
    return "".join(out).replace("{/opt}", '<span class="ph">{/opt}</span>')


def shorten(text: str) -> str:
    return text if len(text) <= LIST_RESULT_MAX else text[:LIST_RESULT_MAX - 1] + "…"


def list_page(cfg) -> str:
    clipboard = k.get_clipboard()
    tracker = k.Usage()
    usage = tracker.load()
    grouped = any(a.get("group") for a in cfg.abbrs.values())
    used_by: dict[str, list[str]] = {}
    for abbr in cfg.abbrs.values():
        for key in includes(abbr["text"]):
            used_by.setdefault(key.lower(), []).append(abbr["key"])
    rows = []
    counts = {kind: 0 for kind, _ in CHIPS[1:]}
    group = None
    for abbr in sorted(cfg.abbrs.values(),
                       key=lambda a: (not a.get("group"), str(a.get("group", "")).lower(), a["key"].lower())):
        if grouped and abbr.get("group", "Other") != group:
            group = abbr.get("group", "Other")
            rows.append(f'<tr class=group><td colspan=4>{escape(group)}</td></tr>')
        template = abbr["text"]
        names = placeholder_names(k.with_includes(template))
        kinds = set()
        if abbr.get("action") == "open":
            kinds.add("open")
        if abbr.get("when"):
            kinds.add("when")
        takes_text = bool(names & {"arg", "input"}) or any(re.fullmatch(r"arg\d+", n) for n in names)
        if takes_text:
            kinds.add("text")
        if names & {"clipboard", "input"}:
            kinds.add("clip")
        if "cmd" in names or "|sh(" in template:
            kinds.add("shell")
        if abbr.get("format"):
            kinds.add("fmt")
        if k.is_lazy(abbr):
            kinds.add("lazy")
        sub = []  # notes under the result
        if k.needs_input(template):
            kinds.add("asks")
            asked = ", ".join(f"{escape(label)} <span class=muted>({PROMPT_KINDS[name]}"
                              + (f": {escape(', '.join(k.choices(default)))}" if name == "choose"
                                 and default else "") + ")</span>"
                              for name, label, default in k.ask_prompts(template))
            result = f'<span class="muted">asks for</span> {asked}'
        elif abbr.get("lazy"):
            result = '<span class="muted">computed when Enter is pressed</span>'
        else:
            text, ok = safe_expand(template, cfg.cmd_timeout, clipboard)
            if ok and "open" in kinds:
                target = text.strip()
                link = (f'<a href="{escape(target)}">{escape(shorten(target))}</a>'
                        if re.match(r"https?://", target) else escape(shorten(target)))
                result = f'<span class="muted">opens</span> <span class="result">{link}</span>'
            elif ok:
                result = f'<span class="result">{escape(shorten(text))}</span>'
            elif "clip" in kinds:  # depends on what was copied, e.g. |roman on a date
                result = (f'<span class="muted">not for the current clipboard: '
                          f"{escape(shorten(text))}</span>")
            else:
                kinds.add("error")
                result = f'<span class="result error">{escape(shorten(text))}</span>'
            if ok and abbr.get("format") == "markdown":
                result += f'<div class="rendered">{k.markdown_to_html(text)}</div>'
            if takes_text:
                example, ok2 = safe_expand(template, cfg.cmd_timeout, clipboard, SAMPLE_TEXT)
                sub.append(f"with text: <code>{escape(typed(cfg, abbr['key'] + ' ' + SAMPLE_TEXT))}</code>"
                           f' → <span class="result{"" if ok2 else " error"}">{escape(shorten(example))}</span>')
        if "cursor" in names:
            sub.append("pasting leaves the cursor at <code>{cursor}</code>")
        if abbr.get("when"):
            hit = k.clipboard_matches(abbr, clipboard)
            sub.append(f"suggested first when the clipboard matches <code>{escape(abbr['when'])}</code>"
                       + (" (it does now)" if hit else ""))
        if abbr.get("propagate_case"):
            sub.append(f"matches the case typed: <code>{escape(typed(cfg, abbr['key'].capitalize()))}</code>"
                       f" → Capitalized, <code>{escape(typed(cfg, abbr['key'].upper()))}</code> → UPPERCASE")
        for kind in kinds:
            counts[kind] += 1

        badges = "".join(f'<span class="badge{" warn" if b == "error" else ""}">{label}</span>'
                         for b, label in (("lazy", "on Enter"), ("fmt", abbr.get("format", "")),
                                          ("open", "opens"), ("error", "error")) if b in kinds)
        aliases = k.as_list(abbr.get("aliases"))
        if aliases:
            badges += "<span class=sub>also " + ", ".join(
                f"<code>{escape(typed(cfg, a))}</code>" for a in aliases) + "</span>"
        use = usage.get(abbr["key"].lower(), {})
        if use:
            last = datetime.fromtimestamp(use.get("last", 0)).strftime("%Y-%m-%d")
            badges += f"<span class=sub>used {use.get('count', 0)}× · last {last}</span>"
        about = [escape(abbr.get("description", ""))]
        if abbr.get("keywords"):
            about.append(f"<span class=sub>keywords: {escape(' '.join(k.as_list(abbr['keywords'])))}</span>")
        if inc := includes(template):
            about.append("<span class=sub>includes " + ", ".join(
                f'<a href="#a-{escape(i.lower())}">{escape(i)}</a>' for i in inc) + "</span>")
        if users := used_by.get(abbr["key"].lower()):
            about.append("<span class=sub>used by " + ", ".join(
                f'<a href="#a-{escape(u.lower())}">{escape(u)}</a>' for u in users) + "</span>")
        result += "".join(f"<span class=sub>{n}</span>" for n in sub)
        search = " ".join([abbr["key"], abbr.get("description", ""), str(abbr.get("keywords", "")),
                           " ".join(aliases), str(abbr.get("group", "")), template]).lower()
        score = tracker.score(abbr["key"])
        rows.append(
            f'<tr id="a-{escape(abbr["key"].lower())}" data-kind="{" ".join(sorted(kinds))}"'
            f' data-search="{escape(search)}" data-score="{score:.4f}" data-key="{escape(abbr["key"].lower())}">'
            f'<td class=nowrap><code class=key title="Click to copy">{escape(typed(cfg, abbr["key"]))}</code>'
            f"{badges}</td><td>{''.join(about)}</td>"
            f"<td><code>{highlight(template, cfg)}</code></td><td>{result}</td></tr>"
        )

    try:
        modified = datetime.fromtimestamp(cfg.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        modified = "?"
    c = lambda s: f"<code>{escape(s)}</code>"
    summary = (f'<p class="summary"><b>{len(rows)}</b> abbreviations: '
               f"<b>{counts['text']}</b> take typed text, <b>{counts['asks']}</b> ask for input, "
               f"<b>{counts['lazy']}</b> run on Enter, <b>{counts['fmt']}</b> formatted. "
               f'<span class="muted">Keyword {c(cfg.prefix) if cfg.prefix else "none"} · '
               f"{c(str(cfg.path))}, changed {modified}</span></p>")
    if cfg.disabled:
        off = ", ".join(escape(g) for g in cfg.disabled_groups)
        summary += (f'<p class="muted">{len(cfg.disabled)} more in disabled groups ({off}), '
                    "not offered in KRunner.</p>")
    if counts["error"]:
        summary += (f'<p class="notice"><b>{counts["error"]}</b> abbreviation'
                    f'{"s" if counts["error"] > 1 else ""} currently fail to expand: '
                    '<a href="#" onclick="return chip(\'error\')">show them</a>.</p>')
    chips = "".join(f'<button type=button data-chip="{kind}"{" class=on" if not kind else ""}'
                    f' onclick="chip(\'{kind}\')">{label}'
                    f'{f" <span class=muted>{counts[kind]}</span>" if kind else ""}</button>'
                    for kind, label in CHIPS if not kind or counts[kind])
    body = f"""
{summary}
<input class="search" type="search" placeholder="Search names, descriptions, keywords, templates…"
       autofocus oninput="update()">
<div class="chips">{chips}
<button type=button id=sortbtn onclick="toggleSort()">Sort: {"by group" if grouped else "by name"}</button></div>
<div class="scroll"><table id="abbrs">
<tr><th>Type</th><th>Description</th><th>Template</th><th>Expansion now</th></tr>
{"".join(rows)}
</table></div>
<p class="muted">Clipboard-based expansions use the clipboard as it was when this page was
opened; examples use the typed text {c(SAMPLE_TEXT)}. Click an abbreviation to copy what to
type. Edit abbreviations with {c(typed(cfg if cfg.prefix else _default(cfg), "settings"))}.</p>
<script>
let kind = "", byUse = false;
const table = document.querySelector("#abbrs tbody") || document.querySelector("#abbrs");
const original = [...table.querySelectorAll("tr[data-kind], tr.group")];
function update() {{
  const q = document.querySelector(".search").value.toLowerCase();
  for (const row of document.querySelectorAll("#abbrs tr[data-kind]"))
    row.hidden = (q && !row.dataset.search.includes(q)) ||
                 (kind && !row.dataset.kind.split(" ").includes(kind));
  for (const head of document.querySelectorAll("#abbrs tr.group")) {{
    let next = head.nextElementSibling, any = false;
    for (; next && !next.classList.contains("group"); next = next.nextElementSibling)
      any ||= !next.hidden;
    head.hidden = byUse || !any;
  }}
}}
function toggleSort() {{
  byUse = !byUse;
  const rows = byUse
    ? original.filter(r => r.dataset.kind !== undefined)
        .sort((a, b) => b.dataset.score - a.dataset.score || a.dataset.key.localeCompare(b.dataset.key))
    : original;
  for (const r of rows) table.appendChild(r);
  document.querySelector("#sortbtn").textContent = byUse ? "Sort: most used" : "Sort: {"by group" if grouped else "by name"}";
  update();
}}
function chip(k) {{
  kind = k;
  for (const b of document.querySelectorAll(".chips button"))
    b.classList.toggle("on", b.dataset.chip === k);
  update();
  return false;
}}
for (const code of document.querySelectorAll("code.key"))
  code.addEventListener("click", () => {{
    navigator.clipboard?.writeText(code.textContent);
    code.title = "Copied";
  }});
</script>
"""
    return page("All Abbreviations", "list", body)


def write_page(kind: str, cfg) -> Path:
    """Write both pages (they link to each other) and return the path of `kind`."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in (("help", help_page), ("list", list_page)):
        (CACHE_DIR / f"{name}.html").write_text(build(cfg))
    return CACHE_DIR / f"{kind}.html"
