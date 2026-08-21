"""Global clicks: taps on the fly's substrate.

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

        Raw XI2 events carry no pointer position, so it is read back from the
        server as each event is drained. That is a frame behind at worst, which
        is irrelevant for a fly.
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
                clicks.append(ClickEvent(button=button, x=p.root_x, y=p.root_y))
        except Exception:
            return clicks
        return clicks
