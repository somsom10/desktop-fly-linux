"""Permission-free environment senses, Linux edition.

Port of upstream's ``Environment.swift``.  macOS gives all four of these away
without a TCC prompt; on GNOME/Wayland the sources are different and one of them
(keyboard-vs-mouse idle) has no direct equivalent and is inferred instead.

===================  ==========================  ================================
sense                macOS                       here
===================  ==========================  ================================
window terrain       ``CGWindowListCopyWindow``   EWMH ``_NET_CLIENT_LIST`` (X11)
user idle            ``CGEventSource``            ``org.gnome.Mutter.IdleMonitor``
typing vibration     keyDown-only idle query      idle reset without pointer motion
temperature          ``ProcessInfo.thermalState`` ``/sys/class/thermal``
===================  ==========================  ================================

**Known limitation.** ``_NET_CLIENT_LIST`` enumerates XWayland clients only.
Native Wayland windows are invisible to any unprivileged client — Wayland has no
protocol for one client to learn another's geometry — so the fly cannot land on
their edges. See ``docs/PORT_PLAN.md``.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

from Xlib import X, display as _xdisplay

from ..body.fly import Ledge
from .gtkcompat import Gio

# --------------------------------------------------------------- circadian


def circadian_activity(hour: float) -> float:
    """Drosophila activity: morning and evening peaks, midday siesta, night quiet.

    Returns a multiplier for the sim's baseline drive.
    """
    pts = [
        (0.0, 0.25), (5.0, 0.25), (8.0, 1.0), (10.0, 1.0), (13.0, 0.55),
        (15.0, 0.55), (17.0, 1.0), (20.0, 1.0), (23.0, 0.3), (24.0, 0.25),
    ]
    for i in range(len(pts) - 1):
        if pts[i][0] <= hour <= pts[i + 1][0]:
            span = max(0.001, pts[i + 1][0] - pts[i][0])
            t = (hour - pts[i][0]) / span
            return pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
    return 0.25


# -------------------------------------------------------------------- idle


class IdleSense:
    """Seconds since the user last touched mouse or keyboard.

    ``org.gnome.Mutter.IdleMonitor`` is the only source that sees *Wayland*
    input; the X11 screensaver extension is not even present under XWayland.
    It reveals when input happened, never what it was.
    """

    def __init__(self) -> None:
        self._proxy = None
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                None,
                "org.gnome.Mutter.IdleMonitor",
                "/org/gnome/Mutter/IdleMonitor/Core",
                "org.gnome.Mutter.IdleMonitor",
                None,
            )
        except Exception:
            self._proxy = None

    @property
    def available(self) -> bool:
        return self._proxy is not None

    def seconds(self) -> float:
        if self._proxy is None:
            return 0.0
        try:
            return self._proxy.GetIdletime() / 1000.0
        except Exception:
            return 0.0


class TypingSense:
    """Infer keyboard activity without ever reading a key.

    macOS can ask "how long since the last *keyDown*"; GNOME only exposes a
    combined idle counter.  But if the idle timer resets while the pointer has
    not moved, the input that reset it was not the mouse — so it was the
    keyboard.  Same "when, never which" guarantee as upstream.
    """

    def __init__(self, idle: IdleSense) -> None:
        self._idle = idle
        self._prev_idle = 0.0
        self._prev_pointer = (0, 0)
        self._last_key_age = 999.0

    def poll(self, pointer: tuple[int, int], dt: float) -> float:
        idle = self._idle.seconds()
        moved = pointer != self._prev_pointer
        # idle counter went backwards => some input happened
        if idle < self._prev_idle and not moved:
            self._last_key_age = 0.0
        else:
            self._last_key_age += dt
        self._prev_idle = idle
        self._prev_pointer = pointer
        return 1.0 if self._last_key_age < 0.6 else 0.0


# ------------------------------------------------------------- temperature

_THERMAL_PREFERRED = ("x86_pkg_temp", "TCPU", "coretemp", "acpitz")


def _thermal_zones() -> list[str]:
    """Package/CPU zones first — they track load the way macOS thermal state does."""
    zones = []
    for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        try:
            with open(os.path.join(z, "type")) as f:
                kind = f.read().strip()
        except OSError:
            continue
        if not os.access(os.path.join(z, "temp"), os.R_OK):
            continue
        zones.append((kind, os.path.join(z, "temp")))
    zones.sort(key=lambda kv: next(
        (i for i, p in enumerate(_THERMAL_PREFERRED) if p in kv[0]), len(_THERMAL_PREFERRED)))
    return [path for _kind, path in zones]


_ZONE_PATHS = None


def cpu_temperature() -> float | None:
    """Hottest reading from the preferred thermal zones, in degrees Celsius."""
    global _ZONE_PATHS
    if _ZONE_PATHS is None:
        _ZONE_PATHS = _thermal_zones()[:3]
    best = None
    for p in _ZONE_PATHS:
        try:
            with open(p) as f:
                v = int(f.read().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if 0 < v < 150 and (best is None or v > best):
            best = v
    return best


def thermal_tempo() -> float:
    """Flies are ectotherms: a hot machine is a fast fly.

    macOS reports four discrete thermal-pressure states; Linux exposes raw
    temperature, so the same four tempo steps are keyed off °C bands.
    """
    t = cpu_temperature()
    if t is None:
        return 1.0
    if t < 60:
        return 1.0  # nominal
    if t < 75:
        return 1.15  # fair
    if t < 85:
        return 1.35  # serious
    return 1.5  # critical


# ---------------------------------------------------------- window terrain


@dataclass
class WindowSnapshot:
    ledges: list[Ledge]
    new_windows: list[tuple[tuple[float, float], float]]  # (centre, size)


class WindowSense:
    """Walkable window top edges and newly-appeared windows, via EWMH."""

    SKIP_TYPES = {"_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK"}

    def __init__(self) -> None:
        self._d = _xdisplay.Display()
        self._root = self._d.screen().root
        self._atoms = {
            n: self._d.intern_atom(n)
            for n in ("_NET_CLIENT_LIST", "_NET_WM_WINDOW_TYPE", "_NET_WM_STATE",
                      "_NET_WM_STATE_HIDDEN", "_NET_WM_PID", "_NET_FRAME_EXTENTS")
        }
        self._known: set[int] = set()
        self._first = True
        self._own_pid = os.getpid()

    def _client_list(self) -> list:
        try:
            prop = self._root.get_full_property(self._atoms["_NET_CLIENT_LIST"], X.AnyPropertyType)
        except Exception:
            return []
        if prop is None:
            return []
        return [self._d.create_resource_object("window", wid) for wid in prop.value]

    def poll(self, screen: tuple[float, float, float, float]) -> WindowSnapshot:
        """``screen`` is the fly's monitor as (x, y, width, height) in X11 coords."""
        mx, my, mw, mh = screen
        cx, cy = mx + mw / 2, my + mh / 2
        ledges: list[Ledge] = []
        new_wins: list[tuple[tuple[float, float], float]] = []
        ids: set[int] = set()

        for win in self._client_list():
            try:
                attrs = win.get_attributes()
                if attrs.map_state != X.IsViewable:
                    continue
                wtype = win.get_full_property(self._atoms["_NET_WM_WINDOW_TYPE"], X.AnyPropertyType)
                if wtype is not None:
                    names = {self._d.get_atom_name(a) for a in wtype.value}
                    if names & self.SKIP_TYPES:
                        continue
                state = win.get_full_property(self._atoms["_NET_WM_STATE"], X.AnyPropertyType)
                if state is not None:
                    if self._atoms["_NET_WM_STATE_HIDDEN"] in list(state.value):
                        continue
                pid_prop = win.get_full_property(self._atoms["_NET_WM_PID"], X.AnyPropertyType)
                if pid_prop is not None and int(pid_prop.value[0]) == self._own_pid:
                    continue
                geom = win.get_geometry()
                # python-xlib's translate_coords() takes the *source* window and
                # is called on the destination, i.e. the opposite of XTranslate-
                # Coordinates' argument order. Getting this backwards yields the
                # negated position.
                coords = self._root.translate_coords(win, 0, 0)
                wx, wy = coords.x, coords.y
                ww, wh = geom.width, geom.height
            except Exception:
                continue
            if ww < 160 or wh < 60:
                continue
            wid = win.id
            ids.add(wid)
            # only windows overlapping the fly's display
            if wx + ww < mx or wx > mx + mw or wy + wh < my or wy > my + mh:
                continue
            # scene coords: centred on this display, y up
            top_y = cy - wy
            x0 = max(wx - cx, -mw / 2 + 15)
            x1 = min(wx + ww - cx, mw / 2 - 15)
            if -mh / 2 + 8 < top_y < mh / 2 - 8 and x1 - x0 > 100 and len(ledges) < 12:
                ledges.append(Ledge(y=top_y, x0=x0, x1=x1, id=wid))
            if not self._first and wid not in self._known:
                centre = (wx + ww / 2 - cx, cy - (wy + wh / 2))
                new_wins.append((centre, float(max(ww, wh))))

        self._known = ids
        self._first = False
        return WindowSnapshot(ledges=ledges, new_windows=new_wins)
