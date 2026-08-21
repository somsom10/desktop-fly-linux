"""Click-through, always-on-top, transparent overlay windows on X11/XWayland.

This is the Linux stand-in for the macOS original's single full-screen
``NSWindow`` with ``ignoresMouseEvents`` + ``level = .floating``.

Two things forced the design away from a straight translation:

* **No full-screen overlay.** Painting a 1920x1880 ARGB surface every frame is
  wasteful when the subject is a 40 px fly.  Instead each fly gets its own
  small window that is *moved* to follow it.  Rendering cost becomes
  independent of screen size.
* **No GI/cairo bridge.** ``python3-gi-cairo`` is not installed and installing
  it needs a password, so the ``draw`` signal (which hands Python a
  ``cairo.Context``) is unusable, as is ``input_shape_combine_region`` (which
  wants a ``cairo.Region``).  We therefore draw into a plain pycairo
  ``ImageSurface``, blit it through ``GdkPixbuf``, and set the input shape with
  raw XShape calls via python-xlib.
"""

from __future__ import annotations

import math

import cairo
import numpy as np

from .gtkcompat import Gdk, GdkPixbuf, GLib, Gtk

from Xlib import display as _xdisplay  # noqa: E402
from Xlib.ext import shape as _shape  # noqa: E402

_XDISPLAY = None


def _xdisp():
    global _XDISPLAY
    if _XDISPLAY is None:
        _XDISPLAY = _xdisplay.Display()
    return _XDISPLAY


_CSS_INSTALLED = False


def _install_css(screen):
    """Strip the theme's opaque window background.

    Without the GI/cairo bridge we cannot clear the background in a ``draw``
    handler, so we ask GTK's CSS engine to make the window and its child image
    fully transparent instead.
    """
    global _CSS_INSTALLED
    if _CSS_INSTALLED:
        return
    css = Gtk.CssProvider()
    css.load_from_data(b"window, image { background-color: rgba(0,0,0,0); }")
    Gtk.StyleContext.add_provider_for_screen(
        screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _CSS_INSTALLED = True


def surface_to_pixbuf(surf: cairo.ImageSurface, w: int, h: int) -> GdkPixbuf.Pixbuf:
    """cairo ARGB32 (premultiplied, native-endian) -> GdkPixbuf RGBA (straight)."""
    stride = surf.get_stride()
    buf = np.frombuffer(surf.get_data(), dtype=np.uint8)
    buf = buf.reshape(h, stride // 4, 4)[:, :w]
    alpha = buf[..., 3]
    # little-endian ARGB32 lands in memory as B,G,R,A
    rgb = buf[..., [2, 1, 0]].astype(np.uint16)
    safe = np.maximum(alpha, 1).astype(np.uint16)[..., None]
    out = np.empty((h, w, 4), dtype=np.uint8)
    np.minimum(rgb * 255 // safe, 255, out=out[..., :3], casting="unsafe")
    out[..., 3] = alpha
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(out.tobytes()), GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4
    )


class Overlay:
    """One transparent, click-through window that follows a point on screen."""

    def __init__(self, width: int, height: int, name: str = "desktopfly",
                 on_click=None):
        """``on_click`` makes the overlay accept button presses.

        Without it the window is fully click-through. With it, the clickable
        area still defaults to nothing until :meth:`set_input_circle` carves one
        out, so an interactive overlay only ever blocks the pixels it actually
        draws on.
        """
        self.width = width
        self.height = height
        self._on_click = on_click
        self._input_radius = None
        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_name(name)
        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is None:
            raise RuntimeError(
                "no 32-bit RGBA visual — a compositing X server is required"
            )
        self.win.set_visual(visual)
        _install_css(screen)
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(False)
        self.win.set_focus_on_map(False)
        self.win.set_decorated(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_default_size(width, height)
        self.win.resize(width, height)
        self.image = Gtk.Image()
        self.win.add(self.image)
        if on_click is not None:
            self.win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.win.connect("button-press-event", self._handle_click)
        self.win.connect("realize", self._on_realize)
        # GDK rewrites the input shape while mapping the window, so a shape set
        # during "realize" is silently discarded. It has to be (re)applied once
        # the MapNotify has actually been processed.
        self.win.connect("map-event", self._on_map)
        self._x = -10_000
        self._y = -10_000
        self.win.show_all()
        self.gdk_window = self.win.get_window()
        self.gdk_window.move(self._x, self._y)

    def _on_realize(self, *_):
        gw = self.win.get_window()
        # Bypass the window manager entirely: no decoration, no focus stealing,
        # and Mutter stacks override-redirect windows above ordinary ones.
        gw.set_override_redirect(True)

    def _on_map(self, *_):
        if self._on_click is None:
            self._make_click_through(self.win.get_window().get_xid())
        else:
            self._apply_input_circle()
        return False

    def _handle_click(self, _widget, event):
        if self._on_click is not None:
            self._on_click(event)
        return True

    def set_input_circle(self, radius: float) -> None:
        """Restrict the clickable area to a disc of ``radius`` at the centre."""
        radius = max(0.0, float(radius))
        if self._input_radius is not None and abs(radius - self._input_radius) < 0.5:
            return
        self._input_radius = radius
        if self.win.get_mapped():
            self._apply_input_circle()

    def _apply_input_circle(self) -> None:
        """Approximate a disc with one rectangle per scanline."""
        xid = self.gdk_window.get_xid()
        r = self._input_radius
        rects = []
        if r:
            cx = cy = self.width / 2.0
            for dy in range(int(-r), int(r) + 1):
                half = math.sqrt(max(0.0, r * r - dy * dy))
                if half < 0.5:
                    continue
                rects.append({
                    "x": int(cx - half), "y": int(cy + dy),
                    "width": max(1, int(2 * half)), "height": 1,
                })
        d = _xdisp()
        win = d.create_resource_object("window", xid)
        win.shape_rectangles(_shape.SO.Set, _shape.SK.Input, 0, 0, 0, rects)
        d.sync()

    @staticmethod
    def _make_click_through(xid: int) -> None:
        """Empty XShape *input* region: pointer events fall through to whatever
        is underneath, while the window stays fully visible."""
        d = _xdisp()
        win = d.create_resource_object("window", xid)
        win.shape_rectangles(_shape.SO.Set, _shape.SK.Input, 0, 0, 0, [])
        d.sync()

    def move(self, x: int, y: int) -> None:
        x, y = int(x), int(y)
        if (x, y) != (self._x, self._y):
            self._x, self._y = x, y
            self.gdk_window.move(x, y)

    def blit(self, surf: cairo.ImageSurface) -> None:
        surf.flush()
        self.image.set_from_pixbuf(surface_to_pixbuf(surf, self.width, self.height))

    def raise_(self) -> None:
        """Restack this overlay above the other override-redirect overlays.

        Override-redirect windows stack in map order, so a sugar drop created
        after the fly would otherwise cover the fly standing on it.
        """
        self.gdk_window.raise_()

    def set_visible(self, visible: bool) -> None:
        self.win.set_visible(visible)

    def destroy(self) -> None:
        self.win.destroy()


def monitors() -> list[dict]:
    """Geometry of every connected monitor, in global screen coordinates."""
    display = Gdk.Display.get_default()
    out = []
    for i in range(display.get_n_monitors()):
        m = display.get_monitor(i)
        g = m.get_geometry()
        out.append(
            {
                "index": i,
                "name": m.get_model() or f"monitor-{i}",
                "x": g.x,
                "y": g.y,
                "width": g.width,
                "height": g.height,
                "primary": m.is_primary(),
            }
        )
    return out


def pointer_position() -> tuple[int, int]:
    """Global cursor position. Works under XWayland for the X11 pointer."""
    display = Gdk.Display.get_default()
    seat = display.get_default_seat()
    _screen, x, y = seat.get_pointer().get_position()
    return x, y
