#!/usr/bin/env python3
"""One dialog for all the prompts of an abbreviation: {ask}, {asktext}, {choose}, {opt}
and {askdate}.

The runner starts it with the window title as argument and the prompts as a JSON list of
[name, label, default] on stdin. It prints the answers as a JSON list in the same order
and exits 0, or exits 1 if cancelled and 2 if it can't run (the runner then falls back to
one kdialog per prompt). The settings dialog calls run_form directly.
"""

import json
import sys

import krunner_abbr as k


def run_form(prompts: list, title: str = "Abbreviations", parent=None) -> list[str] | None:
    """Show the form; the answers in prompt order, or None if cancelled."""
    from PySide6.QtCore import QDate, Qt
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
        QPlainTextEdit,
    )

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
    dlg.setMinimumWidth(420)
    form = QFormLayout(dlg)
    getters = []
    for name, label, default in prompts:
        if name == "opt":
            w = QCheckBox(label, checked=default.strip().lower() in k.YES)
            form.addRow("", w)
            getters.append(lambda w=w: "1" if w.isChecked() else "")
            continue
        if name == "askdate":
            start = QDate.fromString(default.strip(), "yyyy-MM-dd")
            w = QDateEdit(start if start.isValid() else QDate.currentDate(), calendarPopup=True)
            w.setDisplayFormat("yyyy-MM-dd")
            getters.append(lambda w=w: w.date().toString("yyyy-MM-dd"))
        elif name == "choose":
            w = QComboBox()
            w.addItems(k.choices(default))
            getters.append(w.currentText)
        elif name == "asktext":
            w = QPlainTextEdit(default)
            w.setTabChangesFocus(True)
            getters.append(w.toPlainText)
        else:
            w = QLineEdit(default)
            getters.append(w.text)
        form.addRow(f"{label}:", w)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    form.addRow(buttons)
    dlg.activateWindow()
    if dlg.exec() != QDialog.Accepted:
        return None
    return [get() for get in getters]


def main() -> None:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    try:
        prompts = json.load(sys.stdin)
        app = QApplication(sys.argv[:1])
        app.setApplicationName("krunner-abbr-form")
        app.setDesktopFileName("krunner-abbr-form")
        answers = run_form(prompts, sys.argv[1] if len(sys.argv) > 1 else "Abbreviations")
    except Exception as e:  # exit 1 would read as "cancelled"
        print(e, file=sys.stderr)
        sys.exit(2)
    if answers is None:
        sys.exit(1)
    print(json.dumps(answers))


if __name__ == "__main__":
    main()
