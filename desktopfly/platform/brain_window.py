"""The interactive brain window.

Port of ``BrainWindowController`` from upstream's ``BrainView.swift``: an
always-on-top utility window showing the live point cloud, where hovering holds
the rotation still and clicking "optogenetically" stimulates the ~60 circuit
neurons nearest the click ray.

Upstream uses a non-activating ``NSPanel`` so the window never steals focus.
The closest GTK equivalent is a utility-hint, keep-above window that does not
accept focus.
"""

from __future__ import annotations

import numpy as np

from .gtkcompat import Gdk, GdkPixbuf, GLib, Gtk


class BrainWindow:
    WIDTH = 340
    HEIGHT = 280

    def __init__(self, renderer, sim, on_stimulate=None, on_close=None):
        self.renderer = renderer
        self.sim = sim
        self.on_stimulate = on_stimulate
        # The brain window is the app's only real window, so its close button is
        # the obvious way to quit -- "Show/Hide Brain" in the tray is the way to
        # dismiss it without quitting.
        self.on_close = on_close

        self.win = Gtk.Window(title="Fly Brain — FlyWire v783 (click = stimulate)")
        self.win.set_default_size(self.WIDTH, self.HEIGHT)
        self.win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_resizable(False)
        self.win.connect("delete-event", self._on_delete)

        overlay = Gtk.Overlay()
        self.image = Gtk.Image()
        events = Gtk.EventBox()
        events.add(self.image)
        events.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        events.connect("button-press-event", self._on_click)
        events.connect("enter-notify-event", self._on_enter)
        events.connect("leave-notify-event", self._on_leave)
        overlay.add(events)

        self.label = Gtk.Label()
        self.label.set_halign(Gtk.Align.CENTER)
        self.label.set_valign(Gtk.Align.END)
        self.label.set_margin_bottom(10)
        self.label.set_no_show_all(True)
        ctx = self.label.get_style_context()
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"label { background-color: rgba(0,0,0,0.62); color: #f2f2f2;"
            b" padding: 3px 8px; border-radius: 6px; font-size: 11px; }"
        )
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        overlay.add_overlay(self.label)
        self.win.add(overlay)
        self._label_timer = None
        self._visible = False

    # ------------------------------------------------------------ events

    def _on_delete(self, *_):
        if self.on_close is not None:
            self.on_close()
            return True
        self.hide()
        return True  # stop the default destroy

    def _on_enter(self, *_):
        self.renderer.paused = True  # hold the rotation while aiming
        return False

    def _on_leave(self, *_):
        self.renderer.paused = False
        return False

    def _on_click(self, _widget, event):
        picked, _anchor = self.renderer.nearest_neurons(event.x, event.y)
        if len(picked) == 0:
            return False
        self.sim.stimulate(picked, strength=0.25, duration_ms=400)
        for i in picked[:16]:
            self.renderer.flash(int(i), False)
        self._show_label(self.renderer.region_name(picked))
        if self.on_stimulate:
            self.on_stimulate(picked)
        return True

    def _show_label(self, text: str) -> None:
        self.label.set_text(text)
        self.label.show()
        if self._label_timer:
            GLib.source_remove(self._label_timer)
        self._label_timer = GLib.timeout_add(2200, self._hide_label)

    def _hide_label(self):
        self.label.hide()
        self._label_timer = None
        return False

    # ------------------------------------------------------------ render

    def draw(self, dt: float) -> None:
        if not self._visible:
            return
        self.renderer.drain_spikes()
        rgba = self.renderer.render(dt)
        h, w = rgba.shape[:2]
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(np.ascontiguousarray(rgba).tobytes()),
            GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4,
        )
        self.image.set_from_pixbuf(pixbuf)

    # ----------------------------------------------------------- control

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self) -> None:
        self._visible = True
        self.win.show_all()
        self.label.hide()
        self.win.present()

    def hide(self) -> None:
        self._visible = False
        self.win.hide()

    def toggle(self) -> None:
        self.hide() if self._visible else self.show()

    def place_bottom_right(self, monitor: dict) -> None:
        x = monitor["x"] + monitor["width"] - self.WIDTH - 24
        y = monitor["y"] + monitor["height"] - self.HEIGHT - 64
        self.win.move(x, y)
