"""Visual smoke test: park a bright, labelled, click-through overlay on screen.

Run it, then screenshot with tools/shot.sh to confirm the overlay composites
above ordinary windows and that its alpha is respected.
"""
import sys, os, math, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import cairo
from desktopfly.platform.gtkcompat import Gtk, GLib
from desktopfly.platform.overlay import Overlay, monitors

W = H = 240
ov = Overlay(W, H, name="overlay-check")
mon = [m for m in monitors() if not m["primary"]][0]
cx = mon["x"] + mon["width"] // 2
cy = mon["y"] + mon["height"] // 2
surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
cr = cairo.Context(surf)
t0 = time.time()
n = [0]

def tick():
    n[0] += 1
    t = time.time() - t0
    cr.save(); cr.set_operator(cairo.OPERATOR_CLEAR); cr.paint(); cr.restore()
    # translucent disc + hard ring: shows both alpha blending and crisp edges
    cr.set_source_rgba(1.0, 0.25, 0.15, 0.55)
    cr.arc(W / 2, H / 2, 90, 0, 6.283); cr.fill()
    cr.set_source_rgba(0.1, 1.0, 0.35, 0.95); cr.set_line_width(6)
    cr.arc(W / 2, H / 2, 90, 0, 6.283); cr.stroke()
    cr.set_source_rgba(1, 1, 1, 1)
    cr.select_font_face("sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(22)
    cr.move_to(52, H / 2 + 8); cr.show_text("OVERLAY OK")
    cr.set_font_size(13)
    cr.move_to(72, H / 2 + 32); cr.show_text("t=%.1fs" % t)
    ov.blit(surf)
    ov.move(cx - W // 2 + int(70 * math.cos(t)), cy - H // 2 + int(40 * math.sin(t)))
    if t > float(os.environ.get("CHECK_SECONDS", "40")):
        Gtk.main_quit()
    return True

GLib.timeout_add(16, tick)
Gtk.main()
print("fps: %.1f" % (n[0] / (time.time() - t0)))
