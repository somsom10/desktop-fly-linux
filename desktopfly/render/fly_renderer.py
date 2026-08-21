"""Software renderer for the procedural fly body.

Replaces upstream's SceneKit scene.  Because the camera is orthographic and
top-down (see ``geometry.py``), every part has an exact analytic 2D image, so
the whole fly is a couple of dozen cairo paths — no mesh, no depth buffer, no
GPU context on a 32-bit ARGB visual.

Parts are painted in a fixed back-to-front order that matches their local Z:
shadow, legs, proboscis, abdomen, thorax, head, eyes, antennae, wings.  With a
fixed top-down view that order is stable, so no per-frame sorting is needed.
"""

from __future__ import annotations

import math

import cairo
import numpy as np

from ..body import model as M
from ..body.fly import State
from .geometry import (
    PROJECT,
    ellipse_from_matrix,
    ellipse_outline,
    euler_matrix,
    leg_joints,
)

# Upstream's key light: a directional light with eulerAngles (-0.35, 0.30, 0),
# which in SceneKit shines along its own -Z axis.
_LIGHT_DIR = euler_matrix(-0.35, 0.30, 0.0) @ np.array([0.0, 0.0, -1.0])
_TO_LIGHT = -_LIGHT_DIR
_LIGHT_2D = PROJECT @ _TO_LIGHT
_n = np.linalg.norm(_LIGHT_2D)
_LIGHT_2D = _LIGHT_2D / _n if _n > 1e-6 else np.array([0.0, -1.0])

AMBIENT = 0.42  # upstream: ambient 550 against key 1000
SIZE = 160  # overlay tile; the fly spans ~55 px at max flight scale


def _shade(color, lit: float):
    return tuple(min(1.0, c * lit) for c in color)


def _ellipsoid(cr, centre, mat, radii, color, specular=0.25, alpha=1.0):
    """Draw one ellipsoid as its exact projected ellipse with a lit gradient."""
    m = mat @ np.diag(radii)
    a, b, ang = ellipse_from_matrix(m)
    if a < 0.25 or b < 0.25:
        return
    cx, cy = centre
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(ang)
    cr.scale(max(a, 0.01), max(b, 0.01))
    # highlight sits toward the light; radius 1 because we are in unit space
    hx = 0.42 * float(_LIGHT_2D[0] * math.cos(-ang) - _LIGHT_2D[1] * math.sin(-ang))
    hy = 0.42 * float(_LIGHT_2D[0] * math.sin(-ang) + _LIGHT_2D[1] * math.cos(-ang))
    grad = cairo.RadialGradient(hx, hy, 0.05, 0.0, 0.0, 1.25)
    lit = AMBIENT + (1 - AMBIENT)
    grad.add_color_stop_rgba(0.0, *_shade(color, lit + specular * 0.7), alpha)
    grad.add_color_stop_rgba(0.55, *_shade(color, 0.84), alpha)
    grad.add_color_stop_rgba(1.0, *_shade(color, AMBIENT + 0.18), alpha)
    cr.set_source(grad)
    cr.arc(0.0, 0.0, 1.0, 0.0, 2 * math.pi)
    cr.fill()
    cr.restore()


def _capsule(cr, p0, p1, width, color, alpha=1.0):
    if width < 0.4:
        width = 0.4
    cr.set_source_rgba(*color, alpha)
    cr.set_line_width(width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(float(p0[0]), float(p0[1]))
    cr.line_to(float(p1[0]), float(p1[1]))
    cr.stroke()


def _polygon(cr, pts):
    cr.move_to(float(pts[0][0]), float(pts[0][1]))
    for p in pts[1:]:
        cr.line_to(float(p[0]), float(p[1]))
    cr.close_path()


class FlyRenderer:
    """Renders one fly into a reusable ARGB tile."""

    def __init__(self, size: int = SIZE, zoom: float = 1.0):
        self.size = size
        self.zoom = zoom
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        self.cr = cairo.Context(self.surface)
        self.cr.set_antialias(cairo.ANTIALIAS_GOOD)

    def render(self, fly) -> cairo.ImageSurface:
        cr = self.cr
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()
        cr.set_operator(cairo.OPERATOR_OVER)

        c = self.size / 2.0
        cr.save()
        cr.translate(c, c)
        if self.zoom != 1.0:
            cr.scale(self.zoom, self.zoom)

        rot = euler_matrix(fly.model.root.euler_x, fly.model.root.euler_y,
                           fly.model.root.euler_z)
        mat = rot * fly.scale  # uniform node scale folds into the matrix

        def screen(local) -> np.ndarray:
            return PROJECT @ (mat @ np.asarray(local, dtype=float))

        self._draw_shadow(cr, fly, mat)
        self._draw_legs(cr, fly, mat)

        self._draw_proboscis(cr, fly, mat, screen)

        self._draw_abdomen(cr, fly, mat, screen)

        tc, tr, ts = M.THORAX
        _ellipsoid(cr, screen(tc), mat, np.array(ts) * tr, M.BODY_BROWN, specular=0.35)

        hc, hr, hs = M.HEAD
        _ellipsoid(cr, screen(hc), mat, np.array(hs) * hr, M.HEAD_BROWN)

        for ec, er, es in (M.EYE_L, M.EYE_R):
            _ellipsoid(cr, screen(ec), mat, np.array(es) * er, M.EYE_RED, specular=0.55)

        self._draw_antennae(cr, mat, screen)
        self._draw_wings(cr, fly, mat, screen)

        cr.restore()
        self.surface.flush()
        return self.surface

    # ------------------------------------------------------------------

    def _draw_shadow(self, cr, fly, mat):
        """Soft ground shadow; it slides away and fades as the fly climbs."""
        alt = max(0.0, fly.alt)
        offset = _LIGHT_2D * (6.0 + 46.0 * alt)
        rad = 9.0 * fly.scale / M.FLY_SCALE
        alpha = 0.30 * max(0.0, 1.0 - 0.55 * alt)
        if alpha <= 0.01:
            return
        cr.save()
        cr.translate(-float(offset[0]), -float(offset[1]))
        cr.scale(1.0, 0.72)
        grad = cairo.RadialGradient(0, 0, rad * 0.2, 0, 0, rad * 1.5)
        grad.add_color_stop_rgba(0.0, 0.02, 0.02, 0.03, alpha)
        grad.add_color_stop_rgba(1.0, 0.02, 0.02, 0.03, 0.0)
        cr.set_source(grad)
        cr.arc(0, 0, rad * 1.5, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

    def _draw_proboscis(self, cr, fly, mat, screen):
        """The proboscis, extended into a sugar drop while feeding.

        Drawn before the head, so retracted it is hidden under the head's
        silhouette and only the part that reaches past the head shows — which is
        what extension actually looks like from above.
        """
        ext = getattr(fly, "proboscis_ext", 0.0)
        pc, pr, ps = M.PROBOSCIS
        centre = (pc[0], pc[1] + 3.4 * ext, pc[2] - 1.8 * ext)
        radii = np.array(ps) * pr
        radii[1] *= 1.0 + 1.7 * ext  # it lengthens as it unfolds
        colour = M.PROBOSCIS_COLOR
        if ext > 0.01:  # fleshier when everted
            colour = tuple(c + (t - c) * 0.45 * ext
                           for c, t in zip(colour, (0.55, 0.36, 0.30)))
        _ellipsoid(cr, screen(centre), mat, radii, colour)
        if ext > 0.25:  # the labellum dabbing at the drop
            tip = (pc[0], pc[1] + 5.6 * ext, pc[2] - 2.6 * ext)
            _ellipsoid(cr, screen(tip), mat, np.array([0.85, 0.7, 0.6]) * (0.6 + 0.5 * ext),
                       (0.60, 0.40, 0.33))

    def _draw_legs(self, cr, fly, mat):
        for leg in fly.model.legs:
            j = leg_joints(leg, mat)
            p = j @ PROJECT.T
            s = fly.scale
            _capsule(cr, p[0], p[1], 0.96 * s, M.LEG_COLOR)
            _capsule(cr, p[1], p[2], 0.76 * s, M.LEG_COLOR)
            _capsule(cr, p[2], p[3], 0.48 * s, M.TARSUS_COLOR)

    # Upstream paints the abdomen with a 64x128 texture (``abdomenTexture()``)
    # whose dark tergite bands run perpendicular to the sphere's Y axis. Mapped
    # back to normalised body-Y, the dark bands sit at these spans -- note the
    # broad dark tip at the posterior (-1.0).
    _TERGITES = ((-1.0, -0.594), (-0.406, -0.25), (-0.0625, 0.094), (0.281, 0.422))

    def _draw_abdomen(self, cr, fly, mat, screen):
        ab = fly.model.abdomen
        radii = np.array([ab.scale_x, ab.scale_y, ab.scale_z]) * M.ABDOMEN[1]
        centre = screen((ab.x, ab.y, ab.z))
        _ellipsoid(cr, centre, mat, radii, M.ABDOMEN_BASE)

        m = mat @ np.diag(radii)
        a, b, ang = ellipse_from_matrix(m)
        if a < 1.2 or b < 1.2:
            return
        # The bands must follow the *body's* Y axis, not the projected ellipse's
        # major axis -- those coincide only when the fly is axis-aligned.
        y_axis = PROJECT @ (m @ np.array([0.0, 1.0, 0.0]))
        length = float(np.linalg.norm(y_axis))
        if length < 1.0:
            return
        theta = math.atan2(float(y_axis[1]), float(y_axis[0]))

        cr.save()
        base = cr.get_matrix()
        cr.translate(float(centre[0]), float(centre[1]))
        cr.rotate(ang)
        cr.scale(a, b)
        cr.arc(0.0, 0.0, 1.0, 0.0, 2 * math.pi)
        cr.clip()
        # keep the clip, drop back to the unscaled frame to lay the bands down
        cr.set_matrix(base)
        cr.translate(float(centre[0]), float(centre[1]))
        cr.rotate(theta)
        span = 2.2 * max(a, b)
        cr.set_source_rgba(*M.ABDOMEN_STRIPE, 0.92)
        for n0, n1 in self._TERGITES:
            cr.rectangle(n0 * length, -span / 2, (n1 - n0) * length, span)
        cr.fill()
        cr.restore()

    def _draw_antennae(self, cr, mat, screen):
        for side in (-1.0, 1.0):
            base = np.array([side * M.ANTENNA_OFFSET, 11.6, 6.3])
            r = euler_matrix(-1.15, 0.0, side * 0.35)
            tip = base + (r @ np.array([0.0, 2.2, 0.0]))
            _capsule(cr, screen(base), screen(tip), 0.32, M.ANTENNA_COLOR)

    def _draw_wings(self, cr, fly, mat, screen):
        flying = fly.state is State.FLYING
        for i, w in enumerate(fly.model.wings):
            wm = mat @ euler_matrix(w.euler_x, w.euler_y, w.euler_z)
            offset = screen((w.x, w.y, w.z))
            # upstream's wing is an oval path spanning y -15.5..1.0, x +-2.6
            pts = ellipse_outline(0.0, -7.25, 2.6, 8.25, wm) @ PROJECT.T + offset
            _polygon(cr, pts)
            cr.set_source_rgba(*M.WING_COLOR, 0.30 if flying else 0.36)
            cr.fill_preserve()
            cr.set_source_rgba(0.55, 0.57, 0.60, 0.55)
            cr.set_line_width(0.45 * fly.scale)
            cr.stroke()
        if not flying:
            return
        for b in fly.model.blur_wings:
            if b.hidden:
                continue
            bm = mat @ euler_matrix(b.euler_x, b.euler_y, b.euler_z)
            radii = np.array([b.scale_x, b.scale_y, b.scale_z])
            _ellipsoid(cr, screen((b.x, b.y, b.z)), bm, radii,
                       (0.85, 0.86, 0.88), alpha=max(0.05, b.opacity))
