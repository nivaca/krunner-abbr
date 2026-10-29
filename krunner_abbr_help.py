# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""HTML pages behind "<keyword> help" and "<keyword> list".

Both pages are generated from the code and the current configuration each time they
are opened, so they always match what the runner actually does.
"""

import os
import re
import tomllib
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

# (group, [(syntax, description, template whose current expansion is shown, or None)])
PLACEHOLDER_DOCS = [
    ("Dates and times", [
        ("{date}", "Today, in ISO format.", "{date}"),
        ("{time}", "The time now, hours and minutes.", "{time}"),
        ("{now}", "Date and time together, in ISO format.", "{now}"),
        ("{date:%d/%m/%Y}", "Any strftime format after the colon. %-d gives the day without "
         "a zero before.", "{date:%d/%m/%Y}"),
        ("{date+1d}", "With offsets: s seconds, m minutes, h hours, d days, w weeks, mo months, "
         "y years. They can be combined, like {now+1w-2h}.", "{date+1d}"),
        ("{date@es:%A %d de %B}", "Names of days and months in another language, also when the "
         "desktop is in English. Any installed locale: es, es_CO, de, fr, it…",
         "{date@es:%A %d de %B}"),
    ]),
    ("Typed text and clipboard", [
        ("{arg}", "All the text written after the abbreviation.", None),
        ("{arg:default}", "The same, with a value for when nothing was written.", None),
        ("{arg1} {arg2}…", 'Single words of the written text; the last one that is used takes '
         'all the rest. A word with spaces goes between "double quotes". '
         "ex cit Cicero Off. 1.3 gives Cicero / Off. / 1.3.", None),
        ("{clipboard}", "What is on the clipboard now.", None),
        ("{clipboard:2}", "An older entry of the Klipper history: 2 is the one before the "
         "current, 3 the one before that, and so on.", None),
        ("{input}", "The written text if there is some, if not the clipboard. Most of the "
         "examples that transform text use this one.", None),
        ("{selection}", "The text that is selected (highlighted) on the screen, without "
         "copying it before.", None),
    ]),
    ("Prompts (they ask when you press Enter)", [
        ("{ask:Label}", "A line of text. {ask:Label=default} gives a value already written.",
         None),
        ("{asktext:Label}", "Several lines of text.", None),
        ("{choose:Label=a, b, c}", 'A list to choose one option. An option with a comma goes '
         'between "double quotes".', None),
        ("{opt:Label}…{/opt}", "A checkbox: if it is ticked, the text between the two tags "
         "stays, if not it goes away. {opt:Label=yes} starts ticked.", None),
        ("{askdate:Label}", "A calendar for choosing a date. It gives 2026-10-28; the filter "
         "datefmt changes the format.", None),
    ]),
    ("Other placeholders", [
        ("{abbr:name}", "The result of another abbreviation, so a signature or a header is in "
         "only one place. Its prompts go in the same form.", None),
        ("{cursor}", "Where the cursor ends after pasting. When only copying, it is removed.",
         None),
        ("{random:a, b, c}", "One of the options, by chance.", "{random:heads, tails}"),
        ("{calc:2*(3+4)}", "Arithmetic, like the filter calc.", "{calc:2*(3+4)}"),
        ("{uuid}", "A random UUID.", "{uuid}"),
        ("{env:USER}", "An environment variable.", "{env:USER}"),
        ("{cmd:command}", "What a shell command prints. Inside the command, $ABBR_ARG has "
         "{arg}, $ABBR_INPUT has {input}, and $ABBR_ASK1, $ABBR_ASK2… the answers to the "
         "prompts ($ABBR_ASK is the first).", None),
        ("{{  }}", "Literal braces: \\emph{{{input}}} gives \\emph{text}.", None),
    ]),
]

# an example of use for each placeholder, shown under its description
PLACEHOLDER_USES = {
    "{date}": 'ts = "Notes {date}.md" gives Notes 2026-10-28.md',
    "{time}": 'For a log: "[{time}] "',
    "{now}": "Good for file names and logs, or with a format: {now:%H:%M:%S}",
    "{date:%d/%m/%Y}": "{date:%A, %-d %B %Y} gives Wednesday, 28 October 2026",
    "{date+1d}": "{date+1w:%d/%m} next week, {date-1mo} one month ago, {now+90m:%H:%M} in an hour and a half",
    "{date@es:%A %d de %B}": "Bogotá, {date@es:%-d de %B de %Y} for the head of a letter",
    "{arg}": 'gh = "https://github.com/{arg}"; ex gh octocat gives https://github.com/octocat',
    "{arg:default}": "{arg:Sir or Madam} when no name was written",
    "{arg1} {arg2}…": 'cit = "{arg1}, \\textit{{{arg2}}} {arg3}"; ex cit Cicero Off. 1.3',
    "{clipboard}": 'quote = "“{clipboard}”" puts the copied text between quotes',
    "{clipboard:2}": "[{clipboard:2}]({clipboard}): copy a title, then a URL, and you have a Markdown link",
    "{input}": "upper = \"{input|upper}\": ex upper hello, or only ex upper for the clipboard",
    "{selection}": "Select a word with the mouse, then ex logeion with {selection|strip}",
    "{ask:Label}": "Dear {ask:Name}, …",
    "{asktext:Label}": "For the body of a short message",
    "{choose:Label=a, b, c}": "{choose:Greeting=Dear colleague, Dear Sir or Madam, Hi}",
    "{opt:Label}…{/opt}": "{opt:Mention CV=yes} My CV is attached as well.{/opt}",
    "{askdate:Label}": "{askdate:Deadline|datefmt(%-d de %B, es)}",
    "{abbr:name}": "{abbr:signoff} at the end of all your letters",
    "{cursor}": "\\emph{{{cursor}}}: after pasting you write directly inside",
    "{random:a, b, c}": "{random:heads, tails}, or random names for examples in class",
    "{calc:2*(3+4)}": "Mostly with typed text: {input|calc}, and ex calc 12*1,5",
    "{uuid}": "Unique names for files or test data",
    "{env:USER}": "{env:HOME}/Documents for a path",
    "{cmd:command}": "{cmd:uname -r}, or with the typed text: {cmd:date -d \"$ABBR_ARG\" +%F}",
    "{{  }}": "\\cite{{{input}}} gives \\cite{key}",
}

# worked examples for the help page: (title, story, TOML, typed argument, clipboard,
# answers or None, extra templates for {abbr:…}, note). The result is computed when the
# page is built.
RECIPES = [
    ("Clean a paragraph copied from a PDF",
     "Text copied from a PDF has the line breaks of the page, and words cut with a hyphen. "
     "Copy it, write ex unwrap, press Enter, and paste.",
     'unwrap = "{input|unhyphen|join|squeeze}"', "",
     "The philo-\nsophy of the\nmiddle   ages", None, {}, None),
    ("Put a title in the right case",
     "For an English title, title knows the small words. For Spanish, titulo leaves only the "
     "first word with capital.",
     'title = "{input|title}"', "the name of the rose and other stories", "", None, {},
     "For Spanish, the same with {input|titulo}: “historia de la filosofía en Colombia” → "
     "“Historia de la filosofía en Colombia”."),
    ("A date in Spanish for a letter",
     "Your desktop can be in English; the date is in Spanish anyway.",
     'fecha = "Bogotá, {date@es:%-d de %B de %Y}"', "", "", None, {}, None),
    ("A citation of an ancient author",
     "Three words, three places. The last placeholder takes all the rest of the text.",
     'cit = "{arg1}, \\\\textit{{{arg2}}} {arg3}"', "Cicero Off. 1.3", "", None, {},
     'For an author with two words, use quotes: ex cit "Pseudo Cicero" Rhet. 1.1'),
    ("Look up a Latin word",
     "action = \"open\" makes Enter open the result in the browser. Write the word, or copy "
     "it before and write only ex logeion.",
     'logeion = { text = "https://logeion.uchicago.edu/{input|strip|urlencode}", action = "open" }',
     "amicitia", "", None, {}, "The same idea works for any site with the word in the address: "
     "RAE (https://dle.rae.es/…), Perseus, a library catalogue…"),
    ("LaTeX command with the cursor inside",
     "When the abbreviation is pasted, the cursor goes back to {cursor}, here shown as ▮. "
     "This needs pasting (the button, or enter_pastes = true).",
     'emc = "\\\\emph{{{cursor}}}"', "", "", None, {}, None),
    ("A letter that asks for the details",
     "All the questions come in one window when you press Enter. The checkbox decides if the "
     "sentence between {opt:…} and {/opt} stays.",
     'cover = """Dear {ask:Name},\n\nplease find attached my application for the '
     '{ask:Position} position.{opt:Mention CV=yes} My CV is attached as well.{/opt}\n\n'
     '{abbr:signoff}"""',
     "", "", {("ask", "Name"): "Dr. Smith", ("ask", "Position"): "lecturer",
              ("opt", "Mention CV"): "1"},
     {"signoff": "Best regards,\nYour Name"},
     "Here the answers were Dr. Smith, lecturer, and the box ticked. signoff is another "
     "abbreviation, included with {abbr:signoff}: change it once and all the letters change."),
    ("Choose from a list",
     "{choose} shows a list; the first option is selected at the start.",
     'greet = "{choose:Greeting=Dear colleague, Dear Sir or Madam, Hi}"', "", "",
     {("choose", "Greeting"): "Dear colleague"}, {}, None),
    ("Pick a date in a calendar",
     "{askdate} gives the date as 2026-12-24, and datefmt writes it as you want, also in "
     "another language.",
     'due = "Deadline: {askdate:Deadline|datefmt(%A %-d de %B, es)}"', "", "",
     {("askdate", "Deadline"): "2026-12-24"}, {}, None),
    ("Quick calculations",
     "A comma works as decimal point. Only arithmetic is allowed.",
     'calc = "{input|calc}"', "12*1,5 + 3", "", None, {}, None),
    ("Normalize Latin before searching",
     "Editions write Cæsar, poëta, vīvit; a search engine or a concordance wants caesar, "
     "poeta, uiuit.",
     'latin = "{input|latinize|lower}"', "Cæsar poëta vīvit", "", None, {}, None),
    ("Quotes inside quotes",
     "From Spanish style to English style, also the quotes inside, in one step.",
     'g2d = "{input|quotes(guillemets, double, double, single)}"', "«dijo “hola” y se fue»",
     "", None, {}, None),
    ("A signature with format",
     "With format = \"markdown\" the text arrives with bold, italics and links in Thunderbird "
     "or LibreOffice. In a terminal it is pasted plain.",
     'mdsig = { text = "**Best regards**,\\n*Your Name*\\n[example.org](https://example.org)", '
     'format = "markdown" }', "", "", None, {}, None),
]

FAQ = [
    ("I don't see the result in KRunner, only a description.",
     "The abbreviation asks something ({ask}, {choose}…) or has lazy = true. It is computed "
     "when you press Enter, and a notification shows the result."),
    ("My command works in the terminal but not here.",
     "While you write, a command has only cmd_timeout seconds (2 by default). For slow commands "
     "put lazy = true: on Enter they have 20 seconds. Also, a placeholder cannot contain { or }; "
     "put such a command in a script."),
    ("How do I write a brace that is not a placeholder?",
     "Double it: {{ gives {, and }} gives }. So \\emph{{{input}}} gives \\emph{text}."),
    ("Can an abbreviation have two names, for example in Spanish and English?",
     'Yes, with aliases = ["hoy"]. Or use keywords, then also the description words find it.'),
    ("Can I use my abbreviations on two computers?",
     "Put abbreviations.toml in a synchronized folder (Dropbox, Nextcloud…) and make "
     "~/.config/krunner-abbr/abbreviations.toml a symbolic link to it. install.sh never "
     "replaces a link. The usage counts stay local."),
    ("I made a mistake in the file and now nothing works.",
     "The runner keeps the last version that was correct, so it continues working. Open "
     "the settings dialog: it shows the error with the line. There is also "
     "abbreviations.toml.bak, the version before the last save of the dialog."),
    ("Why is a result different when I paste in a terminal?",
     "Terminals get Ctrl+Shift+V instead of Ctrl+V, and plain text instead of formatted text. "
     "{cursor} works there too."),
    ("How do I stop an abbreviation from appearing, without deleting it?",
     'Give it a group, for example group = "Old", and put "Old" in disabled_groups.'),
]

# filter -> group on the help page; others go to "Other"
FILTER_GROUPS = {
    "Case of letters": "upper lower title titulo sentence swapcase capitalize alternating randomcase",
    "Styles for identifiers": "snake kebab constant dot camel pascal train ada cobol path flat "
                              "space start",
    "Cleaning text": "join unhyphen squeeze strip replace sub deaccent slug",
    "Lines and lists": "sort sortitems uniq reverse shuffle number",
    "Encodings and numbers": "urlencode urldecode base64 unbase64 latex roman unroman calc datefmt",
    "Latin": "nomacrons noligatures latinize vtou jtoi",
    "Quotes": "quotes smartquotes straightquotes",
}

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
.toc a, td a, p a, li a { color: var(--accent); text-decoration: none; }
.toc a:hover, td a:hover, p a:hover, li a:hover { text-decoration: underline; }
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
pre.snippet {
  font: 13px/1.45 ui-monospace, "JetBrains Mono", "Noto Sans Mono", monospace;
  background: var(--code-bg); padding: 8px 12px; border-radius: 6px; margin: 8px 0;
  white-space: pre-wrap; overflow-wrap: anywhere;
}
.recipe {
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 4px 16px 12px; margin: 14px 0;
}
.recipe h3 { font-size: 15px; margin: 12px 0 4px; font-weight: 650; }
.recipe p { margin: 6px 0; }
.label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
dl.faq dt { font-weight: 650; margin-top: 14px; }
dl.faq dd { margin: 4px 0 0 0; }
ol.steps li { margin-bottom: 10px; }
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


def recipe_html(cfg, recipe) -> str:
    """One worked example: the TOML, what you write, and the result computed now."""
    title, story, toml, arg, clipboard, answers, extra, note = recipe
    c = lambda s: f"<code>{escape(s)}</code>"
    key, value = next(iter(tomllib.loads("[abbr]\n" + toml)["abbr"].items()))
    abbr = value if isinstance(value, dict) else {"text": value}
    saved = dict(k.ABBR_TEXTS)
    k.ABBR_TEXTS.update(extra)  # for {abbr:…} in the example; restored below
    try:
        text = k.expand(abbr["text"], arg, cfg.cmd_timeout, clipboard, answers, keep_cursor=True)
        text, ok = text.replace(k.CURSOR_MARK, "▮"), True
    except Exception as e:
        text, ok = str(e), False
    finally:
        k.ABBR_TEXTS.clear()
        k.ABBR_TEXTS.update(saved)
    before = []
    if clipboard:
        before.append(f'<p><span class=label>On the clipboard</span></p><pre class="snippet">{escape(clipboard)}</pre>')
    write = typed(cfg, key + (" " + arg if arg else ""))
    action = "Enter opens" if abbr.get("action") == "open" else "you get"
    result = f'<pre class="snippet{"" if ok else " error"}">{escape(text)}</pre>'
    if ok and abbr.get("format") == "markdown":
        result += f'<div class="rendered">{k.markdown_to_html(text)}</div>'
    return f"""<div class="recipe"><h3>{escape(title)}</h3>
<p>{escape(story)}</p>
<pre class="snippet">{escape(toml)}</pre>
{"".join(before)}<p>You write {c(write)}, {action}:</p>
{result}
{f"<p class=muted>{escape(note)}</p>" if note else ""}</div>"""


def help_page(cfg) -> str:
    c = lambda s: f"<code>{escape(s)}</code>"
    t = lambda word: c(typed(cfg, word))  # an abbreviation as typed in KRunner
    b = lambda word: c(typed(cfg if cfg.prefix else _default(cfg), word))  # a built-in command
    toc = """<nav class="toc"><a href="#using">First steps</a><a href="#first">Your first abbreviation</a>
<a href="#recipes">Use cases</a><a href="#commands">Built-in commands</a>
<a href="#pasting">Copying and pasting</a><a href="#placeholders">Placeholders</a>
<a href="#prompts">Prompts</a><a href="#filters">Filters</a><a href="#quotes">Quote styles</a>
<a href="#config">Configuration</a><a href="#files">Files</a><a href="#faq">Questions</a>
<a href="#problems">Problems</a>
<a href="list.html">All abbreviations →</a></nav>"""
    keyword = (f"Your keyword is {c(cfg.prefix)}." if cfg.prefix else
               "You don't use a keyword, so the abbreviations match directly.")
    usage = toc + f"""
<h2 id="using">First steps</h2>
<p>Open KRunner (Alt+Space) and write the keyword and an abbreviation, for example
{t("isod")}. KRunner shows the result already; press <b>Enter</b> and it goes to the
clipboard. {keyword} The names are not case-sensitive.</p>
<ul>
  <li>{t("") if cfg.prefix else "Any abbreviation"} {"alone shows the list of all your abbreviations, with their result." if cfg.prefix else "is found directly, without anything before it."}</li>
  <li>After {cfg.min_prefix_chars or "—"} letters, also the abbreviations that start with them are
      suggested, below the exact one{"" if cfg.min_prefix_chars else " (this is turned off now)"}.
      From 3 letters, the words of the descriptions and the keywords are searched too, a bit lower.</li>
  <li>The text after the abbreviation is its argument: {t("upper hello")} gives
      <code>HELLO</code>. When you don't write anything, most of them take the clipboard.</li>
  <li>Every result has buttons (with the mouse, or Tab and the arrow keys):
      <b>{"copy without pasting" if cfg.enter_pastes else "copy and paste"}</b>,
      <b>copy the template</b> (without expanding it) and <b>edit this abbreviation</b>.</li>
  <li>The abbreviations marked <span class="badge">on Enter</span> are computed only when you
      press Enter: slow commands, or the ones that ask you something. A notification shows
      what was copied, and if you cancel the questions nothing is copied.</li>
  <li>After each copy a small notification shows the text{"" if cfg.notify else " (turned off now, except for errors and the results computed on Enter)"}.</li>
</ul>

<h2 id="first">Your first abbreviation, step by step</h2>
<p>The fastest way is the settings dialog ({b("settings")}, then the tab Abbreviations and
the button <b>Add</b>). Here the same with the file, so you see what happens inside.</p>
<ol class="steps">
  <li>Open {c(str(k.CONFIG_FILE))} with any text editor. (Or write {t("odf")} in KRunner,
      if you have it, to open the folder.)</li>
  <li>Under {c("[abbr]")}, before the first {c("[abbr.something]")} table, add a line:
      <pre class="snippet">hi = "Hello, how are you?"</pre>
      Save. You don't need to restart anything.</li>
  <li>In KRunner write {t("hi")}. The result is there already, press Enter and paste
      it where you want.</li>
  <li>Now something that changes every time. A placeholder between braces:
      <pre class="snippet">today = "Today is {{date:%A}}."</pre>
      {t("today")} gives <span class=result>{escape(safe_expand("Today is {date:%A}.", cfg.cmd_timeout, "")[0])}</span></li>
  <li>With text written after it, and a filter:
      <pre class="snippet">shout = "{{input|upper}}!"</pre>
      {t("shout hello")} gives <span class=result>HELLO!</span>; only {t("shout")} takes
      the clipboard.</li>
  <li>When you want a description, an icon or other options, use a table at the end of the file:
      <pre class="snippet">[abbr.shout]
text = "{{input|upper}}!"
description = "Shout the text"
icon = "format-text-uppercase"
aliases = ["loud"]</pre></li>
</ol>
<p>That's all the basic. The <a href="#recipes">use cases</a> below show more complete things,
and the tables after them have every detail.</p>

<h2 id="recipes">Use cases</h2>
<p>Real examples, each one with the TOML, what you write in KRunner and the result. The results
are computed in the moment this page was opened, with the real code, so they are exact (only the
answers to the questions are invented). Most of them are in the example file too.</p>
{"".join(recipe_html(cfg, r) for r in RECIPES)}

<h2 id="commands">Built-in commands</h2>
<p>These work always, also if you don't use a keyword.</p>
<div class="scroll"><table>
<tr><th>You write</th><th>What happens</th></tr>
<tr><td class=nowrap>{b("settings")}</td><td>Opens the settings dialog.</td></tr>
<tr><td class=nowrap>{b("help")}, {b("?")}</td><td>Opens this page.</td></tr>
<tr><td class=nowrap>{b("list")}</td><td>Opens the <a href="list.html">page with all your abbreviations</a>, their template and their result now.</td></tr>
<tr><td class=nowrap>{b("case Some Text")}</td><td>Shows the text in every case style: UPPER, lower, Title, Sentence, snake_case, camelCase, Train-Case and the others. Without text, it uses the clipboard.</td></tr>
<tr><td class=nowrap>{b("clip")}, {b("clip word")}</td><td>The clipboard history from Klipper (up to {k.CLIP_MAX} entries), or only the entries with this word, without caring about upper case or accents. Enter copies the old entry again.</td></tr>
<tr><td class=nowrap>{b("|unhyphen|join text")}</td><td>Applies any chain of <a href="#filters">filters</a> to the text, or to the clipboard when there is no text. If the last name is not complete, all the filters that start like that are shown, each one with its result. A chain with {c("sh(…)")} runs only on Enter, never while you write.</td></tr>
</table></div>

<h2 id="pasting">Copying and pasting</h2>
<p>Now, Enter <b>{"copies and pastes" if cfg.enter_pastes else "copies"}</b> the result
({c("enter_pastes = " + ("true" if cfg.enter_pastes else "false"))}). For pasting, the
runner presses Ctrl+V for you in the window where you were before KRunner. It uses the
remote control portal of Plasma: the first time, Plasma asks for permission (say yes, and
remember it). If the portal does not work, it tries {c("ydotool")} with its service running
({c("sudo dnf install ydotool && sudo systemctl enable --now ydotool")}). If nothing can
paste, the text stays on the clipboard and a notification says why.</p>
<p>Terminals paste with Ctrl+Shift+V. Before pasting, the runner asks KWin which window has
the focus, and uses Ctrl+Shift+V for the terminals that it knows (Konsole, Yakuake, kitty,
Alacritty, foot, WezTerm, Ghostty, GNOME Terminal, Ptyxis, Tilix, xterm and some more). Other programs go in a
{c("[paste_keys]")} table, with their window class or desktop file name:
{c('"org.example.app" = "shift+insert"')}. The keys can be {c("ctrl+v")},
{c("ctrl+shift+v")} or {c("shift+insert")}.</p>
<p>With {c("{cursor}")} in the template you decide where the cursor stays after pasting:
{c(r"\emph{{{cursor}}}")} leaves it between the braces.</p>
"""
    sections = []
    for group, docs in PLACEHOLDER_DOCS:
        rows = []
        for syntax, desc, example in docs:
            now = ""
            if example:
                result, ok = safe_expand(example, cfg.cmd_timeout, "")
                now = f'<span class="result{"" if ok else " error"}">{escape(result)}</span>'
            use = PLACEHOLDER_USES.get(syntax)
            use = f'<span class=sub>Example: {c(use)}</span>' if use else ""
            rows.append(f"<tr><td class=nowrap>{c(syntax)}</td><td>{escape(desc)}{use}</td>"
                        f"<td>{now}</td></tr>")
        sections.append(f'<tr class=group><td colspan=3>{escape(group)}</td></tr>{"".join(rows)}')
    placeholders = f"""
<h2 id="placeholders">Placeholders</h2>
<p>A placeholder is a word between braces that is replaced when the abbreviation expands.
Every placeholder can be followed by <a href="#filters">filters</a>, like
{c("{clipboard|upper}")}. A placeholder cannot have {c("{")} or {c("}")} inside; if a
command needs them, put the command in a small script.</p>
<div class="scroll"><table>
<tr><th>Placeholder</th><th>What it gives</th><th>Right now</th></tr>
{"".join(sections)}
</table></div>

<h2 id="prompts">Prompts and the form</h2>
<p>When an abbreviation has prompts ({c("{ask}")}, {c("{asktext}")}, {c("{choose}")},
{c("{opt}")}, {c("{askdate}")}), KRunner shows its description instead of the result, and the
questions come when you press Enter, all together in one small window (without PySide6,
{c("kdialog")} asks them one after the other). If you cancel, nothing is copied.</p>
<p>The same label is asked only once, also when it is used several times, and the prompts of
an abbreviation included with {c("{abbr:…}")} go in the same form. The answers can pass
through filters ({c("{ask:Title|titulo}")}), and the shell commands receive them in
{c("$ABBR_ASK1")}, {c("$ABBR_ASK2")}… For a question that is only for a command, the filter
{c("silent")} keeps it out of the result.</p>
"""
    group_of = {name: group for group, names in FILTER_GROUPS.items() for name in names.split()}
    by_group: dict[str, list[str]] = {}
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
        by_group.setdefault(group_of.get(name, "Counting and others"), []).append(
            f"<tr id=f-{name}><td class=nowrap>{c('|' + call)}</td><td>{escape(desc)}</td>"
            f"<td>{shown}</td></tr>")
    order = [*FILTER_GROUPS, "Counting and others"]
    rows = "".join(f'<tr class=group><td colspan=3>{escape(g)}</td></tr>{"".join(by_group[g])}'
                   for g in order if g in by_group)
    filters = f"""
<h2 id="filters">Filters</h2>
<p>Filters change the value of a placeholder. They go after it with {c("|")} and can be
chained, from left to right: {c("{clipboard|unhyphen|join}")}, {c("{date:%Y|roman}")}.
Some take arguments between parentheses; an argument with spaces or commas goes between
double quotes, like {c('replace(" ", "_")')}. Inside {c("{cmd:…}")} write the shell pipes
with spaces ({c("a | sort")}), so they are not taken as filters. In KRunner you can use
them also without an abbreviation: {b("|upper|join some text")}.</p>
<p>The examples in the last column are computed now, with the real code.</p>
<div class="scroll"><table>
<tr><th>Filter</th><th>What it does</th><th>Example</th></tr>
{rows}
</table></div>
"""
    rows = []
    for name, (opener, closer) in k.QUOTE_STYLES.items():
        rows.append(f"<tr><td class=nowrap>{c(name)}</td>"
                    f"<td class=result>{escape(opener)}text{escape(closer)}</td></tr>")
    quotes = f"""
<h2 id="quotes">Quote styles</h2>
<p>For {c("|quotes(from, to)")}. A pair written directly, like {c("«»")}, works too. With
several pairs, all of them are converted in one pass, and this is important for nested quotes:
{c("quotes(guillemets, double, double, single)")} converts
<span class=result>«a “b” c»</span> into <span class=result>“a ‘b’ c”</span>, without
converting the new double quotes again.</p>
<div class="scroll"><table>
<tr><th>Style</th><th>Example</th></tr>
{"".join(rows)}
</table></div>
"""
    yes = lambda v: "true" if v else "false"
    settings_rows = [
        ("prefix", c(cfg.prefix) if cfg.prefix else c('""'), "The keyword before the abbreviations. "
         "A word needs a space after it, a symbol (like ;) does not. Empty means no keyword."),
        ("min_prefix_chars", c(str(cfg.min_prefix_chars)), "After how many letters the "
         "abbreviations that start with them are suggested. 0 means only exact names."),
        ("cmd_timeout", c(str(cfg.cmd_timeout)), "Seconds that a command can take while you "
         f"write (they run on each key). On Enter they can take up to {k.LAZY_TIMEOUT:.0f}."),
        ("notify", c(yes(cfg.notify)), "A notification after every copy."),
        ("enter_pastes", c(yes(cfg.enter_pastes)), "Enter copies and also pastes."),
        ("rank_by_use", c(yes(cfg.rank_by_use)), "The abbreviations you use more, and more "
         "recently, come first. A use of a month ago counts half."),
        ("disabled_groups", c(str(cfg.disabled_groups).replace("'", '"')), "Groups that are not "
         "offered in KRunner. They can still be included with {abbr:…}."),
    ]
    field_rows = [
        ("text", "The template, with placeholders. The only required field."),
        ("description", "Shown under the result in KRunner, and searched from 3 letters."),
        ("icon", "Name of an icon of the theme, like edit-paste or accessories-dictionary."),
        ("lazy", "true: computed only on Enter, for slow things like downloads."),
        ("keywords", 'Other words that find it: keywords = "fecha hoy", or a list.'),
        ("aliases", 'Other names: aliases = ["today", "hoy"]. A real name always wins.'),
        ("group", "Heading in KRunner and section in the list page; groups can be disabled."),
        ("format", '"markdown" or "html": copied as formatted text (in terminals, plain). '
         "On Wayland the HTML is offered also to plain-text programs, so there you see the tags."),
        ("action", '"open": Enter opens the result (web page, file or folder) instead of copying it.'),
        ("when", "A regular expression: when the clipboard matches, the bare keyword shows this "
         "abbreviation first."),
        ("propagate_case", "true: Sig gives the result Capitalized, SIG in UPPERCASE."),
    ]
    config = f"""
<h2 id="config">Configuration file</h2>
<p>Everything is in {c(str(k.CONFIG_FILE))}, in TOML format. It is read again by itself after
each change, from the next key you press in KRunner. If it has an error, the last good version
stays in use. You can edit it by hand or with {b("settings")}; the dialog writes the whole file
again (and keeps the old one as {c("abbreviations.toml.bak")}), so the comments that you wrote
by hand are lost.</p>
<p>Under {c("[settings]")} (the second column is your value now):</p>
<div class="scroll"><table>
<tr><th>Setting</th><th>Now</th><th>Meaning</th></tr>
{"".join(f"<tr><td class=nowrap>{c(n)}</td><td class=nowrap>{v}</td><td>{escape(d)}</td></tr>" for n, v, d in settings_rows)}
</table></div>
<p>Under {c("[abbr]")}, each abbreviation is a simple line, {c('em = "—"')}, or a table
({c("[abbr.name]")}) with these fields. The simple lines must come before the tables.
Long texts can use {c('"""…"""')}, or {c("\'\'\'…\'\'\'")} without escapes, which is practical
for LaTeX.</p>
<div class="scroll"><table>
<tr><th>Field</th><th>Meaning</th></tr>
{"".join(f"<tr><td class=nowrap>{c(n)}</td><td>{escape(d)}</td></tr>" for n, d in field_rows)}
</table></div>
<p>To bring abbreviations from espanso: {c("./krunner_abbr.py --import-espanso FILE")} shows
them converted, and with {c("--write")} they are added to your file.</p>

<h2 id="files">Files</h2>
<div class="scroll"><table>
<tr><th>File</th><th>What</th></tr>
<tr><td>{c(str(k.CONFIG_FILE))}</td><td>Your abbreviations and settings.</td></tr>
<tr><td>{c(str(k.USAGE_FILE))}</td><td>How many times and when you used each abbreviation.</td></tr>
<tr><td>{c(str(CACHE_DIR))}</td><td>This page, the list page, and a small KWin script for knowing the active window.</td></tr>
</table></div>

<h2 id="faq">Questions</h2>
<dl class="faq">
{"".join(f"<dt>{escape(q)}</dt><dd>{escape(a)}</dd>" for q, a in FAQ)}
</dl>

<h2 id="problems">Problems</h2>
<ul>
  <li><b>Nothing is pasted.</b> The notification says what failed. If you said no to the
      Plasma question, restart the runner to be asked again, or install {c("ydotool")} and
      enable its service.</li>
  <li><b>In a terminal the paste does nothing.</b> Probably the terminal is not known; add it to
      {c("[paste_keys]")} with {c("ctrl+shift+v")}.</li>
  <li><b>HTML tags appear when pasting.</b> This is the limit of formatted text on Wayland,
      explained in the {c("format")} field.</li>
  <li><b>{c("{selection}")} gives the old text of KRunner.</b> KRunner can select the previous
      search when it opens. Select your text again, or tell KRunner to not keep the last search.</li>
  <li><b>An abbreviation is slow.</b> Its commands run on every key, with a limit of
      {cfg.cmd_timeout} seconds. Put {c("lazy = true")} for slow ones.</li>
  <li><b>A change in the code has no effect.</b> Stop the runner with
      {c("pkill -f krunner_abbr.py")}; it starts again on the next query.</li>
</ul>
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
