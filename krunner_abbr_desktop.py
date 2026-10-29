# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicolas Vaughan
"""Desktop plumbing: the clipboard, pasting into the focused window, notifications."""

import itertools
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


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


# key name -> (evdev code for the portal and ydotool, wtype name, xdotool name)
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


# ---------------------------------------------------------------- RemoteDesktop portal


PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
APP_ID = "io.github.nivaca.AbbrKRunner"
PORTAL_TOKEN = (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
                / "krunner-abbr" / "portal-token")


class Portal:
    """Presses keys through the RemoteDesktop portal: KWin itself, no root, no extra tools.

    Plasma asks once; the answer is kept with a restore token (each one works once, so
    the new one is saved every time). The session stays open a while, so quick pastes
    don't start a new one. The portal answers with signals, so this needs the GLib main
    loop running in another thread.
    """
    IDLE = 60  # seconds before an unused session is closed

    def __init__(self):
        self.lock = threading.Lock()
        self.session = None
        self.refused = False
        self.timer = None
        self.tokens = itertools.count(1)
        self.registered = False

    def _options(self, **kw):
        import dbus

        return dbus.Dictionary({"handle_token": f"abbr{os.getpid()}_{next(self.tokens)}", **kw},
                               "sv")

    def _request(self, method: str, *args, timeout: float = 10) -> dict:
        """Call a method that answers through a Request object; return its results."""
        import dbus

        bus = dbus.SessionBus()
        options = args[-1]
        sender = bus.get_unique_name()[1:].replace(".", "_")
        path = f"{PORTAL_PATH}/request/{sender}/{options['handle_token']}"
        done, answer = threading.Event(), {}

        def response(code, results):
            answer.update(code=int(code), results=results)
            done.set()

        # subscribe before calling, or a quick answer is lost
        match = bus.add_signal_receiver(response, "Response", "org.freedesktop.portal.Request",
                                        path=path)
        try:
            bus.get_object(PORTAL, PORTAL_PATH).get_dbus_method(
                method, "org.freedesktop.portal.RemoteDesktop")(*args)
            if not done.wait(timeout):
                raise RuntimeError(f"no answer from the portal ({method})")
        finally:
            match.remove()
        if answer["code"] == 1:
            raise PermissionError("remote control was not allowed")
        if answer["code"]:
            raise RuntimeError(f"the portal refused {method}")
        return answer["results"]

    def _start(self) -> None:
        import dbus

        bus = dbus.SessionBus()
        if not self.registered:  # a name in the dialog and for the saved permission
            self.registered = True
            try:
                bus.get_object(PORTAL, PORTAL_PATH).get_dbus_method(
                    "Register", "org.freedesktop.host.portal.Registry")(
                    APP_ID, dbus.Dictionary({}, "sv"))
            except Exception as e:
                print(f"portal registry: {e}", file=sys.stderr)
        token = None
        options = self._options(session_handle_token=f"abbr{os.getpid()}_s{next(self.tokens)}")
        session = dbus.ObjectPath(self._request("CreateSession", options)["session_handle"])
        try:
            token = PORTAL_TOKEN.read_text().strip()
        except OSError:
            pass
        extra = {"restore_token": token} if token else {}
        self._request("SelectDevices", session,
                      self._options(types=dbus.UInt32(1), persist_mode=dbus.UInt32(2), **extra))
        # the first time, Plasma asks: give the user time to answer
        results = self._request("Start", session, "", self._options(), timeout=120)
        if not int(results.get("devices", 0)) & 1:
            raise PermissionError("the portal gave no keyboard")
        if results.get("restore_token"):
            PORTAL_TOKEN.parent.mkdir(parents=True, exist_ok=True)
            PORTAL_TOKEN.write_text(str(results["restore_token"]))
            PORTAL_TOKEN.chmod(0o600)
        handle = bus.add_signal_receiver(lambda *a: self._forget(session), "Closed",
                                         "org.freedesktop.portal.Session", path=session)
        self.session = (session, handle)

    def _forget(self, session) -> None:
        if self.session and self.session[0] == session:
            self.session[1].remove()
            self.session = None

    def _close(self) -> None:
        import dbus

        with self.lock:
            if self.session is None:
                return
            session = self.session[0]
            self._forget(session)
            try:
                dbus.SessionBus().get_object(PORTAL, session).get_dbus_method(
                    "Close", "org.freedesktop.portal.Session")()
            except Exception:
                pass

    def press(self, codes: list[tuple[int, bool]]) -> None:
        """Send (evdev keycode, pressed) events to the focused window."""
        import dbus

        if self.refused:
            raise PermissionError("remote control was not allowed; restart the runner to be "
                                  "asked again")
        with self.lock:
            if self.timer:
                self.timer.cancel()
            try:
                if self.session is None:
                    self._start()
            except PermissionError:
                self.refused = True
                raise
            notify_key = dbus.SessionBus().get_object(PORTAL, PORTAL_PATH).get_dbus_method(
                "NotifyKeyboardKeycode", "org.freedesktop.portal.RemoteDesktop")
            try:
                for code, pressed in codes:
                    notify_key(self.session[0], dbus.Dictionary({}, "sv"), dbus.Int32(code),
                               dbus.UInt32(1 if pressed else 0))
                    time.sleep(0.01)
            except Exception:
                self.session = None  # perhaps closed under us: start again next time
                raise
            self.timer = threading.Timer(self.IDLE, self._close)
            self.timer.daemon = True
            self.timer.start()


portal = Portal()


def key_events(combo: str = "ctrl+v", back: int = 0) -> list[tuple[int, bool]]:
    """(evdev keycode, pressed) events for combo, then Left back times."""
    mods, key = parse_keys(combo)
    down = [KEYS[m][0] for m in mods] + [KEYS[key][0]]
    return ([(c, True) for c in down] + [(c, False) for c in reversed(down)]
            + [(KEYS["left"][0], p) for _ in range(back) for p in (True, False)])


def paste_clipboard(combo: str = "ctrl+v", back: int = 0) -> None:
    """Press combo (Ctrl+V) in the focused window, then move the cursor back characters.

    The RemoteDesktop portal comes first; the external tools are a fallback.
    Raises RuntimeError if nothing can.
    """
    events = key_events(combo, back)
    errors = []
    try:
        portal.press(events)
        return
    except Exception as e:
        errors.append(f"portal: {e}")
    for cmd in paste_commands(combo, back):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return
        errors.append(f"{cmd[0]}: {(r.stderr or r.stdout).strip() or r.returncode}")
    raise RuntimeError("; ".join(errors))

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
