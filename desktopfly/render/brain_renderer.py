"""The live brain window: 23,210 real FlyWire soma positions, spikes flashing.

Port of ``BrainView.swift``.  Upstream feeds two SceneKit point-cloud geometries
to Metal with additive blending and depth testing off; here the same thing is
done by scattering the projected points into a float accumulator with NumPy,
which needs no GL context and is comfortably fast at 30 fps for 23k points.

Unlike the desktop overlay this view is **perspective** (upstream: 46 deg FOV,
camera at (0, 0.6, 29)), so the projection here divides by depth.

Additive blending means the draw order does not matter, which is why no depth
sorting appears anywhere below.
"""

from __future__ import annotations

import math

import numpy as np

from .geometry import euler_matrix

# super_class palette, index order from etl.py (BrainView.swift CLASS_COLORS)
CLASS_COLORS = np.array([
    [0.16, 0.22, 0.34],  # optic — dim blue (the majority, kept subtle)
    [0.45, 0.33, 0.16],  # central — amber
    [0.14, 0.36, 0.34],  # sensory — teal
    [0.10, 0.48, 0.62],  # visual_projection — cyan
    [0.38, 0.22, 0.55],  # visual_centrifugal — violet
    [0.62, 0.28, 0.10],  # descending — orange
    [0.20, 0.45, 0.18],  # ascending — green
    [0.55, 0.14, 0.14],  # motor — red
    [0.50, 0.25, 0.40],  # endocrine — pink
], dtype=np.float32)

ROLE_COLORS = {
    "lc4": (0.15, 0.85, 1.00),
    "lplc2": (0.15, 0.85, 1.00),
    "dna01": (1.00, 0.55, 0.10),
    "dna02": (1.00, 0.55, 0.10),
    "mdn": (1.00, 0.20, 0.80),
    "dnp09": (0.25, 1.00, 0.35),
    "dng11": (0.75, 0.55, 1.00),
    "escw": (1.00, 0.35, 0.25),
    "gf": (1.00, 0.95, 0.40),
}
DEFAULT_ROLE_COLOR = (0.45, 0.45, 0.50)

BACKGROUND = (0.03, 0.035, 0.06)
CAMERA_POS = np.array([0.0, 0.6, 29.0])
FOV_DEG = 46.0
# upstream: repeatForever(rotateBy y: 0.35, duration: 6)
SPIN_RATE = 0.35 / 6.0
EXPOSURE = 1.45  # tone-map strength; see render()
TILT_X = -0.15


class BrainRenderer:
    def __init__(self, points, sim, width: int = 340, height: int = 280):
        self.width = width
        self.height = height
        self.sim = sim
        self.points = points.positions.astype(np.float32)
        self.point_colors = CLASS_COLORS[np.clip(points.class_index, 0, len(CLASS_COLORS) - 1)]
        self.circuit_pos = sim.positions.astype(np.float32)
        self.circuit_colors = np.array(
            [ROLE_COLORS.get(r, DEFAULT_ROLE_COLOR) for r in sim.roles], dtype=np.float32
        )
        self.gf_idx = np.asarray(sim.gf)
        self.angle = 0.0
        self.paused = False
        self._flashes: list[list] = []  # [neuron, age, is_gf]
        self._accum = np.zeros((height, width, 3), dtype=np.float32)
        self._rgba = np.zeros((height, width, 4), dtype=np.uint8)

    # ------------------------------------------------------------ camera

    def _matrix(self) -> np.ndarray:
        return euler_matrix(TILT_X, self.angle, 0.0)

    def _project(self, pts: np.ndarray, matrix: np.ndarray):
        """World -> screen. Returns (x, y, depth, visible-mask)."""
        v = pts @ matrix.T - CAMERA_POS
        depth = -v[:, 2]
        ok = depth > 1.0
        f = (self.height / 2.0) / math.tan(math.radians(FOV_DEG) / 2.0)
        safe = np.where(ok, depth, 1.0)
        x = v[:, 0] / safe * f + self.width / 2.0
        y = -v[:, 1] / safe * f + self.height / 2.0
        return x, y, depth, ok

    def unproject_ray(self, sx: float, sy: float):
        """Screen point -> (origin, direction) in brain-local coordinates."""
        f = (self.height / 2.0) / math.tan(math.radians(FOV_DEG) / 2.0)
        d_cam = np.array([(sx - self.width / 2.0) / f, -(sy - self.height / 2.0) / f, -1.0])
        m = self._matrix()
        inv = m.T  # rotation matrices are orthonormal
        origin = inv @ CAMERA_POS
        direction = inv @ d_cam
        direction /= max(1e-6, np.linalg.norm(direction))
        return origin, direction

    def nearest_neurons(self, sx: float, sy: float, max_count: int = 60):
        """Circuit neurons near the click ray — upstream's stimulation picker."""
        origin, direction = self.unproject_ray(sx, sy)
        ap = self.circuit_pos - origin
        along = ap @ direction
        perp = np.linalg.norm(ap - np.outer(along, direction), axis=1)
        best = int(np.argmin(perp))
        anchor = self.circuit_pos[best]
        dist = np.linalg.norm(self.circuit_pos - anchor, axis=1)
        picked = np.flatnonzero(dist < 2.2)
        if picked.size < 4:
            picked = np.argsort(dist)[:6]
        elif picked.size > max_count:
            picked = picked[np.argsort(dist[picked])[:max_count]]
        return picked, anchor

    # ------------------------------------------------------------ render

    def flash(self, neuron: int, is_gf: bool) -> None:
        self._flashes.append([neuron, 0.0, is_gf])
        if len(self._flashes) > 48:
            del self._flashes[: len(self._flashes) - 48]

    def drain_spikes(self) -> None:
        bus = getattr(self.sim, "spike_bus", None)
        if bus is None:
            return
        for neuron, is_gf in bus.pop_all():
            self.flash(neuron, is_gf)

    def _scatter(self, x, y, ok, colors, gain=1.0, spread=0.0) -> None:
        xi = np.rint(x).astype(np.int64)
        yi = np.rint(y).astype(np.int64)
        m = ok & (xi >= 0) & (xi < self.width) & (yi >= 0) & (yi < self.height)
        if not m.any():
            return
        idx = yi[m] * self.width + xi[m]
        cols = colors[m] * gain
        flat = self._accum.reshape(-1, 3)
        n = flat.shape[0]
        for ch in range(3):
            flat[:, ch] += np.bincount(idx, weights=cols[:, ch], minlength=n)[:n]
        # Upstream renders each soma as a disc of 0.7-1.6 screen px, so dense
        # regions accumulate toward white. Single pixels would look far dimmer;
        # bleeding a fraction into the 4-neighbourhood reproduces that build-up.
        if spread > 0.0:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                xs, ys = xi[m] + dx, yi[m] + dy
                inside = (xs >= 0) & (xs < self.width) & (ys >= 0) & (ys < self.height)
                if not inside.any():
                    continue
                idx2 = ys[inside] * self.width + xs[inside]
                c2 = cols[inside] * spread
                for ch in range(3):
                    flat[:, ch] += np.bincount(idx2, weights=c2[:, ch], minlength=n)[:n]

    def render(self, dt: float) -> np.ndarray:
        if not self.paused:
            self.angle += SPIN_RATE * dt
        matrix = self._matrix()
        self._accum[:] = 0.0

        x, y, _d, ok = self._project(self.points, matrix)
        self._scatter(x, y, ok, self.point_colors, gain=1.15, spread=0.38)

        cx, cy, _cd, cok = self._project(self.circuit_pos, matrix)
        self._scatter(cx, cy, cok, self.circuit_colors, gain=0.60, spread=0.55)

        # the two giant fibers get their own steady glow
        for i in self.gf_idx:
            if cok[i]:
                self._blob(cx[i], cy[i], 3.4, (1.0, 0.85, 0.25), 0.35)

        # spike flashes, aged out
        alive = []
        for f in self._flashes:
            neuron, age, is_gf = f
            life = 0.6 if is_gf else 0.28
            if age >= life or not cok[neuron]:
                if age < life:
                    alive.append(f)
                continue
            k = 1.0 - age / life
            if is_gf:
                self._blob(cx[neuron], cy[neuron], 5.0, (1.0, 0.9, 0.45), k)
            else:
                self._blob(cx[neuron], cy[neuron], 2.0, (0.75, 0.95, 1.0), k * 0.8)
            f[1] = age + dt
            alive.append(f)
        self._flashes = alive

        # Soft tone-map instead of a hard clip. Additive point clouds pile up
        # hugely where a lobe is seen face-on, and clipping turned it into a
        # flat white blob as the brain rotated; 1 - exp(-x) keeps that structure
        # while still saturating gently.
        tone = 1.0 - np.exp(-self._accum * EXPOSURE)
        bg = np.asarray(BACKGROUND, dtype=np.float32)
        rgb = bg + (1.0 - bg) * tone
        # straight RGBA for GdkPixbuf (the brain window is opaque, so there is
        # no premultiplied-ARGB cairo surface in this path at all)
        self._rgba[..., :3] = (np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)
        self._rgba[..., 3] = 255
        return self._rgba

    def _blob(self, x: float, y: float, radius: float, color, strength: float) -> None:
        r = int(math.ceil(radius))
        x0, x1 = int(x) - r, int(x) + r + 1
        y0, y1 = int(y) - r, int(y) + r + 1
        x0c, y0c = max(0, x0), max(0, y0)
        x1c, y1c = min(self.width, x1), min(self.height, y1)
        if x1c <= x0c or y1c <= y0c:
            return
        ys, xs = np.mgrid[y0c:y1c, x0c:x1c]
        d2 = (xs - x) ** 2 + (ys - y) ** 2
        falloff = np.exp(-d2 / max(0.5, radius * radius * 0.42)) * strength
        self._accum[y0c:y1c, x0c:x1c] += falloff[..., None] * np.asarray(color, dtype=np.float32)

    def region_name(self, picked) -> str:
        """Label for a stimulated cluster — upstream's ``regionName``."""
        roles = [self.sim.roles[i] for i in picked]
        major = max(set(roles), key=roles.count)

        def side_suffix(role: str) -> str:
            left = sum(1 for i in picked
                       if self.sim.roles[i] == role and self.circuit_pos[i][0] < 0)
            right = sum(1 for i in picked if self.sim.roles[i] == role) - left
            if left == right:
                return ""
            return " · left" if left > right else " · right"

        if major in ("lc4", "lplc2"):
            return f"⚡ Looming detectors (LC4/LPLC2){side_suffix(major)}"
        if major == "gf":
            return "⚡ Giant Fiber (DNp01) — escape!"
        if major in ("dna01", "dna02"):
            return f"⚡ Steering neurons (DNa01/02){side_suffix(major)}"
        if major == "dnp09":
            return "⚡ Walking command (DNp09)"
        if major == "dng11":
            return "⚡ Grooming command (DNg11)"
        if major == "escw":
            return "⚡ Escape-wing DNs (DNp02/04/11)"
        if major == "mdn":
            return "⚡ Moonwalker neurons (MDN)"
        other = next((i for i in picked if self.sim.roles[i] == "other"), picked[0])
        t = self.sim.types[other] or "central"
        return f"⚡ {'central' if t == '?' else t} neurons"
