# krunner-abbr

This is a small plugin for KRunner (KDE Plasma 6) for expanding abbreviations. You open
KRunner, write the keyword and an abbreviation, for example `ex isod`, and press Enter.
The expansion (in this case today's date, `2026-10-28`) goes to the clipboard, and if
you want it can also be pasted directly in the window where you were working.

The idea is simple, but the abbreviations can do quite a lot of things: dates in any
format and language, text from the clipboard, small forms that ask you for some data,
shell commands, conversions of case and of quotes, Latin and Spanish helpers, links
that open in the browser, and more. Everything is in one text file that you can edit by
hand or with a settings dialog.

I wrote it for my own work (mostly LaTeX, Latin and Spanish texts), so many examples
come from there, but it is general.


## Contents

- [Requirements](#requirements)
- [Installing, updating and removing](#installing-updating-and-removing)
- [Fedora package](#fedora-package)
- [First steps](#first-steps)
- [Built-in commands](#built-in-commands)
- [Copying and pasting](#copying-and-pasting)
- [The configuration file](#the-configuration-file)
- [Placeholders](#placeholders)
- [Prompts and the form](#prompts-and-the-form)
- [Filters](#filters)
- [Quote styles](#quote-styles)
- [Formatted text](#formatted-text)
- [Finding and ordering abbreviations](#finding-and-ordering-abbreviations)
- [The bundled examples](#the-bundled-examples)
- [The settings dialog](#the-settings-dialog)
- [The help and list pages](#the-help-and-list-pages)
- [Importing from espanso](#importing-from-espanso)
- [Command line](#command-line)
- [Where things are saved](#where-things-are-saved)
- [Known limits and problems](#known-limits-and-problems)
- [License](#license)


## Requirements

- KDE Plasma 6 with KRunner (Wayland or X11). It was developed on Fedora with KWin on
  Wayland.
- Python 3.11 or newer, with `dbus-python` and PyGObject (`python3-dbus` and
  `python3-gobject` on Fedora). Normally they are installed already with Plasma.
- Klipper (the Plasma clipboard), or else `wl-clipboard` (`wl-copy`, `wl-paste`) or
  `xclip`.

Optional things, only for some functions:

| What | For what |
|---|---|
| PySide6 (`python3-pyside6`) | The settings dialog, and the form that shows all the questions of an abbreviation in one window. Without it, the questions come one by one with `kdialog`. |
| `kdialog` | Questions when PySide6 is not there. |
| `ydotool` (with its service) | Pasting, only if the Plasma portal does not work (see below). |
| `wl-clipboard` | Formatted text (HTML) on the clipboard, and `{selection}`. |
| PyYAML (`python3-pyyaml`) | Importing espanso files. |
| `curl` | Only for the bundled examples that download BibTeX. |


## Installing, updating and removing

    ./install.sh

The script does this:

1. It registers the runner in KRunner (`~/.local/share/krunner/dbusplugins/krunner-abbr.desktop`)
   and in D-Bus (`~/.local/share/dbus-1/services/io.github.nivaca.AbbrKRunner.service`).
   D-Bus starts the runner by itself the first time KRunner needs it, so there is nothing
   to put in autostart.
2. It creates your configuration, `~/.config/krunner-abbr/abbreviations.toml`, as a copy
   of the example file of the project. If the file exists already, it is never touched,
   also when it is a symbolic link (for example into Dropbox).
3. It stops the running instance and KRunner, so the new code is used from the next
   query.

The runner works directly from the project folder. If you move the folder, run
`./install.sh` again.

To update, get the new code and run `./install.sh` again. Your configuration stays as it
is. New example abbreviations are not added to it automatically: look in the
`abbreviations.toml` of the project and copy the ones you want.

To start again with the example configuration:

    ./install.sh --clean

This replaces `~/.config/krunner-abbr/abbreviations.toml` with the example. Before, it
saves your old file next to it, as `abbreviations.toml.bak-` plus the date and time. If the
file is a symbolic link, the file where it points is the one replaced.

To remove it:

    ./uninstall.sh            # removes the plugin, keeps your abbreviations
    ./uninstall.sh --purge    # removes also ~/.config/krunner-abbr

After changing the code yourself, it is enough to stop the runner; D-Bus starts it
again on the next query:

    pkill -f krunner_abbr.py


## Fedora package

It is possible to make an RPM, for installing it like any other program:

    sudo dnf install rpm-build
    ./build_rpm.py
    sudo dnf install build/rpm/krunner-abbr-1.1-1.fc44.noarch.rpm

The script takes the version from `__version__` in `krunner_abbr.py` and the files of the
git folder (also the ones not committed yet), and leaves the packages in `build/rpm/`.
Other options:

- `./build_rpm.py --srpm` makes only the source package.
- `./build_rpm.py --release 2` for a second package of the same version.
- `./build_rpm.py --mock fedora-44-x86_64` builds it again in a clean system with `mock`,
  the best test that no dependency is missing.
- `./build_rpm.py --copr nivaca/krunner-abbr` sends it to [Copr](https://copr.fedorainfracloud.org),
  after you created the project there and configured `copr-cli`. Then other people install
  it with `sudo dnf copr enable nivaca/krunner-abbr` and `sudo dnf install krunner-abbr`.

The package puts the program in `/usr/share/krunner-abbr`, and a command
`krunner-abbr` (`krunner-abbr --list`, `krunner-abbr settings`…). Your configuration is
created the first time the runner starts, from the example, and only when there is no
file (or link) yet. The files for the package are in the folder `packaging/`.

Note: use the package **or** `install.sh`, not both. If both are installed, the copy of
`install.sh` in your home folder wins; run `./uninstall.sh` before installing the RPM.

## First steps

Open KRunner (Alt+Space or Alt+F2) and write:

- `ex` alone: the list of all your abbreviations, with their result.
- `ex isod`: one abbreviation. Press Enter and the result is on the clipboard.
- `ex is`: after 2 letters (you can change this number), also the abbreviations that
  start with them are suggested, below the exact one.
- `ex fecha`: from 3 letters, words in the descriptions and in the keywords of the
  abbreviations are found too, a bit lower in the list.
- `ex upper hello world`: the text after the abbreviation is its *argument*. Many
  abbreviations use it, and when you don't write anything they take the clipboard.

The keyword `ex` can be changed (in the settings, `prefix`). It can be a word, like
`ex`, or a symbol, like `;`. A word needs a space after it (`ex isod`), a symbol does
not (`;isod`). With an empty keyword, the abbreviations match directly, without
anything before them. The names are not case-sensitive: `ex ISOD` is the same as
`ex isod`.

Enter copies the result. To paste it also, write `|paste` at the end: `ex isod|paste`
(see [Copying and pasting](#copying-and-pasting)).

Every result has some buttons (with the mouse, or with Tab and the arrow keys):

- **Copy the template**: copies the abbreviation without expanding it, for example
  `{date}`. Useful when you want to reuse it somewhere.
- **Edit this abbreviation**: opens the settings dialog with this abbreviation selected.

After each copy a small desktop notification shows what was copied. You can turn this
off with `notify = false`. The abbreviations that are computed only on Enter, and the
errors, always show a notification, because you don't see their result in KRunner.


## Built-in commands

These words work always, also if you have an abbreviation with the same name:

| You write | What happens |
|---|---|
| `ex settings` | Opens the settings dialog. |
| `ex help` or `ex ?` | Opens the help page in the browser. |
| `ex list` | Opens the page with all your abbreviations, their template and their result now. |
| `ex case Some Text` | Shows *Some Text* in every case style: UPPER, lower, Title, Sentence, snake_case, camelCase, Train-Case and others (up to 20; when two styles give the same result, it is shown once). Without text, it uses the clipboard. You choose one and press Enter. |
| `ex clip` | Shows the history of the clipboard (from Klipper), up to 30 entries. `ex clip word` searches in it, without caring about upper case or accents. Enter copies the old entry again. |
| `ex \|filter\|filter text` | Applies any chain of [filters](#filters) to the text, or to the clipboard when there is no text. For example `ex \|unhyphen\|join` cleans text copied from a PDF, and `ex \|replace(" ", "_") a b c` gives `a_b_c`. |

About the filter chains: when the name of the last filter is not complete, KRunner shows
all the filters that start like that, each one with its result, so you can choose. For
example `ex |tr` shows `|train`, and `ex |` alone shows every filter. A chain that
contains `sh(…)` runs only when you press Enter, never while you are writing, so a
command is never executed half written.

If you don't use any keyword (empty `prefix`), these commands still work with `ex`.


## Copying and pasting

Enter **copies** the result. To **paste** it too, write `|paste` at the end of the query:

    ex isod|paste           the abbreviation
    ex sig Ana|paste        also with an argument
    ex |upper|paste         a filter chain on the clipboard
    ex |upper|paste text    ... or on some text
    ex |paste               the clipboard as it is

The result says "Enter pastes", so you know before pressing Enter. Without `|paste`
nothing is pasted, only copied.

For pasting, the runner must press Ctrl+V for you in the window that you were using before KRunner. For this it uses the
"remote control" portal of Plasma, so nothing extra is needed. The first time you paste,
Plasma asks if Abbreviations can control the keyboard: say yes and choose to remember it.
The permission is saved in `~/.local/state/krunner-abbr/portal-token`; delete this file
to be asked again.

If the portal does not work (or you said no), the runner tries `ydotool`:

    sudo dnf install ydotool
    sudo systemctl enable --now ydotool

`wtype` works in some compositors, but in KWin it normally does not. On X11, also `xdotool` is used. If
none of them works, the text stays on the clipboard and a notification explains why it
was not pasted.

**Terminals.** Most terminals paste with Ctrl+Shift+V, not Ctrl+V. Before pasting, the
runner asks KWin which window has the focus, and for these programs it uses
Ctrl+Shift+V: Konsole, Yakuake, kitty, Alacritty, foot, WezTerm, Ghostty, GNOME
Terminal and Console, Ptyxis, Tilix, Terminator, xterm, Hyper, cool-retro-term, Contour
and Rio. For other programs you can say which keys to use, in a `[paste_keys]` table:

```toml
[paste_keys]
"org.example.app" = "shift+insert"
```

The name is the window class or the desktop file name of the program (in Plasma you
can see it with the window rules dialog, "Detect window properties"). The possible keys
are `ctrl+v`, `ctrl+shift+v` and `shift+insert`. If KWin does not answer in half a
second, Ctrl+V is used.

**The cursor.** With `{cursor}` in an abbreviation you decide where the cursor ends
after pasting. For example `\emph{{{cursor}}}` pastes `\emph{}` and leaves the cursor
between the braces, ready to write. The runner does this by pressing the Left arrow the
needed number of times. When the text is only copied, `{cursor}` is simply removed.


## The configuration file

Everything is in `~/.config/krunner-abbr/abbreviations.toml`, in [TOML](https://toml.io)
format. The runner reads it again by itself after each change, from the next key you
press in KRunner. You don't need to restart anything. If the file has an error, the
runner keeps the last good version and the error appears in the settings dialog and in
the logs.

The file has three parts: `[settings]`, `[abbr]` and, if you need it, `[paste_keys]`.

### `[settings]`

```toml
[settings]
prefix = "ex"
min_prefix_chars = 2
cmd_timeout = 2.0
notify = true
rank_by_use = true
disabled_groups = []
```

| Setting | Default | Meaning |
|---|---|---|
| `prefix` | `"ex"` | The keyword before the abbreviations. A word needs a space after it, a symbol does not. `""` means no keyword. |
| `min_prefix_chars` | `2` | After how many letters the abbreviations that *start* with what you wrote are suggested. `0` means only exact names. Search in descriptions and keywords starts at 3 letters (or this number, if it is bigger). |
| `cmd_timeout` | `2.0` | Seconds that a `{cmd:…}` or `sh(…)` can take while you write. Commands run on every key you press, so they must be fast. On Enter (for `lazy` abbreviations and prompts) they can take up to 20 seconds. |
| `notify` | `true` | A notification after every copy. |
| `rank_by_use` | `true` | The abbreviations that you use more, and more recently, come first. |
| `disabled_groups` | `[]` | Groups that are not offered in KRunner, for example `["LaTeX"]`. |

### `[abbr]`

Each abbreviation is either a simple line:

```toml
[abbr]
em = "—"
isod = { text = "{date}", description = "Today's date (ISO)" }
```

or a table with more fields:

```toml
[abbr.logeion]
text = "https://logeion.uchicago.edu/{input|strip|urlencode}"
description = "Look up a Latin or Greek word in Logeion"
icon = "accessories-dictionary"
action = "open"
group = "Lookup"
```

Simple lines must come before the tables, because this is how TOML works. Long texts
can use three quotes, `"""…"""` (with escapes) or `'''…'''` (literal, without escapes;
very practical for LaTeX).

All the fields of an abbreviation:

| Field | Meaning |
|---|---|
| `text` | The template: normal text, with [placeholders](#placeholders) between braces. The only field that is required. |
| `description` | Shown under the result in KRunner, and searched from 3 letters. |
| `icon` | Name of an icon of the theme, for example `edit-paste`, `view-calendar`, `accessories-dictionary`. |
| `lazy` | `true`: the result is computed only when you press Enter, not while you write. For slow things, like downloads. KRunner shows the description instead of the result. |
| `keywords` | Other words that find this abbreviation: `keywords = "fecha hoy"` or a list. |
| `aliases` | Other names for the same abbreviation: `aliases = ["today", "hoy"]`. A real name of another abbreviation always wins over an alias. |
| `group` | The heading in KRunner and the section in `ex list`. Whole groups can be disabled. |
| `format` | `"markdown"` or `"html"`: copied as formatted text. See [Formatted text](#formatted-text). |
| `action` | `"open"`: Enter opens the result (a web page, a file or a folder) instead of copying it. |
| `when` | A regular expression. When the clipboard matches it, writing only `ex` shows this abbreviation first. |
| `propagate_case` | `true`: the result follows how you write the name. `ex Sig` gives it Capitalized, `ex SIG` in UPPERCASE. |

### `[paste_keys]`

Programs that paste with other keys, see [Copying and pasting](#copying-and-pasting).


## Placeholders

A placeholder is a word between braces that is replaced when the abbreviation expands.
All of them can be followed by [filters](#filters): `{clipboard|upper}`.

### Dates and times

| Placeholder | Result |
|---|---|
| `{date}` | Today, `2026-10-28`. |
| `{time}` | Now, `14:05`. |
| `{now}` | Date and time, `2026-10-28T14:05:09`. |
| `{date:%d/%m/%Y}` | Any format of [strftime](https://strftime.org) after the colon: `28/10/2026`. `%-d` gives the day without zero. |
| `{date+1d}` | With offsets: `s` seconds, `m` minutes, `h` hours, `d` days, `w` weeks, `mo` months, `y` years. They can be combined: `{now+1w-2h:%c}`. |
| `{date@es:%A %d de %B}` | Names of days and months in another language: *miércoles 28 de octubre*, also if your desktop is in English. Any installed locale works (`es`, `es_CO`, `de`, `fr`, `it`…). With offsets: `{date+1d@es:%A}`. |

### Typed text and clipboard

| Placeholder | Result |
|---|---|
| `{arg}` | All the text written after the abbreviation: in `ex gh octocat` it is `octocat`. |
| `{arg:default}` | The same, but with a value when nothing was written. |
| `{arg1}`, `{arg2}`… | Single words of the written text. The last one that is used takes all the rest. With "double quotes" a word can have spaces. `ex cit Cicero Off. 1.3` with `{arg1}, \textit{{{arg2}}} {arg3}` gives `Cicero, \textit{Off.} 1.3`. |
| `{clipboard}` | What is on the clipboard now. |
| `{clipboard:2}` | An older entry of the Klipper history: `2` is the one before the current, `3` the one before that, and so on. |
| `{input}` | The written text if there is some, if not the clipboard. Most transformation examples use this one, so they work in both ways. |
| `{selection}` | The text that is selected (highlighted) on the screen, without copying it before. |

### Prompts (they ask you something when you press Enter)

| Placeholder | Result |
|---|---|
| `{ask:Label}` | A line of text. `{ask:Label=default}` gives a value already written. |
| `{asktext:Label}` | Several lines of text. |
| `{choose:Label=a, b, c}` | A list to choose one option. An option with a comma goes between "double quotes". |
| `{opt:Label}…{/opt}` | A checkbox: when it is ticked the text between the two tags stays, if not it goes away. `{opt:Label=yes}` starts ticked. |
| `{askdate:Label}` | A calendar to pick a date. It gives `2026-10-28`; the filter `datefmt` changes the format. |

More about them in [Prompts and the form](#prompts-and-the-form).

### Other placeholders

| Placeholder | Result |
|---|---|
| `{abbr:name}` | The result of another abbreviation. Like this a signature or a header lives in only one place. If they include each other in a circle, you get an error, not an infinite loop. |
| `{cursor}` | Where the cursor ends after pasting. |
| `{random:a, b, c}` | One of the options, by chance. |
| `{calc:2*(3+4)}` | Arithmetic, see the filter `calc`. |
| `{uuid}` | A random UUID. |
| `{env:USER}` | An environment variable. |
| `{cmd:command}` | What a shell command prints. Inside the command, `$ABBR_ARG` has `{arg}`, `$ABBR_INPUT` has `{input}`, and `$ABBR_ASK1`, `$ABBR_ASK2`… have the answers to the prompts (`$ABBR_ASK` is the first one). |
| `{{` and `}}` | Literal braces. `\emph{{{input}}}` gives `\emph{text}`. |

A placeholder cannot contain `{` or `}` inside it. If you need them in a command, put
the command in a small script and call the script.


## Prompts and the form

When an abbreviation has one or more prompts (`{ask}`, `{asktext}`, `{choose}`, `{opt}`,
`{askdate}`), KRunner does not show the result; it shows the description, and the
questions come when you press Enter. All of them appear together, in one small window
(this needs PySide6; without it, `kdialog` asks them one after the other). If you
cancel, nothing is copied.

The same label is asked only once, also when it is used several times. So
`Dear {ask:Name}, … {ask:Name}` asks for the name one time. The prompts inside an
abbreviation included with `{abbr:…}` go in the same form.

The answers can pass through filters (`{ask:Title|titulo}`), and the shell commands
receive them in `$ABBR_ASK1`, `$ABBR_ASK2`… There is a filter `silent` for the case when
you want to ask something only for a command, without writing it in the result.

An example, the bundled `cover`:

```toml
[abbr.cover]
text = """Dear {ask:Name},

please find attached my application for the {ask:Position} position.{opt:Mention CV=yes} My CV is attached as well.{/opt}

{abbr:signoff}"""
```


## Filters

Filters go after a placeholder, with `|`, and they can be chained from left to right:
`{clipboard|unhyphen|join}`, `{date:%Y|roman}`. Some filters take arguments between
parentheses. Arguments with spaces or commas go between double quotes:
`replace(" ", "_")`. Inside `{cmd:…}`, write the shell pipes with spaces (`a | sort`),
so they are not taken as filters.

The same filters work in KRunner without an abbreviation: `ex |upper|join some text`.

**Case of letters**

| Filter | What it does |
|---|---|
| `upper`, `lower` | UPPERCASE, lowercase. |
| `title` | English Title Case, with the usual rules: small words like *of*, *the*, *and* stay in lower case, and words like *iPhone* or *NASA* are not changed. |
| `titulo` | Spanish title: only the first word with capital (*El nombre de la rosa*). Names and acronyms (ONU) stay, but only when the rest of the text is in lower case; in a text Where Every Word Has Capital it is not possible to know which are names. |
| `sentence` | Sentence case: capital at the start and after `.`, `!`, `?`. |
| `capitalize` | Capitalize Every Word, without rules. |
| `swapcase` | Changes upper to lower and the opposite. |
| `alternating`, `randomcase` | aLtErNaTiNg and rAnDOm case. |

**Styles for identifiers.** These split the text in words (also `camelCase` and
`HTTPResponse`), remove accents and join the words again:
`snake` (snake_case), `kebab` (kebab-case), `constant` (CONSTANT_CASE), `dot`
(dot.case), `camel` (camelCase), `pascal` (PascalCase), `train` (Train-Case), `ada`
(Ada_Case), `cobol` (COBOL-CASE), `path` (path/case), `flat` (flatcase), `space`
(*parse http response*) and `start` (*Parse HTTP Response*, the acronyms stay).

**Cleaning text**

| Filter | What it does |
|---|---|
| `join`, `join(sep)` | Joins the lines with a space, or with `sep`. |
| `unhyphen` | Joins again the words cut with a hyphen at the end of a line (typical in PDFs). |
| `squeeze` | Many spaces become one. |
| `strip` | Removes spaces and line breaks at the start and at the end. |
| `replace(old, new)` | Replaces a text by another one. |
| `sub(pattern, repl)` | The same with a regular expression (Python syntax, `\1` for groups). |
| `deaccent` | Removes accents and other diacritics: *café* → *cafe*. |
| `slug` | For URLs and file names: *Café: the Menu!* → *cafe-the-menu*. |

**Lines and lists**

| Filter | What it does |
|---|---|
| `sort`, `sort(reverse)` | Sorts the lines in a natural way (2 before 10), without caring about case or accents. |
| `sortitems(", ")` | Sorts a list in one line: *pear, apple* → *apple, pear*. `sortitems(";", reverse)` for the other order. |
| `uniq` | Removes repeated lines. |
| `reverse` | Inverts the order of the lines. |
| `shuffle` | Mixes the lines. |
| `number` | Numbers the lines that are not empty: 1. 2. 3. |

**Encodings and numbers**

| Filter | What it does |
|---|---|
| `urlencode`, `urldecode` | Percent encoding for URLs. |
| `base64`, `unbase64` | Base64. |
| `latex` | Escapes the special characters of LaTeX: `& % $ # _ { } ~ ^ \`. |
| `roman`, `unroman` | 2026 ↔ MMXXVI. |
| `calc` | Calculates: `+ - * / // % **`, parentheses, `round(x, n)` and `abs(x)`. A comma is read as decimal point when there is no point (`1,5*2` → `3`). Only arithmetic is accepted, nothing else. |
| `datefmt(format)`, `datefmt(format, language)` | Changes the format of a date written as `2026-10-28` (for example the answer of `{askdate}`): `datefmt(%A %-d de %B, es)`. |

**Latin**

| Filter | What it does |
|---|---|
| `nomacrons` | Removes macrons and breves (*āmīcus* → *amicus*), other accents stay. |
| `noligatures` | *æ œ* → *ae oe*. |
| `latinize` | Everything for searching and comparing texts: ligatures, macrons, breves and diaereses out (*poëta* → *poeta*), v → u and j → i. The opposite (ae → æ) is not offered, because it can't be done well: *poeta*, *aer*. |
| `vtou`, `jtoi` | Only v → u, or only j → i. |

**Quotes**

| Filter | What it does |
|---|---|
| `quotes(from, to)` | Converts one style of quotes into another; see [Quote styles](#quote-styles). |
| `smartquotes` | Straight quotes → typographic ones, also apostrophes. |
| `straightquotes` | Typographic quotes and guillemets → straight ones. |

**Counting and others**

| Filter | What it does |
|---|---|
| `stats` | "12 words · 70 characters · 2 lines". |
| `words`, `chars` | Only the number of words, or of characters. |
| `silent` | Gives nothing. For an `{ask}` that is used only by a later `{cmd}`. |
| `sh(command)` | Passes the text through a shell command, for example `sh(sort -u)`. |

The help page (`ex help`) has an example for each filter, computed in the moment.


## Quote styles

For `quotes(from, to)`. The styles are: `straight` "…", `straight-single` '…',
`double` “…”, `single` ‘…’, `guillemets` «…», `single-guillemets` ‹…›, `french` « … »
(with narrow non-breaking spaces inside), `low` „…“ and `low-single` ‚…‘. You can
also give a pair directly, like `«»`.

With several pairs, all are converted in one pass. This is important for nested quotes:
`quotes(guillemets, double, double, single)` converts «a “b” c» into “a ‘b’ c”
correctly, without converting the new double quotes again.


## Formatted text

With `format = "markdown"`, the abbreviation is copied as formatted text (HTML), so
`**bold**`, `*italics*`, `` `code` ``, `[links](https://…)`, headings with `#` and
lists with `-` or `1.` arrive with their format in programs like Thunderbird,
LibreOffice or the editors in the browser. With `format = "html"` you write the HTML
yourself. When the paste goes into a terminal, the plain text is used.

There is one limit: on Wayland, `wl-copy` offers the HTML to all programs, also to the
ones that want plain text. So, if you paste a formatted abbreviation in a field of
plain text (not a terminal), you will see the HTML tags. Also, `{cursor}` is ignored in
formatted text.


## Finding and ordering abbreviations

- **Names and aliases.** Exact name or alias first, then (after `min_prefix_chars`
  letters) the ones that start like that.
- **Descriptions and keywords.** From 3 letters, the words of the descriptions and the
  `keywords` are searched too, without caring about accents. They come after the names.
- **Use.** With `rank_by_use = true`, in each of these levels the abbreviations that you
  use more and more recently come first. A use of one month ago counts half as a use of
  today. The counts are in `~/.local/state/krunner-abbr/usage.json`.
- **Clipboard.** When you write only the keyword, the abbreviations whose `when`
  matches the clipboard come first, with "matches the clipboard" under them. For
  example, with a DOI copied, `ex` shows `bib` and `doi` at the top.
- **Groups.** `group = "LaTeX"` puts the abbreviation under this heading in KRunner and
  in `ex list`. `disabled_groups = ["LaTeX"]` hides the whole group from KRunner; other
  abbreviations can still include them with `{abbr:…}`.
- **Case.** With `propagate_case = true`, `ex Sig` gives the result with capital and
  `ex SIG` all in capitals.


## The bundled examples

The example file (`abbreviations.toml` in the project) has around 90 abbreviations. They
are also a good documentation of the syntax. Some of them:

- **Dates:** `isod` (also `today`), `isodt`, `isot`, `ts` (for file names), `tmrw`,
  `yday`, `nweek`, `dmy`, `longd`, `week`, `epoch`, `fecha` (in Spanish), `on next
  friday` (a date in plain English, with `date -d`), `days` (days until a date), `due`
  (date picker).
- **Case and identifiers:** `title`, `lower`, `upper`, `sentence`, `caps`, `swap`,
  `snake`, `kebab`, `camel`, `pascal`, `const`, `slug`.
- **Cleaning:** `join`, `unwrap` (text copied from a PDF), `sort`, `sortr`, `sortu`,
  `sortc`, `numl`, `wc`.
- **Quotes:** `g2d`, `d2g`, `g2s`, `d2s`, `smartq`, `straightq`, `quote`, `guil`.
- **LaTeX and Markdown:** `emph`, `emc` (with the cursor inside), `cite`, `citep` (asks
  for the page), `cit` (`ex cit Cicero Off. 1.3`), `latex`, `mdlink`.
- **Latin:** `annus`, `roman`, `unroman`, `macrons`, `logeion`.
- **Spanish:** `fecha`, `rae`, `guil`.
- **Bibliography:** `bib` (BibTeX for a typed or copied DOI, computed on Enter),
  `askdoi` (asks for the DOI), `doi` (opens the article).
- **Letters and mail:** `letter`, `cover`, `signoff`, `greet`, `sig`, `mdsig`
  (formatted), `mail`.
- **Symbols:** `em`, `en`, `sect`, `para`, `check`, `copy`, `crme`, `reg`, `tm`, `deg`,
  `dag`, `ddag`, `bullet`, `ell`, `times`, `nbsp`, `nnbsp` and more.
- **Others:** `uuid`, `me`, `kernel`, `gh`, `coin`, `calc`.


## The settings dialog

`ex settings`, or `./krunner_abbr_settings.py` (needs PySide6).

- **General:** keyword, suggestions, command timeout, notifications,
  ranking by use, disabled groups.
- **Abbreviations:** the list on the left (with a filter), and on the right all the
  fields of the selected one, a menu "Insert Placeholder" with all the placeholders and
  filters, a field for testing the argument, and a preview that is updated while you
  write. For the abbreviations that ask something or that are `lazy`, the button
  **Run Now** opens the form and shows the result.

Saving writes the whole file again and keeps the previous version as
`abbreviations.toml.bak`. Be careful: the comments that you wrote by hand in the file
are lost when the dialog saves it (the comments at the start, with the reference, are
written again).


## The help and list pages

`ex help` and `ex list` open two HTML pages in the browser. They are generated in the
moment from the code and from your configuration, so they are always up to date.

- **Help:** how to use it, all the placeholders (with the result now, when it makes
  sense), all the filters with an example each, the quote styles and the configuration.
- **List:** a summary, a search field (names, descriptions, keywords, aliases, groups
  and templates), buttons for showing only some kinds (take typed text, use the
  clipboard, ask for input, run on Enter, formatted, open links, clipboard
  suggestions, run commands, errors), and a table with the template (with colours and
  links), the result now, an example with typed text, the questions of the form, and
  how many times you used each one. You can sort it by group or by use, and a click on
  a name copies what you must write in KRunner.

When an abbreviation does not work with what you have now on the clipboard (for example
`roman` with a date), the page says it in grey, it is not marked as an error.


## Importing from espanso

[espanso](https://espanso.org) is another program for abbreviations, with many shared
packages. You can bring its files here:

    ./krunner_abbr.py --import-espanso base.yml            # shows the result
    ./krunner_abbr.py --import-espanso base.yml --write    # adds it to your configuration

What is converted: triggers (`:date` becomes `date`; with several triggers, the first
one), `replace`, `markdown` and `html`, the cursor `$|$`, forms with their fields, and
the variables `echo`, `date` (with format and offset), `clipboard`, `shell`, `script`,
`choice`, `match` and `form`. What is not converted is skipped, with the reason:
regex triggers, images, the variable `random`, global variables, commands that contain
braces, and names that you have already. With `--write` the old configuration is kept
as `abbreviations.toml.bak`.


## Command line

    ./krunner_abbr.py --list                     # all abbreviations with their result now
    ./krunner_abbr.py --expand ex gh octocat     # what KRunner shows for "ex gh octocat"
    ./krunner_abbr.py --page help                # writes the help page and prints its path
    ./krunner_abbr.py --import-espanso FILE [--write]

With `--expand`, the prompts are asked in the terminal.


## Where things are saved

| File | What |
|---|---|
| `~/.config/krunner-abbr/abbreviations.toml` | Your abbreviations and settings. |
| `~/.config/krunner-abbr/abbreviations.toml.bak` | The previous version, after saving with the dialog or importing. |
| `~/.local/state/krunner-abbr/usage.json` | How many times and when you used each abbreviation. |
| `~/.cache/krunner-abbr/` | The help and list pages, and a small KWin script for knowing the active window. |
| `~/.local/share/krunner/dbusplugins/krunner-abbr.desktop` | The registration in KRunner. |
| `~/.local/share/dbus-1/services/io.github.nivaca.AbbrKRunner.service` | The registration in D-Bus. |


## Known limits and problems

- **Nothing is pasted.** The notification says what failed. If you said no to the
  Plasma question, restart the runner (`pkill -f krunner_abbr`) to be asked again, or
  install `ydotool` and enable its service.
- **In a terminal, the paste does nothing.** Probably the terminal is not in the list;
  add it in `[paste_keys]` with `ctrl+shift+v`.
- **HTML tags appear when pasting.** It is the limit of formatted text on Wayland,
  explained above.
- **`{selection}` gives the old text of KRunner.** When KRunner opens, it can select the
  previous search, and then this is the selection. In this case select the text again,
  or in the KRunner settings tell it to not keep the last search.
- **A command is slow.** Commands run on each key you press, with a limit of
  `cmd_timeout` seconds. For slow ones (downloads), put `lazy = true`.
- **Changes in the code are not used.** The runner keeps running; stop it with
  `pkill -f krunner_abbr.py` and it starts again on the next query.
- **The comments in my file disappeared.** The settings dialog writes the file again;
  if you prefer your comments, edit the file by hand.


## License

Copyright (C) 2026 Nicolas Vaughan.

This program is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version.

It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See
the GNU General Public License for more details. You have a copy in the file
[`LICENSE`](LICENSE); if not, see <https://www.gnu.org/licenses/>.
