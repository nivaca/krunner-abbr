# krunner-abbr

KRunner (Plasma 6) plugin that expands abbreviations. Type the trigger keyword and an
abbreviation in KRunner (`ex isod`), press Enter, and the expansion is copied to the
clipboard. The keyword alone lists all abbreviations.

Each match also has buttons (or press Tab / the arrow keys to reach them):
**Copy and paste** pastes the expansion into the window you were in, **Copy the template**
copies the unexpanded text, and **Edit** opens the abbreviation in the settings dialog.
Pasting presses Ctrl+V for you, which on Wayland needs `ydotool` (with its service
running: `sudo dnf install ydotool && sudo systemctl enable --now ydotool`) or `wtype`;
without one the text is still copied and a notification says why it wasn't pasted.
Terminals (Konsole, Yakuake, kitty, Alacritty, foot, WezTerm, Ghostty, GNOME Terminal…)
get Ctrl+Shift+V instead: the runner asks KWin which window has the focus. Add other
applications under `[paste_keys]`, by their window class or desktop file name:
`"org.example.app" = "shift+insert"` (keys: `ctrl+v`, `ctrl+shift+v`, `shift+insert`).

To make Enter paste as well, set `enter_pastes = true` (or tick "Enter: Paste into the
previous window" in the settings dialog); the button then becomes **Copy without pasting**.

`ex case Some Text` lists Some Text in every case style (`ex case` alone uses the
clipboard): UPPER, lower, Title, Sentence, snake_case, camelCase, Train-Case, and the
rest. Pick one and press Enter to copy it, or use its paste button.

`ex |filter|filter(args) text` applies any chain of filters (see below) without defining
an abbreviation: `ex |unhyphen|join` cleans up text copied from a PDF, `ex |replace(" ", "_")
a b c` gives `a_b_c`. Without text it works on the clipboard. A partly typed name lists
what it could become (`ex |tr` → `|train`, `ex |` → every filter), each with its result.
Chains with `sh(…)` run only when you press Enter.

## Install

    ./install.sh

This registers a D-Bus-activated runner (`io.github.nivaca.AbbrKRunner`) and creates
`~/.config/krunner-abbr/abbreviations.toml` from the example. Re-run it after moving
the project directory.

Re-running `./install.sh` (e.g. after updating) never touches an existing
`abbreviations.toml`, including a symlinked one; new example abbreviations are not merged
in, see `abbreviations.toml` in the project for them.

To remove it, run `./uninstall.sh`. Your abbreviations file is kept unless you add `--purge`.

## Settings

Type `ex help` (or `ex ?`) for a reference page (placeholders, filters, quote styles) or
`ex list` for a searchable table of every abbreviation and its current expansion;
both open in your browser. Type `ex settings` in KRunner (`<keyword> settings` if you changed the keyword), or
run `./krunner_abbr_settings.py` (needs PySide6). It sets the trigger keyword, partial-match
suggestions and the command timeout, and lets you add and edit abbreviations with a
live preview. Saving rewrites the config file and keeps the previous version as
`abbreviations.toml.bak`; comments you added by hand are not kept.

## Abbreviations

You can also edit `~/.config/krunner-abbr/abbreviations.toml` directly. Changes apply on
the next keystroke.
The example file documents all placeholders: `{date}`, `{now+1d:%A}`, `{arg}`,
`{uuid}`, `{env:VAR}`, `{clipboard}`, `{input}`, `{cmd:...}`, and others.

Filters transform any placeholder and can be chained, which makes clipboard clean-up
one keystroke away: copy some text, type `ex title`, press Enter, and the clipboard
now holds it in Title Case. Bundled examples: `title`, `lower`, `upper`, `sentence`,
`caps`, `swap`, the identifier styles `snake`, `kebab`, `camel`, `pascal` and `const`,
`join`, `unwrap` (fixes text copied from PDFs) and `slug`. Write your own with filters
such as `{clipboard|unhyphen|join}`, `{input|replace(" ", "_")}` or
`{clipboard|sh(sort -u)}`. `{input}` is the text typed after the abbreviation, or the
clipboard if nothing was typed.

Case filters: `upper`, `lower`, `title` (English title case), `sentence`, `capitalize`,
`swapcase`, `alternating` (aLtErNaTiNg) and `randomcase`. Identifier styles split words,
camelCase included, and drop accents: `snake`, `kebab`, `camel`, `pascal`, `constant`,
`dot`, `train` (Train-Case), `ada` (Ada_Case), `cobol` (COBOL-CASE), `path`, `flat`,
`space` (`parseHTTPResponse` → `parse http response`) and `start` (→ `Parse HTTP Response`).

Other bundled examples: LaTeX/Markdown wrappers (`emph`, `cite`, `latex`, `mdlink`,
`guil`), Latin helpers (`annus`, `roman`, `unroman`, `macrons`), `wc` (word count),
`on next friday` (plain-English dates), quote conversions (`g2d`, `d2g`, `g2s`, `d2s`,
`smartq`, `straightq`, built on the `quotes(from, to)` filter), list sorting (`sort`, `sortr`,
`sortu`, `sortc`, `numl`) and symbols (`em`, `en`, `sect`, `para`, `check`).

`{ask:Label}` and `{asktext:Label}` open an input dialog when you press Enter and use
the answer, which filters and commands can then process: `{ask:DOI|sh(…)}`, or
`$ABBR_ASK` inside `{cmd:…}`. Examples: `citep`, `letter`, `days`, `askdoi`.
`{choose:Label=a, b, c}` offers a list to pick from instead (example: `greet`).

Dates can use another language's day and month names: `{date@es:%A %d de %B}` gives
*miércoles 28 de octubre* on an English desktop (example: `fecha`); any installed locale
works (`es_CO`, `de`, `fr`…). `{clipboard:2}` is the previous clipboard entry from
Klipper's history, `{clipboard:3}` the one before, and `ex clip` lists the history in
KRunner (`ex clip word` searches it) so you can copy or paste an older entry again.

More filters: `titulo` gives a Spanish title (*El nombre de la rosa*), keeping proper
nouns and acronyms, though in Every Word Capitalized input it can't tell proper nouns
apart, so only acronyms survive. `latinize` normalizes Latin for searching and comparing
(æ œ → ae oe, macrons, breves and diaereses removed, v → u, j → i); `noligatures` does
only the first part.

A desktop notification confirms every copy. Set `notify = false` (or untick "Show a
notification after copying" in the dialog) to turn it off; abbreviations computed on
Enter, and errors, still notify.

`{cursor}` marks where the cursor should end up after pasting: `\emph{{{cursor}}}` pastes
`\emph{}` with the cursor between the braces. When only copying, it is simply removed.

`{abbr:sig}` inserts another abbreviation's expansion, so a signature or a letterhead
lives in one place; prompts inside it are asked along with the rest.

When an abbreviation asks for input, all its `{ask}`, `{asktext}`, `{choose}` and
`{opt}` prompts appear together in one form (or one `kdialog` each without PySide6).
`{opt:Label}…{/opt}` is a checkbox that keeps or drops the text between the tags;
`{opt:Label=yes}` starts ticked. Example: `cover`.

`format = "markdown"` (or `"html"`) copies an abbreviation as formatted text, so
`**bold**`, `*italics*`, `[links](https://…)` and lists arrive formatted in mail, office
and web editors. Pasted into a terminal it stays plain. Wayland's `wl-copy` offers the
HTML to every application, so pasting a formatted abbreviation with plain Ctrl+V into a
plain-text field (outside the terminals above) shows the HTML tags. Example: `mdsig`.

`keywords = "fecha hoy"` adds words that find an abbreviation: from 3 letters on, words
in descriptions and keywords match too, ranked below names (`ex fecha` → `isod`).

`aliases = ["today"]` gives an abbreviation other names. `group = "Lookup"` shows it
under that heading in KRunner and in `ex list`; `disabled_groups = ["Lookup"]` under
`[settings]` hides a whole group (its abbreviations can still be included with `{abbr:…}`).
Abbreviations you use often and recently rank higher (`rank_by_use = false` turns this
off; the counts live in `~/.local/state/krunner-abbr/usage.json` and appear in `ex list`).

`action = "open"` makes Enter open the expansion instead of copying it: a web page, a
file or a folder. The bundled `logeion` and `rae` look up the typed or copied word in
Logeion (Latin and Greek) or the RAE dictionary, and `doi` opens an article.

`when = "regex"` suggests an abbreviation while the clipboard matches: typing just `ex`
lists it first. The bundled `bib` and `doi` come up while a DOI is copied, `mdlink`
while a URL is.

`propagate_case = true` follows the case you type: `ex Sig` gives a Capitalized
expansion, `ex SIG` an UPPERCASE one.

More placeholders: `{selection}` is the selected text, no copying needed. `{arg1}`,
`{arg2}`… are single typed words, with the last one taking the rest and "quotes" keeping
spaces (`ex cit Cicero Off. 1.3`). `{random:a, b, c}` picks one option (`coin`),
`{calc:…}` and the `|calc` filter do arithmetic (`ex calc 2*(3+4)`), and
`{askdate:Label}` adds a date picker to the form, giving `2026-10-28`, which
`|datefmt(%A %-d %B, es)` reformats (`due`).

Slow abbreviations can set `lazy = true` ("Compute only when Enter is pressed" in the
dialog): KRunner shows the description instead of a live result, and the expansion runs
in the background when you press Enter, with a notification when it lands on the
clipboard. The bundled `bib` uses this to fetch BibTeX for a typed or copied DOI.

## Importing from espanso

    ./krunner_abbr.py --import-espanso base.yml           # print the converted abbreviations
    ./krunner_abbr.py --import-espanso base.yml --write   # append them to your config

This reads espanso match files, including packages from the espanso hub: triggers
(`:date` becomes `date`), `replace`/`markdown`/`html`, `$|$`, forms, and the variables
`echo`, `date`, `clipboard`, `shell`, `script`, `choice`, `match` and `form`. Matches
that use anything else (regex triggers, images, `random`, global variables), or whose
name already exists, are skipped with a note. `--write` keeps the previous config as
`abbreviations.toml.bak`.

## Testing from the shell

    ./krunner_abbr.py --list          # every abbreviation with its current expansion
    ./krunner_abbr.py --expand ex gh foo  # what KRunner shows for "ex gh foo"
