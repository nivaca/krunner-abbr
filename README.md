# krunner-abbr

KRunner (Plasma 6) plugin that expands abbreviations. Type the trigger keyword and an
abbreviation in KRunner (`ex isod`), press Enter, and the expansion is copied to the
clipboard. The keyword alone lists all abbreviations.

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

Set `notify = true` (or tick "Show a notification after copying" in the dialog) for a
desktop notification after every copy.

Slow abbreviations can set `lazy = true` ("Compute only when Enter is pressed" in the
dialog): KRunner shows the description instead of a live result, and the expansion runs
in the background when you press Enter, with a notification when it lands on the
clipboard. The bundled `bib` uses this to fetch BibTeX for a typed or copied DOI.

## Testing from the shell

    ./krunner_abbr.py --list          # every abbreviation with its current expansion
    ./krunner_abbr.py --expand ex gh foo  # what KRunner shows for "ex gh foo"
