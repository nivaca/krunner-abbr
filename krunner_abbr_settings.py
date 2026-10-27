#!/usr/bin/env python3
"""Settings dialog for krunner-abbr: trigger keyword, matching options and abbreviations.

Writes ~/.config/krunner-abbr/abbreviations.toml, which the runner reloads on the
next keystroke in KRunner.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

import krunner_abbr as k

KEY_RE = re.compile(r"^\S+$")
BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# (label, text inserted at the cursor)
PLACEHOLDERS = [
    ("Date (ISO)", "{date}"),
    ("Time", "{time}"),
    ("Date and time (ISO)", "{now}"),
    ("Custom date format", "{date:%d/%m/%Y}"),
    ("Tomorrow", "{date+1d}"),
    ("Next month", "{date+1mo}"),
    (None, None),
    ("Typed argument", "{arg}"),
    ("Typed argument with default", "{arg:default}"),
    (None, None),
    ("Clipboard contents", "{clipboard}"),
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
    entries = []
    for key, val in data.get("abbr", {}).items():
        if isinstance(val, str):
            val = {"text": val}
        entries.append({
            "key": key,
            "text": val.get("text", ""),
            "description": val.get("description", ""),
            "icon": val.get("icon", ""),
        })
    return settings, entries


def toml_str(s: str) -> str:
    if "\n" in s and "'''" not in s and "\r" not in s:
        return "'''\n" + s + "'''"  # literal multi-line: no escaping needed
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid TOML basic strings


def toml_key(key: str) -> str:
    return key if BARE_KEY_RE.match(key) else json.dumps(key, ensure_ascii=False)


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
        "",
        "[abbr]",
    ]
    # plain key = value pairs must precede sub-tables in TOML
    simple = [e for e in entries if not e["description"] and not e["icon"]]
    tables = [e for e in entries if e["description"] or e["icon"]]
    out += [f"{toml_key(e['key'])} = {toml_str(e['text'])}" for e in simple]
    for e in tables:
        out += ["", f"[abbr.{toml_key(e['key'])}]", f"text = {toml_str(e['text'])}"]
        if e["description"]:
            out.append(f"description = {toml_str(e['description'])}")
        if e["icon"]:
            out.append(f"icon = {toml_str(e['icon'])}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- dialog


class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Abbreviations Settings")
        self.setWindowIcon(QIcon.fromTheme("edit-paste"))
        self.resize(860, 560)
        self.path = k.CONFIG_FILE
        self.entries: list[dict] = []
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
            "<code>expan isod</code>. A word keyword must be followed by a space; a "
            "symbol such as <code>;</code> need not be. The keyword alone lists all "
            "abbreviations. Leave empty to match abbreviations directly."
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
        return w

    def _build_abbr_tab(self) -> QWidget:
        # left: filter + list + add/remove
        self.filter_edit = QLineEdit(placeholderText="Filter…", clearButtonEnabled=True)
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_entry)

        add_btn = QPushButton(QIcon.fromTheme("list-add"), "Add")
        add_btn.clicked.connect(self.add_entry)
        dup_btn = QPushButton(QIcon.fromTheme("edit-copy"), "Duplicate")
        dup_btn.clicked.connect(self.duplicate_entry)
        self.remove_btn = QPushButton(QIcon.fromTheme("list-remove"), "Remove")
        self.remove_btn.clicked.connect(self.remove_entry)
        del_action = QAction(self.list, shortcut=QKeySequence.Delete,
                             shortcutContext=Qt.WidgetShortcut)
        del_action.triggered.connect(self.remove_entry)
        self.list.addAction(del_action)

        btns = QHBoxLayout()
        for b in (add_btn, dup_btn, self.remove_btn):
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
        insert_btn.setMenu(menu)

        self.arg_edit = QLineEdit(placeholderText="text typed after the abbreviation")
        self.preview = QPlainTextEdit(readOnly=True)
        self.preview.setMaximumHeight(90)

        self.editor = QGroupBox("Abbreviation")
        form = QFormLayout(self.editor)
        form.addRow("Abbreviation:", self.key_edit)
        form.addRow("Description:", self.desc_edit)
        form.addRow("Icon:", icon_row)
        text_label = QWidget()
        tl = QVBoxLayout(text_label)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("Expansion:"))
        tl.addWidget(insert_btn)
        tl.addStretch()
        form.addRow(text_label, self.text_edit)
        form.addRow("Test argument:", self.arg_edit)
        form.addRow("Preview:", self.preview)

        self.key_edit.textChanged.connect(self._entry_changed)
        self.desc_edit.textChanged.connect(self._entry_changed)
        self.icon_edit.textChanged.connect(self._entry_changed)
        self.text_edit.textChanged.connect(self._entry_changed)
        self.arg_edit.textChanged.connect(lambda: self.preview_timer.start())

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 600])
        return splitter

    @staticmethod
    def _hint(html: str) -> QLabel:
        label = QLabel(html, wordWrap=True)
        label.setEnabled(False)  # renders in the theme's dimmed text colour
        return label

    # ---- load / save

    def load(self):
        src = self.path if self.path.exists() else k.EXAMPLE_CONFIG
        try:
            settings, self.entries = load_config(src)
        except (OSError, tomllib.TOMLDecodeError) as e:
            QMessageBox.critical(self, "Cannot read configuration", f"{src}\n\n{e}")
            settings, self.entries = {}, []
        self.loading = True
        self.prefix_edit.setText(settings.get("prefix", ""))
        self.min_prefix_spin.setValue(int(settings.get("min_prefix_chars", 2)))
        self.timeout_spin.setValue(float(settings.get("cmd_timeout", k.CMD_TIMEOUT)))
        self.list.clear()
        for e in self.entries:
            self.list.addItem(self._make_item(e))
        self.loading = False
        self.list.setCurrentRow(0 if self.entries else -1)
        self.show_entry(self.list.currentRow())
        self._update_trigger_example()
        self.set_dirty(src != self.path)  # loaded from the example: not saved yet

    def validate(self) -> str | None:
        seen = {}
        for row, e in enumerate(self.entries):
            key = e["key"]
            if not key:
                return self._fail(row, "An abbreviation has no name.")
            if not KEY_RE.match(key):
                return self._fail(row, f"“{key}” contains spaces; abbreviations must be one word.")
            if key.lower() in seen:
                return self._fail(row, f"“{key}” is defined twice (names are case-insensitive).")
            if not e["text"]:
                return self._fail(row, f"“{key}” has an empty expansion.")
            seen[key.lower()] = row
        if any(c.isspace() for c in self.prefix_edit.text().strip()):
            return "The trigger keyword cannot contain spaces."
        return None

    def _fail(self, row: int, msg: str) -> str:
        self.filter_edit.clear()
        self.list.setCurrentRow(row)
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
        self._update_trigger_example()

    def _update_trigger_example(self):
        prefix = self.prefix_edit.text().strip()
        key = self.entries[0]["key"] if self.entries else "isod"
        sep = " " if prefix[-1:].isalnum() else ""
        self.trigger_example.setText(f"In KRunner, type: <b><code>{prefix}{sep}{key}</code></b>")

    # ---- abbreviation list

    def _make_item(self, e: dict) -> QListWidgetItem:
        item = QListWidgetItem()
        self._refresh_item(item, e)
        return item

    @staticmethod
    def _refresh_item(item: QListWidgetItem, e: dict):
        item.setText(e["key"] or "(unnamed)")
        item.setIcon(QIcon.fromTheme(e["icon"] or "edit-paste"))
        item.setToolTip(e["description"] or k.one_line(e["text"], 80))

    def current(self) -> dict | None:
        row = self.list.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def show_entry(self, row: int):
        e = self.current()
        self.editor.setEnabled(e is not None)
        self.remove_btn.setEnabled(e is not None)
        self.loading = True
        self.key_edit.setText(e["key"] if e else "")
        self.desc_edit.setText(e["description"] if e else "")
        self.icon_edit.setText(e["icon"] if e else "")
        self.text_edit.setPlainText(e["text"] if e else "")
        self.loading = False
        self._update_icon_preview()
        self.update_preview()

    def _entry_changed(self):
        if self.loading or (e := self.current()) is None:
            return
        e["key"] = self.key_edit.text().strip()
        e["description"] = self.desc_edit.text()
        e["icon"] = self.icon_edit.text().strip()
        e["text"] = self.text_edit.toPlainText()
        self._refresh_item(self.list.currentItem(), e)
        self._update_icon_preview()
        self._update_trigger_example()
        self.set_dirty(True)
        self.preview_timer.start()

    def _update_icon_preview(self):
        self.icon_preview.setPixmap(
            QIcon.fromTheme(self.icon_edit.text().strip() or "edit-paste").pixmap(22, 22)
        )

    def _insert_row(self, e: dict):
        self.filter_edit.clear()
        self.entries.append(e)
        self.list.addItem(self._make_item(e))
        self.list.setCurrentRow(len(self.entries) - 1)
        self.set_dirty(True)
        self.key_edit.setFocus()
        self.key_edit.selectAll()

    def add_entry(self):
        existing = {e["key"].lower() for e in self.entries}
        n = 1
        while f"new{n}" in existing:
            n += 1
        self._insert_row({"key": f"new{n}", "text": "", "description": "", "icon": ""})

    def duplicate_entry(self):
        if e := self.current():
            self._insert_row({**e, "key": e["key"] + "2"})

    def remove_entry(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.entries):
            return
        del self.entries[row]
        self.list.takeItem(row)
        self.set_dirty(True)
        self.show_entry(self.list.currentRow())

    def apply_filter(self, text: str):
        text = text.lower()
        for row, e in enumerate(self.entries):
            hit = not text or any(text in e[f].lower() for f in ("key", "description", "text"))
            self.list.item(row).setHidden(not hit)

    # ---- editor helpers

    def insert_placeholder(self, snippet: str):
        self.text_edit.insertPlainText(snippet)
        self.text_edit.setFocus()

    def update_preview(self):
        e = self.current()
        if e is None:
            self.preview.setPlainText("")
            return
        try:
            result = k.expand(e["text"], self.arg_edit.text().strip(), self.timeout_spin.value())
            self.preview.setStyleSheet("")
        except Exception as ex:
            result = f"Error: {ex}"
            self.preview.setStyleSheet("color: palette(link-visited);")
        self.preview.setPlainText(result)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("krunner-abbr-settings")
    app.setDesktopFileName("krunner-abbr-settings")
    dlg = SettingsDialog()
    dlg.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
