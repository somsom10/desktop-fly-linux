"""Procedural fruit-fly body: skeleton spec and articulated node state.

FlyWire is a *brain* connectome — it contains no body geometry — so upstream
models the body by hand in SceneKit and the connectome only drives behaviour.
This module keeps the same skeleton numbers as upstream's ``buildFlyModel()``
and the same articulated state (leg angles, wing eulers, abdomen breathing) so
that behaviour code and renderer agree, without depending on a scene graph.

Local frame matches upstream: +Y forward, +Z up, ground at z = 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

FLY_SCALE = 1.15


@dataclass
class Node:
    """The handful of SCNNode properties the port actually reads or writes."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    euler_x: float = 0.0
    euler_y: float = 0.0
    euler_z: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    scale_z: float = 1.0
    opacity: float = 1.0
    hidden: bool = False


class Leg:
    """One articulated leg. ``angle`` swings it fore/aft, ``lift`` picks it up."""

    __slots__ = ("base_yaw", "swing_sign", "phase", "is_front", "attach",
                 "femur", "tibia", "tarsus", "angle", "lift")

    def __init__(self, attach, base_yaw, swing_sign, phase, is_front, femur, tibia, tarsus):
        self.attach = attach
        self.base_yaw = base_yaw
        self.swing_sign = swing_sign
        self.phase = phase
        self.is_front = is_front
        self.femur = femur
        self.tibia = tibia
        self.tarsus = tarsus
        self.angle = 0.0
        self.lift = 0.0

    @property
    def yaw(self) -> float:
        """Root yaw, matching upstream's ``eulerAngles.z``."""
        return self.base_yaw + self.swing_sign * self.angle

    @property
    def pitch(self) -> float:
        """Root pitch, matching upstream's ``eulerAngles.y`` (= ``-lift``)."""
        return -self.lift


# (side, attach, yaw offset, gait phase, is_front, femur, tibia, tarsus)
_LEG_Z = 4.5
LEG_SPECS = [
    (1, (3.1, 5.3, _LEG_Z), 0.95, 0.0, True, 4.2, 4.8, 3.2),
    (-1, (-3.1, 5.3, _LEG_Z), 0.95, 0.5, True, 4.2, 4.8, 3.2),
    (1, (3.7, 2.0, _LEG_Z), -0.10, 0.5, False, 4.8, 5.6, 3.8),
    (-1, (-3.7, 2.0, _LEG_Z), -0.10, 0.0, False, 4.8, 5.6, 3.8),
    (1, (3.3, -1.2, _LEG_Z), -0.95, 0.0, False, 5.8, 7.0, 4.6),
    (-1, (-3.3, -1.2, _LEG_Z), -0.95, 0.5, False, 5.8, 7.0, 4.6),
]

# Body parts: (centre, radius, non-uniform scale) in local units.
THORAX = ((0.0, 2.5, 6.2), 4.6, (0.95, 1.15, 0.85))
ABDOMEN = ((0.0, -6.5, 5.6), 5.0, (0.9, 1.5, 0.75))
HEAD = ((0.0, 9.0, 6.0), 3.0, (1.0, 0.85, 0.9))
EYE_R = ((2.1, 9.7, 6.4), 2.0, (0.8, 1.0, 1.15))
EYE_L = ((-2.1, 9.7, 6.4), 2.0, (0.8, 1.0, 1.15))
PROBOSCIS = ((0.0, 10.4, 4.6), 1.2, (0.5, 0.5, 1.0))
ANTENNA_OFFSET = 0.9

# Colours (linear RGB, matching upstream's calibrated NSColors).
BODY_BROWN = (0.50, 0.38, 0.22)
HEAD_BROWN = (0.575, 0.473, 0.337)  # bodyBrown blended 15% toward white
LEG_COLOR = (0.33, 0.24, 0.14)
TARSUS_COLOR = (0.2475, 0.18, 0.105)  # legColor blended 25% toward black
EYE_RED = (0.62, 0.10, 0.07)
ANTENNA_COLOR = (0.30, 0.22, 0.13)
PROBOSCIS_COLOR = (0.35, 0.26, 0.16)
ABDOMEN_BASE = (0.72, 0.55, 0.32)
ABDOMEN_STRIPE = (0.22, 0.15, 0.09)
WING_COLOR = (0.92, 0.92, 0.92)


@dataclass
class FlyModel:
    """Articulated state of one fly body."""

    legs: list[Leg] = field(default_factory=list)
    wings: list[Node] = field(default_factory=list)  # [0] = left, [1] = right
    blur_wings: list[Node] = field(default_factory=list)
    abdomen: Node = field(default_factory=Node)
    root: Node = field(default_factory=Node)


def build_fly_model() -> FlyModel:
    legs = []
    for side, attach, yaw_off, phase, is_front, femur, tibia, tarsus in LEG_SPECS:
        base_yaw = yaw_off if side > 0 else (math.pi - yaw_off)
        legs.append(Leg(attach, base_yaw, side, phase, is_front, femur, tibia, tarsus))

    wings = []
    for side in (-1.0, 1.0):
        w = Node()
        w.x = side * 1.6
        w.y = 0.5
        w.z = 7.7 if side > 0 else 7.55
        w.euler_z = side * 0.13
        wings.append(w)

    blur = []
    for side in (-1.0, 1.0):
        b = Node()
        b.x = side * 6.0
        b.y = 1.5
        b.z = 8.2
        b.scale_x, b.scale_y, b.scale_z = 5.5, 2.4, 0.3
        b.euler_z = side * -0.45
        b.hidden = True
        blur.append(b)

    abdomen = Node()
    abdomen.x, abdomen.y, abdomen.z = ABDOMEN[0]
    abdomen.scale_x, abdomen.scale_y, abdomen.scale_z = ABDOMEN[2]

    root = Node()
    root.scale_x = root.scale_y = root.scale_z = FLY_SCALE
    return FlyModel(legs=legs, wings=wings, blur_wings=blur, abdomen=abdomen, root=root)
