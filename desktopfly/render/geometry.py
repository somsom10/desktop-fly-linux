"""Projection maths for the software renderer.

Upstream draws with SceneKit through an **orthographic** camera parked on +Z
looking down at the desktop plane, with ``orthographicScale = height/2`` — one
scene unit is exactly one pixel.  That makes an exact analytic renderer possible
and a triangle rasteriser unnecessary:

* an ellipsoid under orthographic projection is *exactly* an ellipse, whose axes
  are the singular values of the projected 2x3 transform;
* a capsule projects to a stadium — a round-capped thick line;
* any planar outline (the wings) projects to an affine image of itself.

So each body part is drawn as one cairo path with a gradient, which is both
faster and smoother at fly size (~40 px) than shading a mesh would be.

SceneKit applies ``eulerAngles`` in the order roll (Z), yaw (Y), pitch (X), so
the composed matrix is ``Rx @ Ry @ Rz``.
"""

from __future__ import annotations

import math

import numpy as np


def rot_x(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def rot_y(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def rot_z(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def euler_matrix(ex: float, ey: float, ez: float) -> np.ndarray:
    """SceneKit euler angles -> rotation matrix (applied Z, then Y, then X)."""
    return rot_x(ex) @ rot_y(ey) @ rot_z(ez)


# Screen projection: X right, Y up in scene space but down in cairo space.
PROJECT = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])


def project(points: np.ndarray) -> np.ndarray:
    """(..., 3) scene points -> (..., 2) screen offsets (y flipped)."""
    return points @ PROJECT.T


def ellipse_from_matrix(m: np.ndarray) -> tuple[float, float, float]:
    """Silhouette of the unit sphere transformed by ``m`` then projected.

    ``m`` is the 3x3 that maps the unit sphere onto the ellipsoid.  The image of
    the sphere under the 2x3 matrix ``PROJECT @ m`` is an ellipse; its semi-axes
    are that matrix's singular values and its orientation comes from the left
    singular vectors.

    Returns ``(semi_major, semi_minor, angle_radians)``.
    """
    a = PROJECT @ m
    u, s, _vt = np.linalg.svd(a)
    angle = math.atan2(u[1, 0], u[0, 0])
    return float(s[0]), float(s[1]), angle


def leg_joints(leg, root_matrix: np.ndarray) -> np.ndarray:
    """Forward kinematics for one leg -> 4 points (coxa, knee, ankle, tip).

    Mirrors upstream's nested SCNNode chain: each segment runs along its own +X
    axis, with the knee and ankle carrying fixed bend angles.
    """
    attach = np.asarray(leg.attach, dtype=float)
    # leg root: eulerAngles = (0, -lift, base_yaw + swing_sign * angle)
    r_root = root_matrix @ euler_matrix(0.0, leg.pitch, leg.yaw)
    p0 = root_matrix @ attach

    p1 = p0 + r_root @ np.array([leg.femur, 0.0, 0.0])
    r_knee = r_root @ euler_matrix(0.0, 0.75, -0.30 * leg.swing_sign)
    p2 = p1 + r_knee @ np.array([leg.tibia, 0.0, 0.0])
    r_ankle = r_knee @ euler_matrix(0.0, 0.35, -0.15 * leg.swing_sign)
    p3 = p2 + r_ankle @ np.array([leg.tarsus, 0.0, 0.0])
    return np.stack([p0, p1, p2, p3])


def ellipse_outline(cx: float, cy: float, rx: float, ry: float,
                    matrix: np.ndarray, samples: int = 28) -> np.ndarray:
    """A planar ellipse (in the local XY plane) transformed to 3D and projected.

    Used for the wings, whose outline is a flat oval that gets tilted through
    the wing-stroke arc.
    """
    t = np.linspace(0.0, 2 * math.pi, samples, endpoint=False)
    local = np.stack([cx + rx * np.cos(t), cy + ry * np.sin(t), np.zeros_like(t)], axis=1)
    return local @ matrix.T
