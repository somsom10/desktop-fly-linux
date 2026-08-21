"""Per-fly behaviour: states, gait, flight, ledges, sleep.

Port of the ``Fly`` class from upstream's ``FlyModel.swift``.  Every behavioural
decision in :meth:`Fly.brain_behavior` reads a real neuron population's firing
rate — nothing here is scripted animation triggered by distance.

The SceneKit node tree is replaced by the plain state in ``body/model.py``; the
renderer reads exactly the fields the SceneKit version would have read.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum

from ..simcore.signals import BrainSignals, clampf
from .model import FLY_SCALE, FlyModel, build_fly_model

EDGE_MARGIN = 50.0
# Sugar/feeding (an addition, not upstream). A drop takes a few seconds to eat,
# and the fly stays uninterested for a few minutes afterwards.
FEED_SATIETY_RATE = 0.32  # satiety gained per second of feeding
SATIETY_DECAY = 1.0 / 240.0  # hunger returns over ~4 minutes
FEED_HUNGER_THRESHOLD = 0.75  # above this satiety, a drop is not worth stopping for
SCARE_RADIUS = 110.0  # legacy behaviour (non-connectome flies) only
NERVOUS_RADIUS = 240.0  # legacy behaviour only


class State(Enum):
    WALKING = "walking"
    IDLE = "idle"
    GROOMING = "grooming"
    FLYING = "flying"
    SLEEPING = "sleeping"
    FEEDING = "feeding"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Ledge:
    """A walkable window top edge, in scene coordinates (origin at screen centre)."""

    y: float
    x0: float
    x1: float
    id: int


def rnd(lo: float, hi: float) -> float:
    return random.uniform(lo, hi)


def angle_diff(frm: float, to: float) -> float:
    d = math.fmod(to - frm, 2 * math.pi)
    if d > math.pi:
        d -= 2 * math.pi
    if d < -math.pi:
        d += 2 * math.pi
    return d


def smoothstep(t: float) -> float:
    x = clampf(t, 0.0, 1.0)
    return x * x * (3 - 2 * x)


class Fly:
    def __init__(self, x: float = 0.0, y: float = 0.0):
        self.model: FlyModel = build_fly_model()
        self.pos_x = x
        self.pos_y = y
        self.heading = rnd(0, 2 * math.pi)
        self.speed = 30.0
        self.state = State.WALKING
        self.state_timer = rnd(1.5, 4)
        self.gait_phase = rnd(0, 1)
        self.time = rnd(0, 100)
        self.scare_cooldown = 0.0
        self.dart_cooldown = 0.0
        self.backward_timer = 0.0
        self.dart_timer = 0.0
        self.state_age = 0.0
        self.terrain: list[Ledge] = []
        self.ledge: Ledge | None = None

        self.flight_from = (0.0, 0.0)
        self.flight_to = (0.0, 0.0)
        self.flight_t = 0.0
        self.flight_dur = 1.0
        self.flight_effort = 0.6
        self.effort_current = 0.6
        self.alt = 0.0
        self.pitch = 0.0
        self.flap_phase = 0.0
        self.wing_raise = 0.0
        self.satiety = 0.0  # 0 hungry .. 1 full
        self.proboscis_ext = 0.0  # 0 retracted .. 1 extended into the drop

        # node-level state the renderer and the tests read
        self.scale = FLY_SCALE
        self.z = 0.0

        self._brain_live = False
        self._live_arousal = 0.0
        self._live_wing = 0.0
        self.sync_node()

    # ------------------------------------------------------------ helpers

    @property
    def walking_intensity(self) -> float:
        if self.state is not State.WALKING:
            return 0.0
        v = 22.0 if self.backward_timer > 0 else self.speed
        return clampf(abs(v) / 60.0, 0.0, 1.0)

    @property
    def _effective_speed(self) -> float:
        return -22.0 if self.backward_timer > 0 else self.speed

    def sync_node(self) -> None:
        r = self.model.root
        r.x, r.y, r.z = self.pos_x, self.pos_y, self.z
        r.euler_x, r.euler_y, r.euler_z = self.pitch, 0.0, self.heading - math.pi / 2
        r.scale_x = r.scale_y = r.scale_z = self.scale

    def _set_state(self, s: State) -> None:
        if s is self.state:
            return
        self.state = s
        self.state_age = 0.0

    # ------------------------------------------------------------- flight

    def start_flight(self, bounds, away_from=None, escape: bool = False, effort=None) -> None:
        self.state = State.FLYING
        self.ledge = None
        base = effort if effort is not None else (1.0 if escape else rnd(0.4, 0.75))
        self.flight_effort = clampf(base, 0.25, 1.0)
        self.effort_current = self.flight_effort
        self.flap_phase = 0.0
        self.wing_raise = 0.0
        self.flight_from = (self.pos_x, self.pos_y)
        hw = bounds[0] / 2 - EDGE_MARGIN
        hh = bounds[1] / 2 - EDGE_MARGIN
        target = (0.0, 0.0)
        chosen = False
        # casual flights often land on a window edge
        if not escape and away_from is None and self.terrain and rnd(0, 1) < 0.45:
            L = random.choice(self.terrain)
            if L.x1 - L.x0 > 90:
                target = (rnd(L.x0 + 25, L.x1 - 25), L.y)
                chosen = math.hypot(target[0] - self.pos_x, target[1] - self.pos_y) > 180
        if not chosen:
            for _ in range(16):
                target = (rnd(-hw, hw), rnd(-hh, hh))
                far = math.hypot(target[0] - self.pos_x, target[1] - self.pos_y) > (
                    350 if escape else 260
                )
                if not far:
                    continue
                if away_from is not None:
                    to_t = (target[0] - self.pos_x, target[1] - self.pos_y)
                    to_a = (away_from[0] - self.pos_x, away_from[1] - self.pos_y)
                    if to_t[0] * to_a[0] + to_t[1] * to_a[1] > 0:
                        continue
                break
        self.flight_to = target
        dist = math.hypot(target[0] - self.pos_x, target[1] - self.pos_y)
        self.flight_dur = (
            clampf(dist / 650, 0.45, 1.2) if escape else clampf(dist / 420, 0.7, 2.0)
        )
        self.flight_t = 0.0
        self.scare_cooldown = 2.0 if escape else 2.5
        for b in self.model.blur_wings:
            b.hidden = False

    def _land(self) -> None:
        self.state = State.IDLE
        self.state_timer = rnd(0.3, 0.8)
        self.speed = 0.0
        self.alt = 0.0
        self.pitch = 0.0
        self.scale = FLY_SCALE
        self.z = 0.0
        for i, wing in enumerate(self.model.wings):
            side = -1.0 if i == 0 else 1.0
            wing.euler_x, wing.euler_y, wing.euler_z = 0.0, 0.0, side * 0.13
        for b in self.model.blur_wings:
            b.hidden = True

    def _apply_altitude(self) -> None:
        self.scale = FLY_SCALE * (1 + 0.8 * self.alt)
        self.z = 90 * self.alt

    def _update_flight(self, dt: float) -> None:
        self.flight_t = min(1.0, self.flight_t + dt / self.flight_dur)
        if self.flight_t >= 1.0:
            # Touchdown flare: the timer ended, but the fly lands only once it
            # has actually descended — hover over the target and settle.
            self.pos_x = self.flight_to[0] + math.sin(self.time * 26) * 1.2
            self.pos_y = self.flight_to[1] + math.cos(self.time * 22) * 1.0
            self.pitch = clampf(self.alt * 0.4, 0.0, 0.35)
            self.alt += (0 - self.alt) * min(1.0, 9 * dt)
            self._apply_altitude()
            if self.alt < 0.035:
                self.pos_x, self.pos_y = self.flight_to
                self._land()
            return
        e = smoothstep(self.flight_t)
        dx = self.flight_to[0] - self.flight_from[0]
        dy = self.flight_to[1] - self.flight_from[1]
        ln = max(1.0, math.hypot(dx, dy))
        px, py = -dy / ln, dx / ln
        wob = math.sin(self.time * 32) * 4 * math.sin(self.flight_t * math.pi)
        self.pos_x = self.flight_from[0] + dx * e + px * wob
        self.pos_y = self.flight_from[1] + dy * e + py * wob
        self.heading = math.atan2(dy, dx) + math.sin(self.time * 18) * 0.12
        # Effort stays live: ongoing escape-DN (DNp02/04/11) and arousal push
        # the fly to beat harder and fly higher mid-flight. The max() keeps a
        # live modifier from ever *weakening* an escape takeoff.
        if self._brain_live:
            self.effort_current = clampf(
                max(
                    self.flight_effort,
                    self.flight_effort * 0.55 + self._live_arousal * 0.25 + self._live_wing * 0.6,
                ),
                0.25,
                1.3,
            )
        else:
            self.effort_current = self.flight_effort
        rise_env = min(self.flight_t / 0.25, 1.0)
        fall_env = min((1 - self.flight_t) / 0.3, 1.0)
        target = self.effort_current * min(rise_env, fall_env) * (
            0.85 + 0.15 * math.sin(self.time * 7)
        )
        self.pitch = clampf((target - self.alt) * 2.5, -0.45, 0.45)
        self.alt += (target - self.alt) * min(1.0, 6 * dt)
        self._apply_altitude()

    # ---------------------------------------------------------- behaviour

    def _pick_next_state(self) -> None:
        if self.state is State.WALKING:
            r = rnd(0, 1)
            if r < 0.30:
                self.state = State.IDLE
                self.state_timer = rnd(0.8, 3)
                self.speed = 0.0
            elif r < 0.55:
                self.state_timer = rnd(0.3, 0.8)
                self.speed = rnd(95, 150)
                self.heading += rnd(-1.2, 1.2)
            else:
                self.state_timer = rnd(1.5, 5)
                self.speed = rnd(18, 45)
        elif self.state is State.IDLE:
            if rnd(0, 1) < 0.35:
                self.state = State.GROOMING
                self.state_timer = rnd(1.0, 2.5)
            else:
                self.state = State.WALKING
                self.state_timer = rnd(1.5, 5)
                self.speed = rnd(18, 45)
                self.heading += rnd(-1.5, 1.5)
        elif self.state is State.GROOMING:
            self.state = State.IDLE
            self.state_timer = rnd(0.3, 1.0)

    def brain_behavior(self, s: BrainSignals, dt: float, bounds, mouse) -> None:
        # Giant fiber spike -> escape takeoff (startles it even out of sleep)
        if s.escape and self.scare_cooldown == 0:
            self.start_flight(bounds, away_from=mouse, escape=True)
            return
        # circadian sleep: enter, hold, wake to grooming
        if s.sleep:
            if self.state is not State.SLEEPING:
                self._set_state(State.SLEEPING)
                self.speed = 0.0
                self.dart_timer = 0.0
                self.backward_timer = 0.0
            return
        elif self.state is State.SLEEPING:
            self._set_state(State.GROOMING)  # flies groom after waking
            return
        # Looming detectors hot but GF quiet -> nervous dart away
        if s.nervous > 0.40 and self.dart_cooldown == 0:
            self.ledge = None
            self._set_state(State.WALKING)
            if mouse is not None:
                self.heading = math.atan2(self.pos_y - mouse[1], self.pos_x - mouse[0]) + rnd(-0.4, 0.4)
            else:
                self.heading += rnd(-1.5, 1.5)
            self.speed = rnd(110, 155)
            self.dart_timer = rnd(0.4, 0.9)
            self.dart_cooldown = 1.2
        # Sugar: contact chemoreception -> feeding. Modeled outright — the
        # circuit has no gustatory pathway to derive it from (verified: its 16
        # sensory partners are all mechanosensory, and FlyWire's 334 gustatory
        # neurons make zero synapses onto it). Approach, by contrast, is real:
        # the odour bearing is injected onto DNa01/DNa02 and DNp09 in the sim.
        if self.state is State.FEEDING:
            self.satiety = min(1.0, self.satiety + FEED_SATIETY_RATE * dt)
            self.speed = 0.0
            done = (not s.sugar_contact) or self.satiety >= 0.98
            if (done or s.nervous > 0.35) and self.state_age > 0.4:
                self._set_state(State.GROOMING)  # flies groom after a meal
            return
        if (
            s.sugar_contact
            and self.satiety < FEED_HUNGER_THRESHOLD
            and self.state_age > 0.4
            and self.state in (State.WALKING, State.IDLE, State.GROOMING)
        ):
            self._set_state(State.FEEDING)
            self.speed = 0.0
            self.ledge = None
            return

        # DNg11 (grooming command) hysteresis
        if self.state is not State.WALKING or self.dart_timer == 0:
            if (
                self.state is not State.GROOMING
                and s.groom_drive > 0.5
                and s.nervous < 0.3
                and self.state_age > 0.4
            ):
                self._set_state(State.GROOMING)
            elif self.state is State.GROOMING and s.groom_drive < 0.3 and self.state_age > 0.6:
                self._set_state(State.IDLE)
        # DNp09 (forward-walking command) hysteresis
        if self.state is State.IDLE and s.walk_drive > 0.22 and self.state_age > 0.4:
            self._set_state(State.WALKING)
            self.heading += rnd(-0.8, 0.8)
        elif (
            self.state is State.WALKING
            and self.dart_timer == 0
            and s.walk_drive < 0.08
            and self.state_age > 0.5
        ):
            self._set_state(State.IDLE)
            self.speed = 0.0
        # MDN burst -> backward walk, from any grounded state
        if s.backward and self.backward_timer == 0 and self.dart_timer == 0:
            if self.state is not State.WALKING:
                self._set_state(State.WALKING)
                self.speed = 0.0
            self.backward_timer = 0.5
        # walking speed follows the forward command rate; tempo = temperature
        if self.state is State.WALKING:
            if self.dart_timer == 0 and self.backward_timer == 0:
                target = (14 + s.walk_drive * 55) * s.tempo
                self.speed += (target - self.speed) * min(1.0, 3 * dt)
            if self.ledge is None:
                self.heading += s.turn_bias * dt  # DNa01/DNa02 steering
        # spontaneous takeoff, gated on whole-population arousal
        flight_chance = 0.6 if s.arousal > 0.5 else 0.005
        if self.state is State.WALKING and rnd(0, 1) < flight_chance * dt:
            self.start_flight(bounds, effort=0.35 + s.arousal * 0.6)

    # --------------------------------------------------------------- walk

    def _update_walk(self, dt: float, bounds) -> None:
        # refresh the attached ledge from current terrain (windows move/close)
        if self.ledge is not None:
            cur = next((t for t in self.terrain if t.id == self.ledge.id), None)
            if cur is not None and abs(cur.y - self.ledge.y) < 40:
                self.ledge = cur
            else:
                self.ledge = None
                self.start_flight(bounds)  # the ground vanished from under it
                return
        if self.ledge is not None:
            L = self.ledge
            self.heading += rnd(-1, 1) * 0.2 * dt
            along = 0.0 if math.cos(self.heading) >= 0 else math.pi
            self.heading += angle_diff(self.heading, along) * min(1.0, 6 * dt)
            self.pos_x += math.cos(self.heading) * self._effective_speed * dt
            self.pos_y += (L.y - self.pos_y) * min(1.0, 10 * dt)
            if self.pos_x <= L.x0 + 6 and math.cos(self.heading) < 0:
                self.heading = 0.0
            if self.pos_x >= L.x1 - 6 and math.cos(self.heading) > 0:
                self.heading = math.pi
            self.pos_x = clampf(self.pos_x, L.x0, L.x1)
            if rnd(0, 1) < 0.05 * dt:
                self.ledge = None  # wander off the edge
        else:
            self.heading += rnd(-1, 1) * 1.6 * dt
            hw = bounds[0] / 2 - EDGE_MARGIN
            hh = bounds[1] / 2 - EDGE_MARGIN
            if abs(self.pos_x) > hw or abs(self.pos_y) > hh:
                to_center = math.atan2(-self.pos_y, -self.pos_x)
                self.heading += angle_diff(self.heading, to_center) * min(1.0, 4 * dt)
            v = self._effective_speed
            self.pos_x += math.cos(self.heading) * v * dt
            self.pos_y += math.sin(self.heading) * v * dt
            self.pos_x = clampf(self.pos_x, -bounds[0] / 2 + 20, bounds[0] / 2 - 20)
            self.pos_y = clampf(self.pos_y, -bounds[1] / 2 + 20, bounds[1] / 2 - 20)
            # walked onto a window edge? latch on
            for L in self.terrain:
                if L.x0 - 8 < self.pos_x < L.x1 + 8 and abs(self.pos_y - L.y) < 20:
                    if rnd(0, 1) < 0.9 * dt:
                        self.ledge = L
                        self.heading = 0.0 if math.cos(self.heading) >= 0 else math.pi
                        break
        self.z = 0.35 * abs(math.sin(self.gait_phase * math.pi * 2))

    # ------------------------------------------------------------ tick

    def update(self, dt: float, bounds, mouse=None, signals: BrainSignals | None = None) -> None:
        self.time += dt
        self.scare_cooldown = max(0.0, self.scare_cooldown - dt)
        self.dart_cooldown = max(0.0, self.dart_cooldown - dt)
        self.backward_timer = max(0.0, self.backward_timer - dt)
        self.state_age += dt
        self.dart_timer = max(0.0, self.dart_timer - dt)

        # live brain drives reach the wings even mid-flight
        self._brain_live = signals is not None
        self._live_arousal = signals.arousal if signals else 0.0
        self._live_wing = signals.wing_drive if signals else 0.0

        if self.state is State.FLYING:
            self._update_flight(dt)
        elif signals is not None:
            self.brain_behavior(signals, dt, bounds, mouse)
            if self.state is State.WALKING:
                self._update_walk(dt, bounds)
        else:
            # legacy distance-based fear, for the extra brainless flies
            if self.scare_cooldown == 0 and mouse is not None:
                d = math.hypot(mouse[0] - self.pos_x, mouse[1] - self.pos_y)
                if d < SCARE_RADIUS:
                    self.start_flight(bounds, away_from=mouse)
                elif d < NERVOUS_RADIUS and self.state is not State.WALKING:
                    self._set_state(State.WALKING)
                    self.heading = math.atan2(self.pos_y - mouse[1], self.pos_x - mouse[0]) + rnd(-0.4, 0.4)
                    self.speed = rnd(110, 150)
                    self.state_timer = rnd(0.4, 0.9)
                    self.scare_cooldown = 1.0
            if self.state is not State.FLYING:
                self.state_timer -= dt
                if self.state_timer <= 0:
                    if self.state is State.WALKING and rnd(0, 1) < 0.10:
                        self.start_flight(bounds)
                    else:
                        self._pick_next_state()
                if self.state is State.WALKING:
                    self._update_walk(dt, bounds)

        if self.state is not State.FEEDING:
            self.satiety = max(0.0, self.satiety - SATIETY_DECAY * dt)
        # the proboscis extends into the drop while feeding, retracts otherwise
        target = 1.0 if self.state is State.FEEDING else 0.0
        self.proboscis_ext += (target - self.proboscis_ext) * min(1.0, 6 * dt)

        self._update_legs(dt)
        self._update_wings(dt)
        # slower, deeper breathing while asleep
        breathe = (
            1 + 0.05 * math.sin(self.time * 1.1)
            if self.state is State.SLEEPING
            else 1 + 0.03 * math.sin(self.time * 3.0)
        )
        self.model.abdomen.scale_z = 0.75 * breathe
        self.sync_node()

    # --------------------------------------------------------- animation

    def _update_legs(self, dt: float) -> None:
        v = abs(self._effective_speed)
        if self.state is State.WALKING and v > 1:
            amp = clampf(0.20 + v * 0.0022, 0.20, 0.50)
            stride = max(5.0, 2 * amp * 13)
            freq = clampf(v / stride, 3, 11)
            self.gait_phase = math.fmod(self.gait_phase + freq * dt, 1.0)
            stance_frac = 0.6
            for leg in self.model.legs:
                p = math.fmod(self.gait_phase + leg.phase, 1.0)
                if p < stance_frac:
                    leg.angle = amp * (1 - 2 * (p / stance_frac))
                    leg.lift = 0.0
                else:
                    s = (p - stance_frac) / (1 - stance_frac)
                    leg.angle = -amp + 2 * amp * smoothstep(s)
                    leg.lift = math.sin(s * math.pi) * 0.55
                if self.backward_timer > 0:
                    leg.angle = -leg.angle
        elif self.state is State.GROOMING:
            for leg in self.model.legs:
                if leg.is_front:
                    leg.angle = 0.45 + 0.25 * math.sin(self.time * 20 + leg.swing_sign * 1.3)
                    leg.lift = 0.55 + 0.15 * math.sin(self.time * 22)
                else:
                    leg.angle += (0 - leg.angle) * min(1.0, 8 * dt)
                    leg.lift += (0 - leg.lift) * min(1.0, 8 * dt)
        elif self.state is State.FEEDING:
            for leg in self.model.legs:
                if leg.is_front:  # tarsi dabbling at the drop
                    leg.angle += (0.30 + 0.08 * math.sin(self.time * 7) - leg.angle) * min(1.0, 6 * dt)
                    leg.lift += (0.18 - leg.lift) * min(1.0, 6 * dt)
                else:
                    leg.angle += (0 - leg.angle) * min(1.0, 8 * dt)
                    leg.lift += (0 - leg.lift) * min(1.0, 8 * dt)
        elif self.state is State.FLYING:
            for leg in self.model.legs:
                leg.angle += (-0.35 - leg.angle) * min(1.0, 6 * dt)
                leg.lift += (0.5 - leg.lift) * min(1.0, 6 * dt)
        else:
            for leg in self.model.legs:
                leg.angle += (0 - leg.angle) * min(1.0, 10 * dt)
                leg.lift += (0 - leg.lift) * min(1.0, 10 * dt)

    def _update_wings(self, dt: float) -> None:
        if self.state is not State.FLYING:
            # grounded threat posture: escape-DN / loom activity raises the wings
            raise_target = (
                1.0
                if (
                    self.state is not State.SLEEPING
                    and (self._live_wing > 0.7 or (self._brain_live and self.dart_timer > 0))
                )
                else 0.0
            )
            self.wing_raise += (raise_target - self.wing_raise) * min(1.0, 8 * dt)
            if self.wing_raise > 0.01:
                for i, wing in enumerate(self.model.wings):
                    side = -1.0 if i == 0 else 1.0
                    wing.euler_x = -0.5 * self.wing_raise
                    wing.euler_y = 0.0
                    wing.euler_z = side * (0.13 + 0.3 * self.wing_raise)
            return
        # visible wing-beat: the wing shapes sweep through a stroke arc
        self.flap_phase = math.fmod(
            self.flap_phase + dt * (14 + 10 * self.effort_current), 1.0
        )
        stroke = math.sin(self.flap_phase * 2 * math.pi)
        for i, wing in enumerate(self.model.wings):
            side = -1.0 if i == 0 else 1.0
            wing.euler_x = stroke * 0.35
            wing.euler_y = 0.0
            wing.euler_z = side * (0.45 + 0.35 * (0.5 + 0.5 * stroke))
        flick = 0.10 + 0.14 * abs(stroke)
        self.model.blur_wings[0].opacity = flick
        self.model.blur_wings[1].opacity = flick
        self.model.blur_wings[0].euler_z = 0.45 + stroke * 0.2
        self.model.blur_wings[1].euler_z = -0.45 - stroke * 0.2
