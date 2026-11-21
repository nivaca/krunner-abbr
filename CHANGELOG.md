# Changelog

All notable changes to krunner-abbr. The releases are on
[GitHub](https://github.com/nivaca/krunner-abbr/releases) and
[Codeberg](https://codeberg.org/nivaca/krunner-abbr/releases).

## Unreleased

- `build_rpm.py` copies the newest RPM to `dist/` too, in place of the
  older ones there.
- The compiled `__pycache__` files are no longer in the repository, and
  `dist/` is ignored.

## 2.0.1 — 2026-11-17

- Python 3.14 or later is needed (it was 3.11): the runner, the settings
  dialog and the build scripts stop with a message on an older Python,
  and the RPM requires it.
- A new filter, `utf16`, turns UTF-16 code units into text:
  `\uD83D\uDE00` inside a text, or only the units (`D83D DE00`,
  `U+00E9`), become 😀 and é. Surrogate pairs are joined.
- The release has a zip, `krunner-abbr-2.0.1.zip`, with the whole
  program: unzip it and run `./install.sh` in its folder.

## 2.0 — 2026-10-29

- **Pasting with `|paste`.** Enter copies the result; to paste it too,
  write `|paste` at the end of the query (`ex isod|paste`, `ex |upper|paste
  text`). The result says "Enter pastes" before Enter is pressed. The
  setting `enter_pastes` and the button Copy and paste are gone.
- Pasting goes through the "remote control" portal of Plasma, so nothing
  extra is needed: Plasma asks once for permission, which is kept in
  `~/.local/state/krunner-abbr/portal-token`. `ydotool` is used only if the
  portal does not work; `wtype` is no longer used.
- Results with several lines, or long ones, are shown in KRunner on up to
  five lines, and a result that is a web address can be dragged to the
  browser or to a document.
- KRunner's help (`?`) lists the syntax of this plugin (the runner uses
  the DBus2 API).
- When the keyword is changed in the settings dialog, KRunner is
  restarted, since it asks for the keyword only when it starts.
- The settings dialog marks the problems of an abbreviation and of the
  keyword, and a removed abbreviation can be brought back with undo.
- `./install.sh --clean` replaces the configuration with the example,
  keeping the old file as `abbreviations.toml.bak-` plus the date and
  time.
- `build_zip.py` makes a zip of the program.

## 1.1 — 2026-10-29

- **A Fedora package.** `./build_rpm.py` makes an RPM (also `--srpm`,
  `--release N`, `--mock CHROOT` and `--copr PROJECT`), which puts the
  program in `/usr/share/krunner-abbr` with a command `krunner-abbr`. The
  files for the package are in `packaging/`.
- The program, which was one file, is divided into modules:
  `krunner_abbr_config.py`, `_desktop`, `_filters`, `_match`, `_service`
  and `_template`.

## 1.0 — 2026-10-28

- First version: a plugin for KRunner (KDE Plasma 6) that expands
  abbreviations. Writing the keyword and an abbreviation (`ex isod`) puts
  the expansion on the clipboard, and can also paste it in the window
  where one was working. The abbreviations are kept in one TOML file and
  can give dates in any format and language, text from the clipboard,
  forms that ask for data, shell commands, filters for case and quotes,
  Latin and Spanish helpers, and links.
- A settings dialog (PySide6), help and list pages, an importer of
  espanso files, a command line, and `install.sh` and `uninstall.sh`.
- The default keyword is `ex` (it was `abbr`).
- GPL-3.0-or-later.
