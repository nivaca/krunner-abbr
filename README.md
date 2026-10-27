# krunner-abbr

KRunner (Plasma 6) plugin that expands abbreviations. Type the trigger keyword and an
abbreviation in KRunner (`abbr isod`), press Enter, and the expansion is copied to the
clipboard. The keyword alone lists all abbreviations.

## Install

    ./install.sh

This registers a D-Bus-activated runner (`io.github.nivaca.AbbrKRunner`) and creates
`~/.config/krunner-abbr/abbreviations.toml` from the example. Re-run it after moving
the project directory.

To remove it, run `./uninstall.sh`. Your abbreviations file is kept unless you add `--purge`.

## Settings

Type `abbr settings` in KRunner (`<keyword> settings` if you changed the keyword), or
run `./krunner_abbr_settings.py` (needs PySide6). It sets the trigger keyword, partial-match
suggestions and the command timeout, and lets you add and edit abbreviations with a
live preview. Saving rewrites the config file and keeps the previous version as
`abbreviations.toml.bak`; comments you added by hand are not kept.

## Abbreviations

You can also edit `~/.config/krunner-abbr/abbreviations.toml` directly. Changes apply on
the next keystroke.
The example file documents all placeholders: `{date}`, `{now+1d:%A}`, `{arg}`,
`{uuid}`, `{env:VAR}`, `{clipboard}`, `{cmd:...}`, and others.

## Testing from the shell

    ./krunner_abbr.py --list          # every abbreviation with its current expansion
    ./krunner_abbr.py --expand abbr gh foo  # what KRunner shows for "abbr gh foo"
