#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""KRunner D-Bus plugin that expands abbreviations and copies them to the clipboard.

Abbreviations live in ~/.config/krunner-abbr/abbreviations.toml and may contain
dynamic placeholders such as {date}, {now+1d:%A}, {uuid}, {clipboard},
{cmd:...} or {arg}. See abbreviations.toml for the full syntax.

Run with --expand KEY [ARG...] to test an abbreviation from the shell.
"""

import argparse
import locale
import sys

# the façade: the other scripts and the tests use these names as krunner_abbr.X
from krunner_abbr_config import *  # noqa: F401,F403
from krunner_abbr_desktop import *  # noqa: F401,F403
from krunner_abbr_filters import *  # noqa: F401,F403
from krunner_abbr_match import *  # noqa: F401,F403
from krunner_abbr_template import *  # noqa: F401,F403

__version__ = "2.0"

SUBTEXT_MAX = 80


def run_service() -> None:
    import krunner_abbr_service

    krunner_abbr_service.run()


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expand", nargs="+", metavar=("QUERY", "ARG"),
                    help="print the matches for QUERY (as typed in KRunner) and exit")
    ap.add_argument("--list", action="store_true", help="list all abbreviations with their expansion")
    ap.add_argument("--page", choices=["help", "list"],
                    help="write the help or list page and print its path")
    ap.add_argument("--import-espanso", metavar="FILE",
                    help="convert an espanso match file and print the abbreviations")
    ap.add_argument("--write", action="store_true",
                    help="with --import-espanso: append them to your config instead")
    args = ap.parse_args()

    if args.import_espanso:
        import krunner_abbr_import

        krunner_abbr_import.main(args.import_espanso, args.write)
        return

    if args.page:
        import krunner_abbr_help

        cfg = Config(CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_CONFIG)
        cfg.reload_if_changed()
        print(krunner_abbr_help.write_page(args.page, cfg))
        return

    if args.expand or args.list:
        cfg = Config(CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_CONFIG)
        cfg.reload_if_changed()
        if cfg.error:
            sys.exit(cfg.error)
        if args.list:
            for abbr in cfg.abbrs.values():
                if needs_input(abbr["text"]):
                    labels = ", ".join(label for _, label, _ in ask_prompts(abbr["text"]))
                    print(f"{abbr['key']:<12} (asks for: {labels})")
                    continue
                try:
                    text = expand(abbr["text"], timeout=cfg.cmd_timeout)
                except Exception as e:
                    text = f"ERROR: {e}"
                print(f"{abbr['key']:<12} {one_line(text, SUBTEXT_MAX)}")
            return
        query = " ".join(args.expand)
        for word, _ in builtin_matches(cfg, query):
            print(f"[{word}] {BUILTINS[word][1]}")
        for abbr, arg, category in find_matches(cfg, query):
            tag = "exact " if category == HIGHEST else "prefix"
            timeout = LAZY_TIMEOUT if is_lazy(abbr) else cfg.cmd_timeout
            answers = None
            if needs_input(abbr["text"]):
                if category != HIGHEST or not sys.stdin.isatty():
                    print(f"[{tag}] {abbr['key']}: (asks for input)")
                    continue
                answers = collect_answers(abbr["text"], terminal_prompt)
            try:
                text = expand(abbr["text"], arg, timeout, answers=answers)
            except Exception as e:
                text = f"ERROR: {e}"
            print(f"[{tag}] {abbr['key']}: {text}")
        return

    run_service()


if __name__ == "__main__":
    main()

