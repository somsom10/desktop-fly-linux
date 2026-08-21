"""Menu-bar equivalent: an AppIndicator tray menu.

Upstream puts a 🪰 ``NSStatusItem`` in the macOS menu bar. GNOME dropped
GtkStatusIcon and does not implement the tray protocol itself, but Ubuntu ships
the ``ubuntu-appindicators`` shell extension and libayatana-appindicator, which
together provide the same thing over ``org.kde.StatusNotifierWatcher``.

If the indicator is unavailable the app must still run, so construction failing
is not fatal — see :attr:`Tray.available`.
"""

from __future__ import annotations

import math
import os

import cairo
import gi

from .gtkcompat import Gtk


def _icon_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    d = os.path.join(base, "desktopfly")
    os.makedirs(d, exist_ok=True)
    return d


def ensure_icon(name: str = "desktopfly") -> tuple[str, str]:
    """Draw a small fly silhouette for the panel and return (dir, icon name).

    There is no fly in any standard icon theme, and a stock stand-in is worse
    than useless — an unrecognisable tray icon means the user cannot find the
    quit item at all.
    """
    d = _icon_dir()
    path = os.path.join(d, f"{name}.png")
    if not os.path.exists(path):
        size = 48
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surf)
        c = size / 2.0
        cr.set_source_rgba(0.93, 0.93, 0.93, 1.0)
        # wings, swept back
        for side in (-1, 1):
            cr.save()
            cr.translate(c + side * 7.5, c + 1)
            cr.rotate(side * 0.55)
            cr.scale(1.0, 2.1)
            cr.arc(0, 0, 5.0, 0, 2 * math.pi)
            cr.set_source_rgba(0.93, 0.93, 0.93, 0.45)
            cr.fill()
            cr.restore()
        cr.set_source_rgba(0.95, 0.95, 0.95, 1.0)
        # legs
        cr.set_line_width(1.7)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for side in (-1, 1):
            for dy, dx, ly in ((-6, 11, -13), (0, 12, -1), (6, 11, 12)):
                cr.move_to(c + side * 3, c + dy)
                cr.line_to(c + side * dx, c + ly)
            cr.stroke()
        # abdomen, thorax, head
        cr.save(); cr.translate(c, c + 8.5); cr.scale(0.82, 1.25)
        cr.arc(0, 0, 7.2, 0, 2 * math.pi); cr.fill(); cr.restore()
        cr.save(); cr.translate(c, c - 2.5); cr.scale(0.95, 1.0)
        cr.arc(0, 0, 6.4, 0, 2 * math.pi); cr.fill(); cr.restore()
        cr.save(); cr.translate(c, c - 12.5); cr.scale(1.12, 0.85)
        cr.arc(0, 0, 5.0, 0, 2 * math.pi); cr.fill(); cr.restore()
        surf.write_to_png(path)
    return d, name

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator

    _HAVE_INDICATOR = True
except (ValueError, ImportError):  # pragma: no cover - depends on the desktop
    AppIndicator = None
    _HAVE_INDICATOR = False


class Tray:
    def __init__(self, title: str, subtitle: str, actions: dict, icon: str | None = None):
        self.available = _HAVE_INDICATOR
        self.menu = Gtk.Menu()
        self._items: dict[str, Gtk.MenuItem] = {}

        header = Gtk.MenuItem(label=title)
        header.set_sensitive(False)
        self.menu.append(header)
        info = Gtk.MenuItem(label=subtitle)
        info.set_sensitive(False)
        self.menu.append(info)
        self.menu.append(Gtk.SeparatorMenuItem())

        for key, (label, callback) in actions.items():
            if label is None:
                self.menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            self.menu.append(item)
            self._items[key] = item

        self.menu.show_all()
        self.indicator = None
        if _HAVE_INDICATOR:
            icon_dir, icon_name = ensure_icon()
            self.indicator = AppIndicator.Indicator.new_with_path(
                "desktopfly", icon or icon_name,
                AppIndicator.IndicatorCategory.APPLICATION_STATUS, icon_dir,
            )
            self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.indicator.set_title(title)
            self.indicator.set_label("🪰", "🪰")
            self.indicator.set_menu(self.menu)

    def set_label(self, key: str, text: str) -> None:
        if key in self._items:
            self._items[key].set_label(text)

    def set_visible(self, key: str, visible: bool) -> None:
        if key in self._items:
            self._items[key].set_visible(visible)

    def popup(self) -> None:
        """Fallback for desktops with no tray: show the menu at the pointer."""
        self.menu.popup_at_pointer(None)
