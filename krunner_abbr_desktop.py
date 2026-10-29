# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Desktop plumbing: the clipboard, pasting into the focused window, notifications."""

import os
import shutil
import subprocess
import sys
import time


# ---------------------------------------------------------------- clipboard


def _dbus_klipper():
    import dbus

    return dbus.Interface(
        dbus.SessionBus().get_object("org.kde.klipper", "/klipper"),
        "org.kde.klipper.klipper",
    )


def get_clipboard() -> str:
    try:
        return str(_dbus_klipper().getClipboardContents())
    except Exception:
        pass
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-o", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout
            except Exception:
                pass
    return ""


def get_selection() -> str:
    """The selected (highlighted) text: the primary selection."""
    for cmd in (["wl-paste", "--primary", "--no-newline"], ["xclip", "-o", "-selection", "primary"]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout
            except Exception:
                pass
    return ""


def get_clipboard_entry(pos: int) -> str:
    """Entry pos of Klipper's history (1 = current clipboard); "" if there is none."""
    try:
        return str(_dbus_klipper().getClipboardHistoryItem(pos - 1))
    except Exception:
        return get_clipboard() if pos == 1 else ""


def get_clipboard_history() -> list[str]:
    """Klipper's history, most recent (the current clipboard) first."""
    try:
        return [str(t) for t in _dbus_klipper().getClipboardHistoryMenu()]
    except Exception:
        current = get_clipboard()
        return [current] if current else []


def set_clipboard(text: str, html_text: str | None = None) -> None:
    """Copy text; with html_text, copy that as text/html instead (rich-text targets)."""
    if html_text is not None:
        for cmd in (["wl-copy", "--type", "text/html"],
                    ["xclip", "-selection", "clipboard", "-t", "text/html"]):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True,
                                 start_new_session=True).communicate(html_text)
                return
    try:
        _dbus_klipper().setClipboardContents(text)
        return
    except Exception:
        pass
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True, start_new_session=True).communicate(text)
            return
    raise RuntimeError("no clipboard backend available (Klipper, wl-copy, xclip)")


# key name -> (evdev code for ydotool, wtype name, xdotool name)
KEYS = {
    "ctrl": (29, "ctrl", "ctrl"), "shift": (42, "shift", "shift"), "alt": (56, "alt", "alt"),
    "super": (125, "logo", "super"), "v": (47, "v", "v"), "insert": (110, "Insert", "Insert"),
    "left": (105, "Left", "Left"),
}
MODIFIERS = ("ctrl", "shift", "alt", "super")


def parse_keys(combo: str) -> tuple[list[str], str]:
    """'ctrl+shift+v' -> (['ctrl', 'shift'], 'v'). Raises ValueError."""
    *mods, key = [k.strip().lower() for k in combo.split("+")]
    if key not in KEYS or key in MODIFIERS or any(m not in MODIFIERS for m in mods):
        raise ValueError(f"unsupported paste keys “{combo}”: use ctrl+v, ctrl+shift+v or "
                         "shift+insert")
    return mods, key


def paste_commands(combo: str = "ctrl+v", back: int = 0) -> list[list[str]]:
    """Commands that press combo in the focused window, then Left back times; best first."""
    mods, key = parse_keys(combo)
    code = {k: v[0] for k, v in KEYS.items()}
    ydotool = ["ydotool", "key", *[f"{code[m]}:1" for m in mods], f"{code[key]}:1",
               f"{code[key]}:0", *[f"{code[m]}:0" for m in reversed(mods)],
               *["105:1", "105:0"] * back]
    wkey = KEYS[key][1]
    wtype = ["wtype", *[a for m in mods for a in ("-M", KEYS[m][1])],
             *(["-k", wkey] if len(wkey) > 1 else [wkey]),
             *[a for m in reversed(mods) for a in ("-m", KEYS[m][1])], *["-k", "Left"] * back]
    cmds = [ydotool, wtype]
    if os.environ.get("XDG_SESSION_TYPE") != "wayland":  # on Wayland it reaches only XWayland
        cmds.append(["xdotool", "key", "--clearmodifiers",
                     "+".join([KEYS[m][2] for m in mods] + [KEYS[key][2]]), *["Left"] * back])
    return [c for c in cmds if shutil.which(c[0])]


def paste_clipboard(combo: str = "ctrl+v", back: int = 0) -> None:
    """Press combo (Ctrl+V) in the focused window, then move the cursor back characters.

    Raises RuntimeError if no tool can.
    """
    errors = []
    for cmd in paste_commands(combo, back):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return
        errors.append(f"{cmd[0]}: {(r.stderr or r.stdout).strip() or r.returncode}")
    raise RuntimeError("; ".join(errors) or
                       "install ydotool (and enable its service) or wtype to paste automatically")

# ---------------------------------------------------------------- notifications


NOTIFY_TIMEOUT = 5000  # ms
_last_notification = 0  # replaced by the next notification instead of stacking up
_last_notified = 0.0
_watching = False


def _closed(notification_id, reason) -> None:
    global _last_notification
    if int(notification_id) == _last_notification:
        _last_notification = 0


def notify(summary: str, body: str = "", icon: str = "edit-paste") -> None:
    """Show a notification, replacing the previous one only while it is still on screen.

    Plasma keeps the "expired"/"dismissed" state of a notification when it is replaced,
    so replacing one that was already closed shows nothing at all.
    """
    global _last_notification, _last_notified, _watching
    try:
        import dbus

        bus = dbus.SessionBus()
        if not _watching:  # needs the GLib main loop of the runner; outside it, the timer is enough
            bus.add_signal_receiver(_closed, "NotificationClosed", "org.freedesktop.Notifications",
                                    path="/org/freedesktop/Notifications")
            _watching = True
        still_open = time.monotonic() - _last_notified < NOTIFY_TIMEOUT / 1000 - 1
        _last_notification = int(dbus.Interface(
            bus.get_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications"),
            "org.freedesktop.Notifications",
        ).Notify("Abbreviations", dbus.UInt32(_last_notification if still_open else 0), icon,
                 summary, body, dbus.Array([], "s"),
                 dbus.Dictionary({"transient": dbus.Boolean(True)}, "sv"), NOTIFY_TIMEOUT))
        _last_notified = time.monotonic()
    except Exception as e:
        print(f"notification failed: {e}", file=sys.stderr)


def open_url(target: str) -> None:
    subprocess.Popen(["xdg-open", target.strip()], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
