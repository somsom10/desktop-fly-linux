"""Global clicks: taps on the fly's substrate, and right-click to leave sugar.

Upstream uses ``NSEvent.addGlobalMonitorForEvents``, which is permission-free on
macOS and reports every click anywhere on screen.  Wayland has no equivalent —
a client cannot observe input directed at other surfaces — but XInput2 *raw*
events on the X root come closest: they report button presses without grabbing
or otherwise interfering with them.

**Limitation.** Under XWayland this sees presses routed through XWayland.
Clicks landing on native Wayland surfaces are not reported, so the fly will not
feel those taps.  It degrades to simply never being startled by them.
"""

from __future__ import annotations

from dataclasses import dataclass

from Xlib import X as _X, display as _xdisplay

# X modifier bitmasks
_SHIFT, _CONTROL, _ALT, _SUPER = 1, 4, 8, 64

# python-xlib does not parse XI2 raw events: it hands back a GenericEvent whose
# .data is the unparsed tail of xXIRawEvent, starting after the 10-byte generic
# header. The remaining 30 bytes are, little-endian:
#     0..1  deviceid (u16)
#     2..5  time (u32)
#     6..9  detail (u32)  <- the button number
#    10..11 sourceid (u16)
#    12..13 valuators_len (u16)
#    14..17 flags (u32)
_RAW_DETAIL_OFFSET = 6


def _raw_button(data) -> int:
    """Button number out of a raw XI2 event, or 0 if it cannot be read.

    Returns 0 rather than a plausible default on purpose: an earlier version
    defaulted to 1, so every unparsed event looked like a left-click and
    right-click silently never fired.
    """
    try:
        if data is None or len(data) < _RAW_DETAIL_OFFSET + 4:
            return 0
        return int.from_bytes(bytes(data[_RAW_DETAIL_OFFSET:_RAW_DETAIL_OFFSET + 4]), "little")
    except Exception:
        return 0


@dataclass
class ClickEvent:
    button: int  # 1 = left, 2 = middle, 3 = right
    x: int
    y: int
    ctrl: bool = False
    shift: bool = False
    alt: bool = False
    super_: bool = False

    def has_modifier(self, name: str) -> bool:
        return {
            "ctrl": self.ctrl, "shift": self.shift, "alt": self.alt,
            "super": self.super_, "none": True,
        }.get(name, False)

try:
    from Xlib.ext import xinput as _xinput

    _HAVE_XINPUT = True
except ImportError:  # pragma: no cover
    _xinput = None
    _HAVE_XINPUT = False


class TapSense:
    def __init__(self) -> None:
        self.available = False
        self._d = None
        if not _HAVE_XINPUT:
            return
        try:
            self._d = _xdisplay.Display()
            self._d.xinput_query_version()
            root = self._d.screen().root
            root.xinput_select_events([
                (_xinput.AllMasterDevices, _xinput.RawButtonPressMask),
            ])
            self._d.sync()
            self.available = True
        except Exception:
            self._d = None
            self.available = False

    def poll(self) -> list[ClickEvent]:
        """Button presses observed since the previous call.

        Raw XI2 events carry no pointer position or modifier state, so both are
        read back from the server as each event is drained. That is a frame
        behind at worst, which is irrelevant for a fly.
        """
        if not self.available or self._d is None:
            return []
        clicks: list[ClickEvent] = []
        try:
            for _ in range(self._d.pending_events()):
                event = self._d.next_event()
                if getattr(event, "evtype", None) != _xinput.RawButtonPress:
                    continue
                button = _raw_button(getattr(event, "data", None))
                if button == 0:
                    continue
                p = self._d.screen().root.query_pointer()
                clicks.append(ClickEvent(
                    button=button, x=p.root_x, y=p.root_y,
                    ctrl=bool(p.mask & _CONTROL), shift=bool(p.mask & _SHIFT),
                    alt=bool(p.mask & _ALT), super_=bool(p.mask & _SUPER),
                ))
        except Exception:
            return clicks
        return clicks


_MOD_MASKS = {
    "ctrl": _X.ControlMask,
    "shift": _X.ShiftMask,
    "alt": _X.Mod1Mask,
    "super": _X.Mod4Mask,
}
# CapsLock and NumLock are just more modifier bits, and a grab registered
# without them silently stops matching the moment either is on.
_LOCK_COMBOS = (0, _X.LockMask, _X.Mod2Mask, _X.LockMask | _X.Mod2Mask)


class ButtonGrab:
    """A passive grab on <modifier>+button, e.g. Ctrl+right-click.

    Preferred over watching raw XI2 events for this job, for two reasons:

    * **Modifiers are correct.** Raw events carry no modifier state, so the only
      way to pair one with a modifier is to query the keyboard afterwards — a
      race that loses whenever the key is released quickly. A grab matches the
      modifier at press time, in the server.
    * **The click is consumed.** Without a grab the right-click would also reach
      whatever is underneath and open its context menu.

    Plain clicks are untouched: only this one combination is grabbed.
    """

    def __init__(self, button: int = 3, modifier: str = "ctrl"):
        self.available = False
        self.error = None
        self.button = button
        self._d = None
        mask = _MOD_MASKS.get(modifier)
        if mask is None:
            # "none" would mean grabbing every right-click, which would break
            # right-click everywhere; leave it to the raw-event path instead.
            return
        try:
            self._d = _xdisplay.Display()
            self._root = self._d.screen().root
            # X reports protocol errors asynchronously, so a failed grab never
            # raises here — it just prints later. Capture them explicitly, or a
            # combination already grabbed by another client (or a second copy of
            # this app) would look like success and silently do nothing.
            errors = []
            self._d.set_error_handler(lambda err, req: errors.append(err))
            for extra in _LOCK_COMBOS:
                self._root.grab_button(
                    button, mask | extra, True, _X.ButtonPressMask,
                    _X.GrabModeAsync, _X.GrabModeAsync, _X.NONE, _X.NONE)
            self._d.sync()
            self._d.set_error_handler(None)
            if errors:
                self.error = (
                    f"{modifier}+button-{button} is already grabbed by another "
                    "client (another copy of desktopfly?); sugar placement by "
                    "click is unavailable — use the tray menu"
                )
                self._d = None
            else:
                self.available = True
        except Exception as exc:
            self.error = str(exc)
            self._d = None
            self.available = False

    def poll(self) -> list[ClickEvent]:
        """Grabbed presses since the previous call, with real screen coords."""
        if not self.available or self._d is None:
            return []
        out: list[ClickEvent] = []
        try:
            for _ in range(self._d.pending_events()):
                event = self._d.next_event()
                if event.type != _X.ButtonPress:
                    continue
                out.append(ClickEvent(
                    button=int(event.detail), x=int(event.root_x), y=int(event.root_y),
                    ctrl=bool(event.state & _X.ControlMask),
                    shift=bool(event.state & _X.ShiftMask),
                    alt=bool(event.state & _X.Mod1Mask),
                    super_=bool(event.state & _X.Mod4Mask),
                ))
        except Exception:
            return out
        return out

    def close(self) -> None:
        if self._d is None:
            return
        try:
            mask = _MOD_MASKS.get("ctrl", 0)
            for extra in _LOCK_COMBOS:
                self._root.ungrab_button(self.button, mask | extra)
            self._d.sync()
        except Exception:
            pass
