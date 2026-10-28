"""HTML pages behind "<keyword> help" and "<keyword> list".

Both pages are generated from the code and the current configuration each time they
are opened, so they always match what the runner actually does.
"""

import os
from html import escape
from pathlib import Path

import krunner_abbr as k

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "krunner-abbr"

LIST_RESULT_MAX = 300  # characters of each expansion shown on the list page

# (syntax, description, template whose current expansion is shown, or None)
PLACEHOLDER_DOCS = [
    ("{date}", "Today, ISO 8601", "{date}"),
    ("{time}", "Current time", "{time}"),
    ("{now}", "ISO date and time", "{now}"),
    ("{date:%d/%m/%Y}", "Any strftime format after the colon", "{date:%d/%m/%Y}"),
    ("{date+1d}", "Offsets: s m h d w mo y, chainable as in {now+1w-2h}", "{date+1d}"),
    ("{arg}", "Text typed after the abbreviation", None),
    ("{arg:default}", "… with a default when nothing is typed", None),
    ("{clipboard}", "Current clipboard contents", None),
    ("{input}", "Typed text if any, otherwise the clipboard", None),
    ("{ask:Label}", "Opens an input box when Enter is pressed; {ask:Label=default} pre-fills it",
     None),
    ("{asktext:Label}", "Same, with a multi-line box", None),
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


def safe_expand(template: str, timeout: float, clipboard: str) -> tuple[str, bool]:
    """(result, ok) for template, never raising."""
    try:
        return k.expand(template, "", timeout, clipboard), True
    except Exception as e:
        return str(e), False


def help_page(cfg) -> str:
    c = lambda s: f"<code>{escape(s)}</code>"
    usage = f"""
<h2>Using it</h2>
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
<h2>Placeholders</h2>
<div class="scroll"><table>
<tr><th>Placeholder</th><th>Meaning</th><th>Right now</th></tr>
{"".join(rows)}
</table></div>
"""
    rows = []
    for name, (_, lo, hi, desc) in k.FILTERS.items():
        call = name if hi == 0 else f"{name}(…)" if lo else f"{name}, {name}(…)"
        rows.append(f"<tr><td class=nowrap>{c('|' + call)}</td><td>{escape(desc)}</td></tr>")
    filters = f"""
<h2>Filters</h2>
<p>Filters transform any placeholder and can be chained, left to right:
{c("{clipboard|unhyphen|join}")}, {c("{date:%Y|roman}")}.
Quote arguments that contain spaces or commas: {c('replace(" ", "_")')}.
Inside {c("{cmd:…}")}, write shell pipes with spaces ({c("a | sort")}) so they are not
read as filters. Placeholders cannot contain {c("{")} or {c("}")}.</p>
<div class="scroll"><table>
<tr><th>Filter</th><th>Effect</th></tr>
{"".join(rows)}
</table></div>
"""
    rows = []
    for name, (opener, closer) in k.QUOTE_STYLES.items():
        rows.append(f"<tr><td class=nowrap>{c(name)}</td>"
                    f"<td class=result>{escape(opener)}text{escape(closer)}</td></tr>")
    quotes = f"""
<h2>Quote styles</h2>
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
<h2>Configuration file</h2>
<p>{c(str(k.CONFIG_FILE))} is reloaded on the next keystroke after it changes. Under
{c("[settings]")}: {c("prefix")} (trigger keyword), {c("min_prefix_chars")} (0 turns
suggestions off) and {c("cmd_timeout")} (seconds). Under {c("[abbr]")}, each abbreviation
is either {c('key = "text"')} or a table with {c("text")}, {c("description")},
{c("icon")} and {c("lazy = true")} (run on Enter, with up to
{k.LAZY_TIMEOUT:.0f}&nbsp;s for commands).</p>
"""
    return page("Abbreviations Help", "help", usage + placeholders + filters + quotes + config)


def _default(cfg):
    """cfg stand-in with the default keyword, for built-ins when no keyword is set."""
    return type("Cfg", (), {"prefix": k.DEFAULT_PREFIX})()


def list_page(cfg) -> str:
    clipboard = k.get_clipboard()
    rows = []
    for abbr in sorted(cfg.abbrs.values(), key=lambda a: a["key"].lower()):
        if k.needs_input(abbr["text"]):
            labels = ", ".join(label for _, label, _ in k.ask_prompts(abbr["text"]))
            result = f'<span class="muted">asks for {escape(labels)} when Enter is pressed</span>'
        elif abbr.get("lazy"):
            result = '<span class="muted">computed when Enter is pressed</span>'
        else:
            text, ok = safe_expand(abbr["text"], cfg.cmd_timeout, clipboard)
            if len(text) > LIST_RESULT_MAX:
                text = text[:LIST_RESULT_MAX - 1] + "…"
            result = f'<span class="result{"" if ok else " error"}">{escape(text)}</span>'
        badge = '<span class="badge">on Enter</span>' if k.is_lazy(abbr) else ""
        rows.append(
            f"<tr><td class=nowrap><code>{escape(typed(cfg, abbr['key']))}</code>{badge}</td>"
            f"<td>{escape(abbr.get('description', ''))}</td>"
            f"<td><code>{escape(abbr['text'])}</code></td><td>{result}</td></tr>"
        )
    body = f"""
<input class="search" type="search" placeholder="Filter {len(rows)} abbreviations…" autofocus
       oninput="filterRows(this.value)">
<div class="scroll"><table id="abbrs">
<tr><th>Type</th><th>Description</th><th>Template</th><th>Expansion now</th></tr>
{"".join(rows)}
</table></div>
<p class="muted">Clipboard-based expansions use the clipboard as it was when this page was
opened. Edit abbreviations with <code>{escape(typed(cfg if cfg.prefix else _default(cfg),
"settings"))}</code>.</p>
<script>
function filterRows(q) {{
  q = q.toLowerCase();
  for (const row of document.querySelectorAll("#abbrs tr + tr"))
    row.hidden = q && !row.textContent.toLowerCase().includes(q);
}}
</script>
"""
    return page("All Abbreviations", "list", body)


def write_page(kind: str, cfg) -> Path:
    """Write both pages (they link to each other) and return the path of `kind`."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for name, build in (("help", help_page), ("list", list_page)):
        (CACHE_DIR / f"{name}.html").write_text(build(cfg))
    return CACHE_DIR / f"{kind}.html"
