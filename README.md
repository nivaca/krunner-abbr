# krunner-abbr

KRunner (Plasma 6) plugin that expands abbreviations. Type one in KRunner, press
Enter, and the expansion is copied to the clipboard.

## Install

    ./install.sh

This registers a D-Bus-activated runner (`io.github.nivaca.AbbrKRunner`) and creates
`~/.config/krunner-abbr/abbreviations.toml` from the example. Re-run it after moving
the project directory.

To remove it, run `./uninstall.sh`. Your abbreviations file is kept unless you add `--purge`.

## Abbreviations

Edit `~/.config/krunner-abbr/abbreviations.toml`. Changes apply on the next keystroke.
The example file documents all placeholders: `{date}`, `{now+1d:%A}`, `{arg}`,
`{uuid}`, `{env:VAR}`, `{clipboard}`, `{cmd:...}`, and others.

## Testing from the shell

    ./krunner_abbr.py --list          # every abbreviation with its current expansion
    ./krunner_abbr.py --expand gh foo # what KRunner would show for "gh foo"
