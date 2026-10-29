# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""The KRunner D-Bus service (org.kde.krunner1)."""

import sys
import threading
import time
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from krunner_abbr_config import CONFIG_FILE, Config, Usage, ensure_config
from krunner_abbr_desktop import (get_clipboard, get_clipboard_history, notify, open_url,
                                  paste_clipboard, set_clipboard)
from krunner_abbr_filters import (FILTERS, deaccent, markdown_to_html, one_line,
                                  parse_filter_chain)
from krunner_abbr_match import (BUILTIN_ID, BUILTINS, CASE_ID, CASE_WORD, CLIP_ID, CLIP_MAX,
                                CLIP_WORD, FILTER_ICON, FILTER_ID, HIGH, HIGHEST, LAZY_TIMEOUT, LOW,
                                apply_case, builtin_matches, case_icon, case_query, case_style,
                                case_variants, chain_query, complete_chain, find_matches,
                                open_settings, run_builtin, run_chain, typed_key, word_query)
from krunner_abbr_template import collect_answers, expand, is_lazy, needs_input, split_cursor

SERVICE = "io.github.nivaca.AbbrKRunner"
OBJPATH = "/runner"
IFACE = "org.kde.krunner1"

# buttons on each match: id -> (text, icon)
ACTIONS = {
    "paste": ("Copy and paste into the previous window", "edit-paste"),
    "copy": ("Copy without pasting", "edit-copy"),  # replaces "paste" when Enter pastes
    "template": ("Copy the template instead of the expansion", "text-x-generic"),
    "edit": ("Edit this abbreviation", "document-edit"),
}
PASTE_DELAY = 0.35  # seconds for KRunner to close and the previous window to get focus
WINDOW_SCRIPT_NAME = "krunner-abbr-active-window"
WINDOW_SCRIPT = Path.home() / ".cache" / "krunner-abbr" / "active-window.js"
WINDOW_JS = f"""const w = workspace.activeWindow;
callDBus("{SERVICE}", "{OBJPATH}", "{SERVICE}", "ReportWindow",
         w ? w.resourceClass + "|" + w.desktopFileName : "");
"""


def notify_copied(abbr: dict, text: str, verb: str = "Copied") -> None:
    body = one_line(text, 300) if text.strip() else abbr.get("description", "")
    notify(f"{verb} “{abbr['key']}”", body, abbr.get("icon", "edit-paste"))


class Runner(dbus.service.Object):
    def __init__(self, cfg: Config, usage: Usage):
        self.cfg, self.usage = cfg, usage
        DBusGMainLoop(set_as_default=True)
        # the Object keeps the BusName alive; storing it ourselves as self._name would be
        # clobbered by Object.__init__, releasing the name immediately
        super().__init__(dbus.service.BusName(SERVICE, dbus.SessionBus()), OBJPATH)
        # expansions shown in the last query, so Run copies exactly what was displayed
        self.shown: dict[str, str] = {}
        self.window_callback = None  # waiting for ReportWindow

    def actions(self, *ids: str):
        """Match property listing which ACTIONS buttons a match shows (none if empty).

        "paste" stands for whichever of paste / copy Enter doesn't already do.
        """
        alt = "copy" if self.cfg.enter_pastes else "paste"
        return dbus.Array([alt if i == "paste" else i for i in ids], signature="s")

    def abbr_actions(self):
        return self.actions("paste", "template", "edit")

    @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})")
    def Match(self, query: str):
        self.cfg.reload_if_changed()
        self.shown = {}
        results = []
        for word, category in builtin_matches(self.cfg, query):
            title, subtext, icon = BUILTINS[word]
            results.append((
                BUILTIN_ID + word, title, icon, category,
                1.0 if category == HIGHEST else 0.4,
                {"subtext": subtext, "category": "Abbreviations", "actions": self.actions()},
            ))
        case_text = case_query(self.cfg, query)
        if case_text is not None:
            results.extend(self.case_results(case_text))
        chain = chain_query(self.cfg, query)
        if chain is not None:
            results.extend(self.chain_results(*chain))
        clip_search = word_query(self.cfg, query, CLIP_WORD)
        if clip_search is not None:
            results.extend(self.clip_results(clip_search))
        matches = find_matches(self.cfg, query)
        scores = {a["key"]: self.usage.score(a["key"]) if self.cfg.rank_by_use else 0.0
                  for a, _, _ in matches}
        top = max(scores.values(), default=0.0) or 1.0
        typed = typed_key(self.cfg, query)
        for abbr, arg, category in matches:
            style = case_style(typed) if abbr.get("propagate_case") and category == HIGHEST else ""
            match_id = f"{abbr['key']}\x1f{arg}\x1f{style}"
            # within a category, used more and more recently ranks higher
            relevance = {HIGHEST: 1.0, HIGH: 0.8, LOW: 0.5}.get(category, 0.3) \
                + 0.09 * scores[abbr["key"]] / top * (category != HIGHEST)
            opens = abbr.get("action") == "open"
            buttons = self.actions("template", "edit") if opens else self.abbr_actions()
            group = abbr.get("group") or "Abbreviations"
            note = "matches the clipboard · " if category == HIGH else ""
            if is_lazy(abbr):  # computed in Run: slow commands, or prompts for input
                label = abbr.get("description") or one_line(abbr["text"], 200)
                hint = ("press Enter, then fill in the prompt" if needs_input(abbr["text"])
                        else "press Enter to open" if opens else "press Enter to run and copy")
                results.append((
                    match_id, f"{label}: {arg}" if arg else label,
                    abbr.get("icon", "system-run"), category, relevance,
                    {"subtext": f"{note}{abbr['key']} · {hint}", "category": group,
                     "actions": buttons},
                ))
                continue
            try:
                text = apply_case(expand(abbr["text"], arg, self.cfg.cmd_timeout, keep_cursor=True),
                                  style)
            except Exception as e:
                results.append((
                    match_id, f"{abbr['key']}: {e}", "dialog-error", category, 0.1,
                    {"subtext": "abbreviation error", "actions": self.actions("edit")},
                ))
                continue
            self.shown[match_id] = text
            about = abbr.get("description") or ("open" if opens else "copy to clipboard")
            subtext = f"{note}{abbr['key']} · {about}" + (" · Enter opens it" if opens else "")
            results.append((
                match_id, one_line(split_cursor(text)[0], 200),
                abbr.get("icon", "internet-web-browser" if opens else "edit-paste"), category,
                relevance, {"subtext": subtext, "category": group, "actions": buttons},
            ))
        return results

    def case_results(self, text: str) -> list:
        """One match per case style of text, or of the clipboard if text is empty."""
        source = text or get_clipboard()
        if not source.strip():
            return [(CASE_ID, "Type some text after “case”, or copy some first",
                     "format-text-capitalize", HIGHEST, 1.0,
                     {"subtext": "every case style of the text", "category": "Change case",
                      "actions": self.actions()})]
        results = []
        for i, (name, result) in enumerate(case_variants(source)):
            match_id = f"{CASE_ID}{name}\x1f{text}"
            self.shown[match_id] = result
            subtext = f"{name} · {FILTERS[name][3]}" + ("" if text else " · from clipboard")
            results.append((
                match_id, one_line(result, 200), case_icon(name), HIGHEST,
                1.0 - i * 0.01,  # keep CASE_FILTERS order
                {"subtext": subtext, "category": "Change case", "actions": self.actions("paste")},
            ))
        return results

    def chain_results(self, chain: str, text: str) -> list:
        """The typed filter chain applied to text or the clipboard, or its completions."""
        def hint(title: str, icon: str = FILTER_ICON) -> list:
            return [(FILTER_ID, title, icon, HIGHEST, 1.0,
                     {"subtext": "|filter|filter(args) [text]; without text, the clipboard",
                      "category": "Filters", "actions": self.actions()})]

        try:
            chains = complete_chain(chain)
        except ValueError as e:
            return hint(str(e), "dialog-error")
        source = text or get_clipboard()
        if not source.strip():
            return hint("Type some text after the filters, or copy some first")
        results = []
        for i, full in enumerate(chains):
            match_id = f"{FILTER_ID}{full}\x1f{text}"
            last = FILTERS[parse_filter_chain(full)[-1][0]][3]
            subtext = f"{full} · {last}" + ("" if text else " · from clipboard")
            if "|sh" in full:  # never run shell commands while typing
                title, icon = "Press Enter to run the shell command", "system-run"
            else:
                try:
                    result = run_chain(full, source)
                except Exception as e:
                    title, icon = str(e) or type(e).__name__, "dialog-error"
                    results.append((FILTER_ID, title, icon, HIGHEST, 0.9 - i * 0.001,
                                    {"subtext": subtext, "category": "Filters",
                                     "actions": self.actions()}))
                    continue
                self.shown[match_id] = result
                title, icon = one_line(result, 200) or "(empty)", FILTER_ICON
            results.append((
                match_id, title, icon, HIGHEST, 1.0 - i * 0.001,  # keep FILTERS order
                {"subtext": subtext, "category": "Filters", "actions": self.actions("paste")},
            ))
        return results

    def clip_results(self, search: str) -> list:
        """Klipper's history, filtered by search (ignoring case and accents)."""
        want = deaccent(search).lower()
        results = []
        for pos, text in enumerate(get_clipboard_history(), 1):
            if not text.strip() or want not in deaccent(text).lower():
                continue
            match_id = f"{CLIP_ID}{pos}"
            self.shown[match_id] = text
            lines = text.count("\n") + 1
            subtext = "current clipboard" if pos == 1 else f"#{pos} in the history"
            if lines > 1:
                subtext += f" · {lines} lines"
            results.append((
                match_id, one_line(text, 200), "klipper", HIGHEST, 1.0 - pos * 0.001,
                {"subtext": subtext, "category": "Clipboard history",
                 "actions": self.actions("paste")},
            ))
            if len(results) == CLIP_MAX:
                break
        if not results:
            results.append((CLIP_ID, "No clipboard history matches" if search
                            else "The clipboard history is empty", "klipper", HIGHEST, 1.0,
                            {"subtext": "clip [search]", "category": "Clipboard history",
                             "actions": self.actions()}))
        return results

    @dbus.service.method(IFACE, out_signature="a(sss)")
    def Actions(self):
        return [(action_id, text, icon) for action_id, (text, icon) in ACTIONS.items()]

    @dbus.service.method(IFACE, in_signature="ss")
    def Run(self, match_id: str, action_id: str):
        if match_id.startswith(BUILTIN_ID):
            run_builtin(self.cfg, match_id[len(BUILTIN_ID):])
            return
        # Enter (no action) pastes or copies per the setting; the buttons do the other
        paste = action_id == "paste" or (not action_id and self.cfg.enter_pastes)
        if match_id.startswith(CASE_ID):
            name, sep, typed = match_id[len(CASE_ID):].partition("\x1f")
            if not sep or name not in FILTERS:
                return  # the "copy some text first" hint
            text = self.shown.get(match_id)
            if text is None:  # stale id: recompute
                text = FILTERS[name][0](typed or get_clipboard())
            self.deliver({"key": f"{CASE_WORD} {name}", "icon": case_icon(name)}, text, paste)
            return
        if match_id.startswith(CLIP_ID):
            text = self.shown.get(match_id)
            if text is not None:  # copying makes it the current clipboard entry again
                self.deliver({"key": "clipboard history", "icon": "klipper"}, text, paste)
            return
        if match_id.startswith(FILTER_ID):
            chain, sep, typed = match_id[len(FILTER_ID):].partition("\x1f")
            if not sep:
                return  # a hint or an error
            text = self.shown.get(match_id)
            if text is None:  # shell commands, or a stale id
                self.run_chain_lazy(chain, typed or get_clipboard(), paste)
                return
            self.deliver({"key": chain, "icon": FILTER_ICON}, text, paste)
            return
        key, arg, style = (match_id.split("\x1f", 2) + ["", ""])[:3]
        abbr = self.cfg.abbrs.get(key.lower())
        if action_id == "edit":
            open_settings(abbr["key"] if abbr else key)
            return
        if action_id == "template" and abbr is not None:
            set_clipboard(abbr["text"])
            if self.cfg.notify:
                notify(f"Copied the template of “{abbr['key']}”", one_line(abbr["text"], 300),
                       abbr.get("icon", "edit-copy"))
            return
        text = self.shown.get(match_id)
        if abbr is None and text is None:
            return
        if abbr is not None and is_lazy(abbr):
            self.run_lazy(abbr, arg, paste, style)
            return
        if text is None:  # stale id: recompute
            try:
                text = apply_case(expand(abbr["text"], arg, self.cfg.cmd_timeout, keep_cursor=True),
                                  style)
            except Exception as e:
                notify(f"“{abbr['key']}” failed", str(e), "dialog-error")
                return
        self.deliver(abbr or {"key": key}, text, paste)

    def deliver(self, abbr: dict, text: str, paste: bool, always_notify: bool = False):
        """Copy text, then paste it into the previous window if asked.

        Formatted abbreviations are copied as HTML, except into terminals. Pasting
        uses the keys configured for the window that gets the focus back, then moves
        the cursor to {cursor}.
        """
        text, back = split_cursor(text)
        if "text" in abbr:  # a configured abbreviation, not a case/filter/history result
            self.usage.record(abbr["key"])
        if abbr.get("action") == "open":
            open_url(text)
            return
        fmt = abbr.get("format", "")
        html_text = markdown_to_html(text) if fmt == "markdown" else \
            text if fmt == "html" else None
        if not paste:
            set_clipboard(text, html_text)
            if self.cfg.notify or always_notify:
                notify_copied(abbr, text)
            return

        def with_window(apps: list[str]):
            keys = next((self.cfg.paste_keys[a] for a in apps if a in self.cfg.paste_keys), "ctrl+v")
            rich = html_text is not None and keys == "ctrl+v"  # terminals get plain text
            set_clipboard(text, html_text if rich else None)
            threading.Thread(target=work, args=(keys, 0 if rich else back),
                             daemon=True).start()

        def work(keys: str, back: int):
            time.sleep(0.05)  # let the clipboard owner settle
            try:
                paste_clipboard(keys, back)
                error = None
            except Exception as e:
                error = e
            GLib.idle_add(done, error)

        def done(error):
            if error is not None:
                notify("Copied, but could not paste", str(error), "dialog-warning")
            elif self.cfg.notify or always_notify:
                notify_copied(abbr, text, "Pasted")
            return False

        # wait for KRunner to close, so the previous window has the focus again
        GLib.timeout_add(int(PASTE_DELAY * 1000), lambda: self.active_window(with_window))

    def active_window(self, callback) -> bool:
        """Ask KWin for the focused window; callback gets its lowercase app ids."""
        def finish(apps: list[str]) -> bool:
            if self.window_callback is callback:
                self.window_callback = None
                callback(apps)
            return False

        self.window_callback = callback
        try:
            WINDOW_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
            WINDOW_SCRIPT.write_text(WINDOW_JS)
            scripting = dbus.Interface(dbus.SessionBus().get_object("org.kde.KWin", "/Scripting"),
                                       "org.kde.kwin.Scripting")
            scripting.unloadScript(WINDOW_SCRIPT_NAME)
            script_id = scripting.loadScript(str(WINDOW_SCRIPT), WINDOW_SCRIPT_NAME)
            dbus.Interface(dbus.SessionBus().get_object("org.kde.KWin", f"/Scripting/Script{script_id}"),
                           "org.kde.kwin.Script").run()
            GLib.timeout_add(500, finish, [])  # no answer: paste with Ctrl+V
        except Exception as e:  # not KWin: paste with Ctrl+V
            print(f"active window unknown: {e}", file=sys.stderr)
            finish([])
        return False

    @dbus.service.method(SERVICE, in_signature="s")
    def ReportWindow(self, ids: str):
        """Called back by WINDOW_JS with "resourceClass|desktopFileName"."""
        try:
            dbus.Interface(dbus.SessionBus().get_object("org.kde.KWin", "/Scripting"),
                           "org.kde.kwin.Scripting").unloadScript(WINDOW_SCRIPT_NAME)
        except Exception:
            pass
        if self.window_callback is not None:
            callback, self.window_callback = self.window_callback, None
            callback([i.lower() for i in str(ids).split("|") if i])

    def run_chain_lazy(self, chain: str, source: str, paste: bool):
        """Apply chain in a thread, so shell commands don't block KRunner's queries."""
        def work():
            try:
                text, error = run_chain(chain, source), None
            except Exception as e:
                text, error = None, e
            GLib.idle_add(finish, text, error)

        def finish(text, error):
            if error is not None:
                notify(f"“{chain}” failed", str(error), "dialog-error")
            else:  # always notify: the result wasn't shown in KRunner
                self.deliver({"key": chain, "icon": FILTER_ICON}, text, paste,
                             always_notify=True)
            return False

        threading.Thread(target=work, daemon=True).start()

    def run_lazy(self, abbr: dict, arg: str, paste: bool = False, style: str = ""):
        """Expand in a thread so slow commands don't block KRunner's queries."""
        clipboard = get_clipboard()  # D-Bus calls stay on the main thread

        def work():
            try:
                answers = collect_answers(abbr["text"],
                                          title=f"Abbreviations · {abbr['key']}")
                if answers is None:
                    return  # prompt cancelled: copy nothing
                text, error = apply_case(expand(abbr["text"], arg, LAZY_TIMEOUT, clipboard,
                                                answers, keep_cursor=True), style), None
            except Exception as e:
                text, error = None, e
            GLib.idle_add(finish, text, error)

        def finish(text, error):
            if error is not None:
                notify(f"“{abbr['key']}” failed", str(error), "dialog-error")
            elif not text.strip():
                notify(f"“{abbr['key']}” produced no output", "Nothing was copied.",
                       "dialog-warning")
            else:  # always notify: the result wasn't shown in KRunner
                self.deliver(abbr, text, paste, always_notify=True)
            return False

        threading.Thread(target=work, daemon=True).start()

    @dbus.service.method(IFACE)
    def Teardown(self):
        self.shown = {}


def run() -> None:
    ensure_config()
    Runner(Config(CONFIG_FILE), Usage())
    GLib.MainLoop().run()
