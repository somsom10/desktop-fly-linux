"""Draws a sugar drop: a small glossy bead sitting on the desktop."""

from __future__ import annotations

import math

import cairo

TILE = 48  # comfortably larger than the biggest drop plus its shadow


class SugarRenderer:
    def __init__(self, tile: int = TILE):
        self.tile = tile
        self.surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, tile, tile)
        self.cr = cairo.Context(self.surface)
        self.cr.set_antialias(cairo.ANTIALIAS_GOOD)

    def render(self, drop) -> cairo.ImageSurface:
        cr = self.cr
        cr.save()
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.restore()
        cr.set_operator(cairo.OPERATOR_OVER)

        c = self.tile / 2.0
        r = max(2.0, drop.draw_radius)

        # contact shadow, so the bead sits on the desktop rather than floating
        cr.save()
        cr.translate(c, c + r * 0.30)
        cr.scale(1.0, 0.42)
        g = cairo.RadialGradient(0, 0, r * 0.15, 0, 0, r * 1.25)
        g.add_color_stop_rgba(0.0, 0.25, 0.15, 0.02, 0.34)
        g.add_color_stop_rgba(1.0, 0.25, 0.15, 0.02, 0.0)
        cr.set_source(g)
        cr.arc(0, 0, r * 1.25, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        # the bead: amber, translucent, brighter at the rim than the centre
        body = cairo.RadialGradient(c - r * 0.30, c - r * 0.34, r * 0.10, c, c, r * 1.06)
        body.add_color_stop_rgba(0.0, 1.00, 0.93, 0.68, 0.96)
        body.add_color_stop_rgba(0.45, 0.98, 0.76, 0.30, 0.92)
        body.add_color_stop_rgba(0.88, 0.80, 0.52, 0.12, 0.90)
        body.add_color_stop_rgba(1.0, 0.62, 0.38, 0.08, 0.72)
        cr.set_source(body)
        cr.arc(c, c, r, 0, 2 * math.pi)
        cr.fill()

        # specular glint
        cr.save()
        cr.translate(c - r * 0.34, c - r * 0.38)
        cr.scale(1.0, 0.72)
        gl = cairo.RadialGradient(0, 0, 0, 0, 0, r * 0.40)
        gl.add_color_stop_rgba(0.0, 1, 1, 1, 0.92)
        gl.add_color_stop_rgba(1.0, 1, 1, 1, 0.0)
        cr.set_source(gl)
        cr.arc(0, 0, r * 0.40, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

        self.surface.flush()
        return self.surface
