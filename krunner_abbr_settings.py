#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Settings dialog for krunner-abbr: trigger keyword, matching options and abbreviations.

Writes ~/.config/krunner-abbr/abbreviations.toml, which the runner reloads on the
next keystroke in KRunner.
"""

import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections import Counter

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QSplitter, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

import krunner_abbr as k
from krunner_abbr_form import run_form

KEY_RE = re.compile(r"^\S+$")
# list items: the index in SettingsDialog.entries (None on a group header), and the group
ENTRY_ROLE = Qt.UserRole + 1
GROUP_ROLE = Qt.UserRole + 2


def entry_problems(e: dict, key_counts: Counter) -> dict[str, str]:
    """What stops e from being saved, by field (key, text, when); key_counts counts the
    lowercase names of all abbreviations, to find the ones defined twice."""
    problems = {}
    key = e["key"]
    if not key:
        problems["key"] = "An abbreviation has no name."
    elif not KEY_RE.match(key):
        problems["key"] = f"“{key}” contains spaces; abbreviations must be one word."
    elif key_counts[key.lower()] > 1:
        problems["key"] = f"“{key}” is defined twice (names are case-insensitive)."
    if not e["text"]:
        problems["text"] = f"“{key}” has an empty expansion."
    try:
        re.compile(e.get("when") or "")
    except re.error as ex:
        problems["when"] = f"“{key}”: the “Suggest when” pattern is not valid: {ex}"
    return problems

# (label, text inserted at the cursor)
PLACEHOLDERS = [
    ("Date (ISO)", "{date}"),
    ("Time", "{time}"),
    ("Date and time (ISO)", "{now}"),
    ("Custom date format", "{date:%d/%m/%Y}"),
    ("Tomorrow", "{date+1d}"),
    ("Date in Spanish", "{date@es:%A %d de %B de %Y}"),
    ("Next month", "{date+1mo}"),
    (None, None),
    ("Typed argument", "{arg}"),
    ("Typed argument with default", "{arg:default}"),
    (None, None),
    ("Clipboard contents", "{clipboard}"),
    ("Previous clipboard entry", "{clipboard:2}"),
    ("Typed argument, else clipboard", "{input}"),
    ("Ask for input (dialog)", "{ask:Label}"),
    ("Ask for multi-line text (dialog)", "{asktext:Label}"),
    ("Choose from a list (dialog)", "{choose:Label=one, two, three}"),
    ("Optional section (checkbox)", "{opt:Label}text{/opt}"),
    ("Pick a date (dialog)", "{askdate:Label|datefmt(%d/%m/%Y)}"),
    ("Selected text", "{selection}"),
    ("First / second typed word", "{arg1} {arg2}"),
    ("Random choice", "{random:one, two, three}"),
    ("Calculation", "{calc:2*(3+4)}"),
    ("Cursor position after pasting", "{cursor}"),
    ("Another abbreviation", "{abbr:name}"),
    ("Random UUID", "{uuid}"),
    ("Environment variable", "{env:USER}"),
    ("Shell command output", "{cmd:echo hello}"),
    (None, None),
    ("Literal {", "{{"),
    ("Literal }", "}}"),
]


# ---------------------------------------------------------------- TOML I/O


def load_config(path) -> tuple[dict, list[dict]]:
    data = tomllib.loads(path.read_text())
    settings = data.get("settings", {})
    settings["paste_keys"] = data.get("paste_keys", {})
    entries = []
    for key, val in data.get("abbr", {}).items():
        if isinstance(val, str):
            val = {"text": val}
        entries.append({
            "key": key,
            "text": val.get("text", ""),
            "description": val.get("description", ""),
            "icon": val.get("icon", ""),
            "lazy": bool(val.get("lazy", False)),
            "keywords": " ".join(k.as_list(val.get("keywords"))),
            "format": val.get("format", ""),
            "aliases": " ".join(k.as_list(val.get("aliases"))),
            "group": val.get("group", ""),
            "action": val.get("action", ""),
            "when": val.get("when", ""),
            "propagate_case": bool(val.get("propagate_case", False)),
        })
    return settings, entries


toml_str, toml_key = k.toml_str, k.toml_key


def doc_header() -> list[str]:
    """Leading comment block of the bundled example config (placeholder reference)."""
    lines = []
    try:
        for line in k.EXAMPLE_CONFIG.read_text().splitlines():
            if not line.startswith("#"):
                break
            lines.append(line)
    except OSError:
        pass
    return lines


def dump_config(settings: dict, entries: list[dict]) -> str:
    out = doc_header() + [
        "# Written by krunner_abbr_settings.py; comments below this header are not preserved.",
        "",
        "[settings]",
        f"prefix = {toml_str(settings['prefix'])}",
        f"min_prefix_chars = {settings['min_prefix_chars']}",
        f"cmd_timeout = {settings['cmd_timeout']}",
        f"notify = {'true' if settings['notify'] else 'false'}",
        f"enter_pastes = {'true' if settings['enter_pastes'] else 'false'}",
        f"rank_by_use = {'true' if settings.get('rank_by_use', True) else 'false'}",
        "disabled_groups = [" + ", ".join(toml_str(g) for g in settings.get("disabled_groups", [])) + "]",
    ]
    if settings.get("paste_keys"):
        out += ["", "[paste_keys]"]
        out += [f"{toml_key(app)} = {toml_str(keys)}" for app, keys in settings["paste_keys"].items()]
    out += ["", "[abbr]"]
    # plain key = value pairs must precede sub-tables in TOML
    extras = ("description", "icon", "lazy", "keywords", "format", "aliases", "group", "action",
              "when", "propagate_case")
    simple = [e for e in entries if not any(e.get(f) for f in extras)]
    tables = [e for e in entries if any(e.get(f) for f in extras)]
    out += [f"{toml_key(e['key'])} = {toml_str(e['text'])}" for e in simple]
    for e in tables:
        out += ["", f"[abbr.{toml_key(e['key'])}]", f"text = {toml_str(e['text'])}"]
        if e["description"]:
            out.append(f"description = {toml_str(e['description'])}")
        if e["icon"]:
            out.append(f"icon = {toml_str(e['icon'])}")
        if e["lazy"]:
            out.append("lazy = true")
        if e.get("keywords"):
            out.append(f"keywords = {toml_str(e['keywords'])}")
        if e.get("format"):
            out.append(f"format = {toml_str(e['format'])}")
        if e.get("aliases"):
            out.append("aliases = [" + ", ".join(toml_str(a) for a in k.as_list(e["aliases"])) + "]")
        for name in ("group", "action", "when"):
            if e.get(name):
                out.append(f"{name} = {toml_str(e[name])}")
        if e.get("propagate_case"):
            out.append("propagate_case = true")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- dialog


def expanding(label: QLabel) -> QLabel:
    """Let a word-wrapped label take the whole width of a form row.

    Breeze's form layouts only widen fields that ask for it; a wrapped label left at its
    narrow size hint gets too little height, and its text runs into the next row.
    """
    policy = label.sizePolicy()  # keeps heightForWidth, which wrapping needs
    policy.setHorizontalPolicy(QSizePolicy.Expanding)
    label.setSizePolicy(policy)
    return label


class EntryDelegate(QStyledItemDelegate):
    """List row: the abbreviation, then its description (Qt.UserRole) in muted text; or a
    group header, in bold muted text."""

    GAP = 12

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        if index.data(ENTRY_ROLE) is None:  # a group header
            painter.save()
            font = QFont(opt.font)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(opt.palette.color(QPalette.Active, QPalette.PlaceholderText))
            painter.drawText(opt.rect.adjusted(4, 0, -4, 0), Qt.AlignBottom | Qt.AlignLeft,
                             opt.text)
            painter.restore()
            return
        key, desc = opt.text, index.data(Qt.UserRole) or ""
        opt.text = ""
        style = opt.widget.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget).adjusted(2, 0, -4, 0)

        # align descriptions in a column after the widest key (capped at 45% of the row)
        fm = opt.fontMetrics
        model = index.model()
        widest = max((fm.horizontalAdvance(model.index(r, 0).data() or "")
                      for r in range(model.rowCount())
                      if model.index(r, 0).data(ENTRY_ROLE) is not None), default=0)
        key_width = min(widest, int(rect.width() * 0.45))

        selected = bool(opt.state & QStyle.State_Selected)
        group = QPalette.Active if opt.state & QStyle.State_Active else QPalette.Inactive
        painter.save()
        painter.setPen(opt.palette.color(group, QPalette.HighlightedText if selected else QPalette.Text))
        key_rect = rect.adjusted(0, 0, -(rect.width() - key_width), 0)
        painter.drawText(key_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         fm.elidedText(key, Qt.ElideRight, key_rect.width()))
        if desc:
            desc_rect = rect.adjusted(key_width + self.GAP, 0, 0, 0)
            muted = QPalette.HighlightedText if selected else QPalette.PlaceholderText
            painter.setPen(opt.palette.color(group, muted))
            painter.drawText(desc_rect, Qt.AlignVCenter | Qt.AlignLeft,
                             fm.elidedText(desc, Qt.ElideRight, desc_rect.width()))
        painter.restore()



class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Abbreviations Settings")
        self.setWindowIcon(QIcon.fromTheme("edit-paste"))
        self.resize(940, 600)
        self.path = k.CONFIG_FILE
        self.entries: list[dict] = []
        self.removed: list[tuple[int, dict]] = []  # (index, entry), for Undo
        self.warnings: dict[QLineEdit, QAction] = {}  # the warning icon in each field
        self.dirty = False
        self.loading = False

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), QIcon.fromTheme("configure"), "General")
        tabs.addTab(self._build_abbr_tab(), QIcon.fromTheme("format-text-code"), "Abbreviations")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel
        )
        open_btn = self.buttons.addButton("Open Config File", QDialogButtonBox.ResetRole)
        open_btn.setIcon(QIcon.fromTheme("document-open"))
        open_btn.clicked.connect(self.open_file)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.Apply).clicked.connect(self.save)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(self.buttons)

        self.preview_timer = QTimer(self, singleShot=True, interval=300)
        self.preview_timer.timeout.connect(self.update_preview)

        self.load()

    # ---- UI construction

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.prefix_edit = QLineEdit(placeholderText="(none — react to any input)")
        self.prefix_edit.textChanged.connect(self._general_changed)
        form.addRow("Trigger keyword:", self.prefix_edit)
        form.addRow("", self._hint(
            "Only handle KRunner queries that start with this keyword, e.g. "
            "<code>ex isod</code>. A word keyword must be followed by a space; a "
            "symbol such as <code>;</code> need not be. The keyword alone lists all "
            "abbreviations, and <code>ex settings</code> opens this dialog. Leave "
            "empty to match abbreviations directly."
        ))
        self.trigger_example = QLabel()
        form.addRow("", self.trigger_example)

        self.min_prefix_spin = QSpinBox(minimum=0, maximum=20)
        self.min_prefix_spin.setSpecialValueText("Off (exact matches only)")
        self.min_prefix_spin.setSuffix(" characters")
        self.min_prefix_spin.valueChanged.connect(self._general_changed)
        form.addRow("Suggest partial matches after:", self.min_prefix_spin)
        form.addRow("", self._hint(
            "Also offer abbreviations that start with what you typed (e.g. <code>tm</code> "
            "suggests <code>tmrw</code>), ranked below exact matches."
        ))

        self.timeout_spin = QDoubleSpinBox(minimum=0.1, maximum=30, singleStep=0.5, decimals=1)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.valueChanged.connect(self._general_changed)
        form.addRow("Shell command timeout:", self.timeout_spin)
        form.addRow("", self._hint(
            "Maximum run time for <code>{cmd:…}</code> placeholders. Commands run on "
            "every keystroke that matches, so keep them fast."
        ))

        self.notify_check = QCheckBox("Show a notification after copying")
        self.notify_check.toggled.connect(self._general_changed)
        form.addRow("Notifications:", self.notify_check)
        form.addRow("", self._hint(
            "Abbreviations computed on Enter, and errors, always notify, since their "
            "result isn't shown in KRunner."
        ))

        self.enter_pastes_check = QCheckBox("Paste into the previous window")
        self.enter_pastes_check.toggled.connect(self._general_changed)
        form.addRow("Enter:", self.enter_pastes_check)
        form.addRow("", self._hint(
            "Enter copies the expansion and presses Ctrl+V in the window you came from; "
            "a button on each match copies without pasting. The first time, Plasma asks "
            "permission to control the keyboard; if it can't, <code>ydotool</code> is used."
        ))

        self.rank_check = QCheckBox("Rank abbreviations you use often and recently higher")
        self.rank_check.toggled.connect(self._general_changed)
        form.addRow("Ranking:", self.rank_check)
        self.disabled_groups_edit = QLineEdit(placeholderText="e.g. LaTeX, Symbols")
        self.disabled_groups_edit.textChanged.connect(self._general_changed)
        form.addRow("Disabled groups:", self.disabled_groups_edit)
        form.addRow("", self._hint(
            "Abbreviations in these groups (comma-separated) are not offered in KRunner; "
            "other abbreviations can still include them with <code>{abbr:name}</code>."
        ))
        return w

    def _build_abbr_tab(self) -> QWidget:
        # left: filter + list + add/remove
        self.filter_edit = QLineEdit(placeholderText="Filter…", clearButtonEnabled=True)
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.list = QListWidget()
        self.list.setItemDelegate(EntryDelegate(self.list))
        self.list.currentRowChanged.connect(self.show_entry)

        add_btn = QPushButton(QIcon.fromTheme("list-add"), "Add")
        add_btn.clicked.connect(self.add_entry)
        dup_btn = QPushButton(QIcon.fromTheme("edit-copy"), "Duplicate")
        dup_btn.clicked.connect(self.duplicate_entry)
        self.remove_btn = QPushButton(QIcon.fromTheme("list-remove"), "Remove")
        self.remove_btn.clicked.connect(self.remove_entry)
        self.undo_btn = QPushButton(QIcon.fromTheme("edit-undo"), "Undo")
        self.undo_btn.clicked.connect(self.undo_remove)
        del_action = QAction(self.list, shortcut=QKeySequence.Delete,
                             shortcutContext=Qt.WidgetShortcut)
        del_action.triggered.connect(self.remove_entry)
        self.list.addAction(del_action)
        # only in the list: in the fields, Ctrl+Z undoes typing
        undo_action = QAction(self.list, shortcut=QKeySequence.Undo,
                              shortcutContext=Qt.WidgetShortcut)
        undo_action.triggered.connect(self.undo_remove)
        self.list.addAction(undo_action)

        btns = QHBoxLayout()
        for b in (add_btn, dup_btn, self.remove_btn, self.undo_btn):
            btns.addWidget(b)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.filter_edit)
        lv.addWidget(self.list)
        lv.addLayout(btns)

        # right: editor
        self.key_edit = QLineEdit()
        self.desc_edit = QLineEdit(placeholderText="optional, shown under the result")
        self.keywords_edit = QLineEdit(placeholderText="optional words that also find it, e.g. fecha hoy")
        self.aliases_edit = QLineEdit(placeholderText="optional other names, e.g. fh hoy")
        self.group_edit = QLineEdit(placeholderText="optional, e.g. LaTeX; shown as KRunner's category")
        self.action_combo = QComboBox()
        self.action_combo.addItem("Copy (or paste) the expansion", "")
        self.action_combo.addItem("Open it (a link, file or folder)", "open")
        self.when_edit = QLineEdit(placeholderText=r"optional regex, e.g. ^https?:// or 10\.\d{4,}/")
        self.when_edit.setToolTip("When the clipboard matches, typing just the keyword lists "
                                  "this abbreviation first.")
        self.case_check = QCheckBox("Match the case typed: Sig → Capitalized, SIG → UPPERCASE")
        self.format_combo = QComboBox()
        for label, value in (("Plain text", ""), ("Markdown → formatted text", "markdown"),
                             ("HTML → formatted text", "html")):
            self.format_combo.addItem(label, value)
        self.format_combo.setToolTip(
            "Formatted abbreviations are copied as rich text (bold, italics, links, lists) "
            "for mail, office and web editors; terminals get the plain text when pasting.")
        self.icon_edit = QLineEdit(placeholderText="edit-paste")
        self.icon_preview = QLabel()
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon_edit)
        icon_row.addWidget(self.icon_preview)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setTabChangesFocus(True)
        insert_btn = QToolButton(text="Insert Placeholder", icon=QIcon.fromTheme("insert-text"))
        insert_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        insert_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(insert_btn)
        for label, snippet in PLACEHOLDERS:
            if label is None:
                menu.addSeparator()
            else:
                menu.addAction(f"{label}\t{snippet}", lambda s=snippet: self.insert_placeholder(s))
        menu.addSeparator()
        filters = menu.addMenu("Filters")
        filters.setToolTipsVisible(True)
        for name, (_, min_args, _, desc) in k.FILTERS.items():
            # for required arguments, insert the call shown in the description, e.g. "replace(old, new)"
            call = re.search(rf"\b{name}\([^)]*\)", desc) if min_args else None
            snippet = "|" + (call[0] if call else name)
            filters.addAction(f"{desc}\t{snippet}", lambda s=snippet: self.insert_filter(s))
        insert_btn.setMenu(menu)

        self.arg_edit = QLineEdit(placeholderText="text typed after the abbreviation")
        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setMaximumHeight(90)

        self.editor = QGroupBox("Abbreviation")
        form = QFormLayout(self.editor)
        form.addRow("Abbreviation:", self.key_edit)
        # what would stop saving, shown while editing instead of only on Save
        self.problem_label = expanding(QLabel(wordWrap=True))
        self.problem_label.setStyleSheet("color: palette(link-visited);")
        form.addRow("", self.problem_label)
        form.addRow("Description:", self.desc_edit)
        form.addRow("Keywords:", self.keywords_edit)
        form.addRow("Aliases:", self.aliases_edit)
        form.addRow("Group:", self.group_edit)
        form.addRow("Icon:", icon_row)
        self.lazy_check = QCheckBox("Compute only when Enter is pressed")
        self.lazy_check.setToolTip(
            "For slow or networked commands: KRunner shows the description instead of a "
            f"live preview, and the result is copied (with a notification) when you press "
            f"Enter. Commands may then run for up to {k.LAZY_TIMEOUT:.0f} s."
        )
        form.addRow("", self.lazy_check)
        form.addRow("Format:", self.format_combo)
        form.addRow("Enter:", self.action_combo)
        form.addRow("Suggest when:", self.when_edit)
        form.addRow("", self.case_check)
        text_label = QWidget()
        tl = QVBoxLayout(text_label)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("Expansion:"))
        tl.addWidget(insert_btn)
        tl.addStretch()
        form.addRow(text_label, self.text_edit)
        form.addRow("Test argument:", self.arg_edit)
        self.run_now_btn = QPushButton(QIcon.fromTheme("system-run"), "Run Now")
        self.run_now_btn.clicked.connect(lambda: self.update_preview(force=True))
        preview_label = QWidget()
        pl = QVBoxLayout(preview_label)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel("Preview:"))
        pl.addWidget(self.run_now_btn)
        pl.addStretch()
        form.addRow(preview_label, self.preview)

        self.key_edit.textChanged.connect(self._entry_changed)
        self.desc_edit.textChanged.connect(self._entry_changed)
        self.icon_edit.textChanged.connect(self._entry_changed)
        self.lazy_check.toggled.connect(self._entry_changed)
        self.keywords_edit.textChanged.connect(self._entry_changed)
        for w in (self.aliases_edit, self.group_edit, self.when_edit):
            w.textChanged.connect(self._entry_changed)
        self.action_combo.currentIndexChanged.connect(self._entry_changed)
        self.case_check.toggled.connect(self._entry_changed)
        self.format_combo.currentIndexChanged.connect(self._entry_changed)
        self.text_edit.textChanged.connect(self._entry_changed)
        self.arg_edit.textChanged.connect(lambda: self.preview_timer.start())
        self.group_edit.editingFinished.connect(self._group_edited)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 520])
        return splitter

    @staticmethod
    def _hint(html: str) -> QLabel:
        label = QLabel(html, wordWrap=True)
        label.setEnabled(False)  # renders in the theme's dimmed text colour
        return expanding(label)

    # ---- load / save

    def load(self):
        src = self.path if self.path.exists() else k.EXAMPLE_CONFIG
        try:
            settings, self.entries = load_config(src)
        except (OSError, tomllib.TOMLDecodeError) as e:
            QMessageBox.critical(self, "Cannot read configuration", f"{src}\n\n{e}")
            settings, self.entries = {}, []
        self.paste_keys = settings.get("paste_keys", {})  # no editor: kept as loaded
        self.loading = True
        self.prefix_edit.setText(settings.get("prefix", k.DEFAULT_PREFIX))
        self.min_prefix_spin.setValue(int(settings.get("min_prefix_chars", 2)))
        self.timeout_spin.setValue(float(settings.get("cmd_timeout", k.CMD_TIMEOUT)))
        self.notify_check.setChecked(bool(settings.get("notify", True)))
        self.enter_pastes_check.setChecked(bool(settings.get("enter_pastes", False)))
        self.rank_check.setChecked(bool(settings.get("rank_by_use", True)))
        self.disabled_groups_edit.setText(", ".join(k.as_list(settings.get("disabled_groups", []))))
        self.loading = False
        self.removed.clear()
        self._update_undo()
        self.rebuild_list(None)  # selects the first abbreviation
        self._update_general()
        self.set_dirty(src != self.path)  # loaded from the example: not saved yet

    def validate(self) -> str | None:
        counts = self.key_counts()
        for e in self.entries:
            if problems := entry_problems(e, counts):
                return self._fail(e, next(iter(problems.values())))
        return self.prefix_problem() or None

    def select_key(self, key: str) -> None:
        """Show abbreviation key (case-insensitive), if it exists."""
        for e in self.entries:
            if e["key"].lower() == key.lower():
                self.filter_edit.clear()
                self.list.setCurrentItem(self.entry_item(e))
                return

    def _fail(self, e: dict, msg: str) -> str:
        self.filter_edit.clear()
        self.list.setCurrentItem(self.entry_item(e))
        return msg

    def save(self) -> bool:
        error = self.validate()
        if error:
            QMessageBox.warning(self, "Cannot save", error)
            return False
        settings = {
            "prefix": self.prefix_edit.text().strip(),
            "min_prefix_chars": self.min_prefix_spin.value(),
            "cmd_timeout": round(self.timeout_spin.value(), 1),
            "notify": self.notify_check.isChecked(),
            "enter_pastes": self.enter_pastes_check.isChecked(),
            "rank_by_use": self.rank_check.isChecked(),
            "disabled_groups": [g.strip() for g in self.disabled_groups_edit.text().split(",")
                                if g.strip()],
            "paste_keys": self.paste_keys,
        }
        text = dump_config(settings, self.entries)
        tomllib.loads(text)  # never write a file the runner can't read
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(".toml.bak"))
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text(text)
        os.replace(tmp, self.path)
        self.set_dirty(False)
        return True

    def accept(self):
        if not self.dirty or self.save():
            super().accept()

    def reject(self):
        if self.dirty:
            answer = QMessageBox.question(
                self, "Unsaved changes", "Save your changes before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            )
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.Save and not self.save():
                return
        super().reject()

    def open_file(self):
        if self.dirty and QMessageBox.question(
            self, "Unsaved changes", "Save changes first so the file is up to date?"
        ) == QMessageBox.Yes:
            if not self.save():
                return
        subprocess.Popen(["xdg-open", str(self.path)], start_new_session=True)

    def set_dirty(self, dirty: bool):
        self.dirty = dirty
        self.buttons.button(QDialogButtonBox.Apply).setEnabled(dirty)
        self.setWindowTitle(("* " if dirty else "") + "Abbreviations Settings")

    # ---- general tab

    def _general_changed(self):
        if not self.loading:
            self.set_dirty(True)
        self._update_general()

    def _update_general(self):
        self._update_trigger_example()
        self._set_warning(self.prefix_edit, self.prefix_problem())

    def prefix_problem(self) -> str:
        if any(c.isspace() for c in self.prefix_edit.text().strip()):
            return "The trigger keyword cannot contain spaces."
        return ""

    def _update_trigger_example(self):
        prefix = self.prefix_edit.text().strip()
        key = self.entries[0]["key"] if self.entries else "isod"
        sep = " " if prefix[-1:].isalnum() else ""
        self.trigger_example.setText(f"In KRunner, type: <b><code>{prefix}{sep}{key}</code></b>")

    # ---- abbreviation list

    def rebuild_list(self, select: dict | None):
        """Fill the list from self.entries and show select (else the first one shown).

        When some abbreviations have a group, they are listed by group under headers,
        with the ones without a group last; otherwise in the order of the file.
        """
        groups = [e.get("group", "").strip() for e in self.entries]
        order = list(range(len(self.entries)))
        grouped = any(groups)
        if grouped:
            order.sort(key=lambda i: (not groups[i], groups[i].lower()))
        counts = Counter(groups)
        self.list.blockSignals(True)
        self.list.clear()
        header = None
        for i in order:
            if grouped and groups[i] != header:
                header = groups[i]
                item = QListWidgetItem(f"{header or 'No group'}  ({counts[header]})")
                item.setFlags(Qt.NoItemFlags)
                item.setData(GROUP_ROLE, header)
                self.list.addItem(item)
            item = QListWidgetItem()
            item.setData(ENTRY_ROLE, i)
            item.setData(GROUP_ROLE, groups[i])
            self.list.addItem(item)
        self.refresh_items()
        self.apply_filter(self.filter_edit.text())
        target = self.entry_item(select) if select is not None else None
        if target is None or target.isHidden():
            target = next((item for item, _ in self.entry_items() if not item.isHidden()), None)
        self.list.setCurrentItem(target)
        self.list.blockSignals(False)
        self.show_entry()

    def entry_items(self):
        """(item, entry) for each abbreviation in the list, in the order shown."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            if (i := item.data(ENTRY_ROLE)) is not None:
                yield item, self.entries[i]

    def entry_item(self, e: dict) -> QListWidgetItem | None:
        return next((item for item, x in self.entry_items() if x is e), None)

    def key_counts(self) -> Counter:
        return Counter(e["key"].lower() for e in self.entries)

    def refresh_items(self):
        """Names, icons and descriptions in the list; a warning icon on the ones with problems."""
        counts = self.key_counts()
        for item, e in self.entry_items():
            problems = entry_problems(e, counts)
            item.setText(e["key"] or "(unnamed)")
            item.setIcon(QIcon.fromTheme("dialog-warning" if problems else e["icon"] or "edit-paste"))
            item.setData(Qt.UserRole, e["description"])
            item.setToolTip("\n".join(problems.values()) or e["description"]
                            or k.one_line(e["text"], 80))

    def _set_warning(self, edit: QLineEdit, problem: str):
        """Show a warning icon at the end of edit, with problem as its tooltip ("" hides it)."""
        if edit not in self.warnings:
            self.warnings[edit] = edit.addAction(QIcon.fromTheme("dialog-warning"),
                                                 QLineEdit.TrailingPosition)
        self.warnings[edit].setVisible(bool(problem))
        self.warnings[edit].setToolTip(problem)

    def show_problems(self):
        e = self.current()
        problems = entry_problems(e, self.key_counts()) if e else {}
        self._set_warning(self.key_edit, problems.get("key", ""))
        self._set_warning(self.when_edit, problems.get("when", ""))
        self.problem_label.setText("\n".join(problems.values()))
        self.problem_label.setVisible(bool(problems))

    def current(self) -> dict | None:
        item = self.list.currentItem()
        i = item.data(ENTRY_ROLE) if item is not None else None
        return self.entries[i] if i is not None else None

    def show_entry(self, *_):
        e = self.current()
        self.editor.setEnabled(e is not None)
        self.remove_btn.setEnabled(e is not None)
        self.loading = True
        self.key_edit.setText(e["key"] if e else "")
        self.desc_edit.setText(e["description"] if e else "")
        self.icon_edit.setText(e["icon"] if e else "")
        self.lazy_check.setChecked(bool(e and e["lazy"]))
        self.keywords_edit.setText(e.get("keywords", "") if e else "")
        self.aliases_edit.setText(e.get("aliases", "") if e else "")
        self.group_edit.setText(e.get("group", "") if e else "")
        self.when_edit.setText(e.get("when", "") if e else "")
        self.action_combo.setCurrentIndex(max(0, self.action_combo.findData(e.get("action", "") if e else "")))
        self.case_check.setChecked(bool(e and e.get("propagate_case")))
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(e.get("format", "") if e else "")))
        self.text_edit.setPlainText(e["text"] if e else "")
        self.loading = False
        self._update_icon_preview()
        self.show_problems()
        self.update_preview()

    def _entry_changed(self):
        if self.loading or (e := self.current()) is None:
            return
        e["key"] = self.key_edit.text().strip()
        e["description"] = self.desc_edit.text()
        e["icon"] = self.icon_edit.text().strip()
        e["lazy"] = self.lazy_check.isChecked()
        e["keywords"] = self.keywords_edit.text().strip()
        e["aliases"] = self.aliases_edit.text().strip()
        e["group"] = self.group_edit.text().strip()
        e["when"] = self.when_edit.text().strip()
        e["action"] = self.action_combo.currentData()
        e["propagate_case"] = self.case_check.isChecked()
        e["format"] = self.format_combo.currentData()
        e["text"] = self.text_edit.toPlainText()
        self.refresh_items()  # all of them: a changed name can make or end a duplicate
        self.show_problems()
        self._update_icon_preview()
        self._update_trigger_example()
        self.set_dirty(True)
        self.preview_timer.start()

    def _update_icon_preview(self):
        self.icon_preview.setPixmap(
            QIcon.fromTheme(self.icon_edit.text().strip() or "edit-paste").pixmap(22, 22)
        )

    def _group_edited(self):
        """Move the abbreviation under its new group header, once the group is typed."""
        e, item = self.current(), self.list.currentItem()
        if e is not None and e.get("group", "") != item.data(GROUP_ROLE):
            self.rebuild_list(e)

    def _insert_row(self, e: dict):
        self.filter_edit.clear()
        self.entries.append(e)
        self.rebuild_list(e)
        self.set_dirty(True)
        self.key_edit.setFocus()
        self.key_edit.selectAll()

    def add_entry(self):
        existing = {e["key"].lower() for e in self.entries}
        n = 1
        while f"new{n}" in existing:
            n += 1
        self._insert_row({"key": f"new{n}", "text": "", "description": "", "icon": "", "lazy": False,
                          "keywords": "", "format": "", "aliases": "", "group": "", "action": "",
                          "when": "", "propagate_case": False})

    def duplicate_entry(self):
        if e := self.current():
            self._insert_row({**e, "key": e["key"] + "2"})

    def remove_entry(self):
        if (e := self.current()) is None:
            return
        # then show the next one in the list, or the previous one at the end
        shown = [x for item, x in self.entry_items() if not item.isHidden()]
        pos = next(n for n, x in enumerate(shown) if x is e)
        neighbour = (shown[pos + 1:] or shown[pos - 1:pos] or [None])[0]
        i = next(n for n, x in enumerate(self.entries) if x is e)
        self.removed.append((i, e))
        del self.entries[i]
        self.rebuild_list(neighbour)
        self._update_undo()
        self.set_dirty(True)

    def undo_remove(self):
        """Put back the abbreviation removed last, where it was."""
        if not self.removed:
            return
        i, e = self.removed.pop()
        self.entries.insert(min(i, len(self.entries)), e)
        if self.filter_edit.text():
            self.filter_edit.clear()  # it might not match the filter
        self.rebuild_list(e)
        self._update_undo()
        self.set_dirty(True)
        self.list.setFocus()

    def _update_undo(self):
        self.undo_btn.setEnabled(bool(self.removed))
        self.undo_btn.setToolTip(f"Bring back “{self.removed[-1][1]['key']}” (Ctrl+Z in the list)"
                                 if self.removed else "Nothing removed to bring back")

    def apply_filter(self, text: str):
        text = text.lower()
        shown_groups = set()
        for item, e in self.entry_items():
            hit = not text or any(text in e.get(f, "").lower() for f in ("key", "description", "keywords", "aliases", "group", "text"))
            item.setHidden(not hit)
            if hit:
                shown_groups.add(item.data(GROUP_ROLE))
        for row in range(self.list.count()):  # headers of groups with nothing shown
            item = self.list.item(row)
            if item.data(ENTRY_ROLE) is None:
                item.setHidden(item.data(GROUP_ROLE) not in shown_groups)

    # ---- editor helpers

    def insert_placeholder(self, snippet: str):
        self.text_edit.insertPlainText(snippet)
        self.text_edit.setFocus()

    def insert_filter(self, snippet: str):
        """Insert |filter; right after a placeholder, put it inside the closing brace."""
        cursor = self.text_edit.textCursor()
        before = self.text_edit.toPlainText()[:cursor.position()]
        if before.endswith("}") and not before.endswith("}}"):
            cursor.movePosition(cursor.MoveOperation.Left)
            self.text_edit.setTextCursor(cursor)
        self.insert_placeholder(snippet)

    def ask(self, e: dict) -> dict | None:
        """All prompts of e in one form, as for the runner; None if cancelled."""
        prompts = k.ask_prompts(e["text"])
        values = run_form(prompts, f"Abbreviations · {e['key']}", self)
        return None if values is None else {(n, l): v for (n, l, _), v in zip(prompts, values)}

    def update_preview(self, force: bool = False):
        e = self.current()
        k.ABBR_TEXTS.clear()  # for {abbr:key}, as edited here
        k.ABBR_TEXTS.update({x["key"].lower(): x["text"] for x in self.entries})
        asks = bool(e and k.needs_input(e["text"]))
        lazy = bool(e and e["lazy"]) or asks
        self.run_now_btn.setVisible(lazy)
        if e is None:
            self.preview.setPlainText("")
            return
        if lazy and not force:
            self.preview.setStyleSheet("")
            self.preview.setPlainText(
                "Asks for input when Enter is pressed. Click Run Now to test it." if asks else
                "Computed when Enter is pressed. Click Run Now to test it.")
            return
        answers = self.ask(e) if asks else None
        if asks and answers is None:
            return  # cancelled
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            timeout = k.LAZY_TIMEOUT if lazy else self.timeout_spin.value()
            result = k.expand(e["text"], self.arg_edit.text().strip(), timeout, answers=answers)
            if e.get("format") == "markdown":
                result += "\n\n— copied as HTML —\n" + k.markdown_to_html(result)
            self.preview.setStyleSheet("")
        except Exception as ex:
            result = f"Error: {ex}"
            self.preview.setStyleSheet("color: palette(link-visited);")
        finally:
            QApplication.restoreOverrideCursor()
        self.preview.setPlainText(result)


def main():
    locale.setlocale(locale.LC_ALL, "")  # locale-aware sorting in previews
    app = QApplication(sys.argv)
    app.setApplicationName("krunner-abbr-settings")
    dlg = SettingsDialog()
    if len(sys.argv) > 1:  # "krunner_abbr_settings.py KEY" opens on that abbreviation
        dlg.select_key(sys.argv[1])
    dlg.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
