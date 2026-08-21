"""End-to-end sim -> body checks — port of ``runBehaviorTest()`` (main.swift).

Seven scenarios stimulate a real neuron population and assert the body reacts,
then ten body-level checks exercise terrain, sleep, thermal tempo, flight and
landing with hand-built signals.  Upstream calls these "the ground truth"; they
are what proves the ported behaviour layer still matches.
"""

from __future__ import annotations

import math
import random
import sys

from ..body.fly import Fly, Ledge, State
from ..world.sugar import SugarField, sense as sugar_sense
from ..body.model import FLY_SCALE
from ..platform.environment import circadian_activity
from ..simcore.data import load_brain_data
from ..simcore.lif import LIFSim
from ..simcore.signals import BrainSignals, SignalBuilder

BOUNDS = (1512.0, 982.0)
DT = 1.0 / 60.0

# Upstream runs these suites unseeded, which makes a couple of them genuinely
# flaky: the 0.5%/s spontaneous-takeoff roll turns a walking fly into a flying
# one about once in 300 runs of the thermal-tempo check. The behaviour is
# faithful to upstream and deliberately unchanged -- only the *test* is pinned,
# so a red result always means a real regression. Pass --no-seed to sample the
# stochastic space instead.
DEFAULT_SEED = 20260821


def _seed_from_argv(argv: list[str]) -> int | None:
    if "--no-seed" in argv:
        return None
    if "--seed" in argv:
        return int(argv[argv.index("--seed") + 1])
    return DEFAULT_SEED


def run(seed: int | None = DEFAULT_SEED) -> int:
    if seed is not None:
        random.seed(seed)
    _points, circuit = load_brain_data()
    failures = 0

    def scenario(name, stim, hold, check, describe, setup=None):
        nonlocal failures
        sim = LIFSim(circuit, seed=seed)
        builder = SignalBuilder()
        fly = Fly(0.0, 0.0)
        fly.state = State.IDLE
        fly.speed = 0.0
        if setup:
            setup(fly)
        # settle the network, drain any startup GF latch
        sim.step(400)
        sim.consume_gf()
        stim(sim)
        passed = False
        frames = int(hold / DT)
        while frames > 0:
            frames -= 1
            sim.step(int(round(DT * 1000)))
            s = builder.make(sim, DT)
            fly.update(DT, BOUNDS, None, s)
            if check(fly):
                passed = True
                break
        if not passed:
            failures += 1
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {describe(fly)}")

    scenario(
        "GF stim -> escape flight",
        lambda s: s.stimulate(s.gf, 0.5, 40), 0.5,
        lambda f: f.state is State.FLYING,
        lambda f: f"state={f.state}",
    )
    scenario(
        "DNg11 stim -> grooming",
        lambda s: s.stimulate(s.groom, 0.25, 600), 1.5,
        lambda f: f.state is State.GROOMING,
        lambda f: f"state={f.state}",
    )
    scenario(
        "DNp09 stim -> walks, speed rises (capped)",
        lambda s: s.stimulate(s.fwd, 0.25, 1200), 1.5,
        lambda f: f.state is State.WALKING and 40 < f.speed < 100,
        lambda f: f"state={f.state} speed={int(f.speed)}",
    )
    scenario(
        "MDN stim (from idle) -> backward walk",
        lambda s: s.stimulate(s.mdn, 0.3, 600), 1.2,
        lambda f: f.backward_timer > 0,
        lambda f: f"backwardTimer={f.backward_timer:.2f}",
    )

    def setup_walk(f):
        f.state = State.WALKING
        f.speed = 30.0
        f.heading = 0.0

    scenario(
        "DNa-left stim -> left (CCW) turn while walking",
        lambda s: s.stimulate(s.dna_l, 0.3, 900), 1.4,
        lambda f: f.heading > 0.25,
        lambda f: f"heading change {f.heading:+.2f} rad",
        setup=setup_walk,
    )

    def moderate_loom(s):
        s.loom_l = 0.45
        s.loom_r = 0.45

    scenario(
        "moderate loom -> fear response (dart or escape)",
        moderate_loom, 1.0,
        lambda f: (f.state is State.WALKING and f.speed > 100) or f.state is State.FLYING,
        lambda f: f"state={f.state} speed={int(f.speed)}",
    )
    scenario(
        "tap near fly -> startle escape via sensory pathway",
        lambda s: s.stimulate(s.sens, 0.45, 150), 0.8,
        lambda f: f.state is State.FLYING,
        lambda f: f"state={f.state}",
    )

    # ---- body-level environment checks (hand-built signals, no sim) ----
    def body_check(name, fn):
        nonlocal failures
        ok, detail = fn()
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    walk_signals = BrainSignals()
    walk_signals.walk_drive = 0.6

    def check_ledge():
        fly = Fly(0.0, -55.0)
        fly.state = State.WALKING
        fly.speed = 30.0
        fly.heading = 0.0
        fly.terrain = [Ledge(y=-40, x0=-300, x1=300, id=1)]
        for _ in range(240):
            fly.update(DT, BOUNDS, None, walk_signals)
            if fly.ledge is not None and abs(fly.pos_y + 40) < 8:
                return True, f"attached, y={int(fly.pos_y)}"
        return False, f"state={fly.state} y={int(fly.pos_y)} ledge={fly.ledge is not None}"

    body_check("ledge attach + follow window edge", check_ledge)

    def check_vanish():
        fly = Fly(0.0, -40.0)
        fly.state = State.WALKING
        fly.speed = 25.0
        fly.heading = 0.0
        fly.terrain = [Ledge(y=-40, x0=-300, x1=300, id=1)]
        fly.ledge = fly.terrain[0]
        fly.terrain = []
        for _ in range(60):
            fly.update(DT, BOUNDS, None, walk_signals)
            if fly.state is State.FLYING:
                return True, "took off"
        return False, f"state={fly.state}"

    body_check("window closes underfoot -> takeoff", check_vanish)

    def check_sleep():
        fly = Fly(0.0, 0.0)
        fly.state = State.IDLE
        s = BrainSignals()
        s.sleep = True
        for _ in range(60):
            fly.update(DT, BOUNDS, None, s)
        if fly.state is not State.SLEEPING:
            return False, f"no sleep: {fly.state}"
        s.sleep = False
        fly.update(DT, BOUNDS, None, s)
        return fly.state is State.GROOMING, f"woke to {fly.state}"

    body_check("sleep signal -> sleeping; wake -> grooming", check_sleep)

    def check_tempo():
        fly = Fly(0.0, 0.0)
        fly.state = State.WALKING
        fly.speed = 20.0
        fly.heading = 0.0
        cool = BrainSignals(walk_drive=0.6, tempo=1.0)
        for _ in range(120):
            fly.update(DT, BOUNDS, None, cool)
        cool_speed = fly.speed
        hot = BrainSignals(walk_drive=0.6, tempo=1.5)
        for _ in range(120):
            fly.update(DT, BOUNDS, None, hot)
        hot_speed = fly.speed
        return (
            fly.state is State.WALKING and hot_speed > cool_speed + 10,
            f"cool {int(cool_speed)} -> hot {int(hot_speed)} pt/s",
        )

    body_check("thermal tempo scales walking speed", check_tempo)

    def check_flight_altitude():
        def flight(escape, effort):
            fly = Fly(0.0, 0.0)
            fly.state = State.IDLE
            fly.start_flight(BOUNDS, escape=escape, effort=effort)
            max_alt = max_scale = 0.0
            frames = 0
            while fly.state is State.FLYING and frames < 400:
                frames += 1
                fly.update(DT, BOUNDS, None, BrainSignals())
                max_alt = max(max_alt, fly.alt)
                max_scale = max(max_scale, fly.scale)
            return max_alt, max_scale

        esc_alt, esc_scale = flight(True, None)
        cas_alt, cas_scale = flight(False, 0.45)
        ok = (
            esc_alt > cas_alt + 0.15
            and esc_scale > FLY_SCALE * 1.5
            and abs(esc_scale - FLY_SCALE * (1 + 0.8 * esc_alt)) < 0.15
        )
        return ok, (
            f"escape alt {esc_alt:.2f} scale {esc_scale:.2f} | "
            f"casual alt {cas_alt:.2f} scale {cas_scale:.2f}"
        )

    body_check("flight: altitude drives scale; escape flies higher than casual",
               check_flight_altitude)

    def check_wingbeat():
        fly = Fly(0.0, 0.0)
        fly.state = State.IDLE
        fly.start_flight(BOUNDS, effort=0.8)
        lo, hi = math.inf, -math.inf
        for _ in range(30):
            if fly.state is not State.FLYING:
                break
            fly.update(DT, BOUNDS, None, BrainSignals())
            z = fly.model.wings[0].euler_z
            lo, hi = min(lo, z), max(hi, z)
        return hi - lo > 0.25, f"wing sweep {hi - lo:.2f} rad over 0.5 s"

    body_check("flight: wings actually beat", check_wingbeat)

    def check_effort():
        fly = Fly(0.0, 0.0)
        fly.state = State.IDLE
        fly.start_flight(BOUNDS, effort=0.5)
        calm = BrainSignals()
        for _ in range(12):
            fly.update(DT, BOUNDS, None, calm)
        calm_effort = fly.effort_current
        hot = BrainSignals(wing_drive=1.0, arousal=0.6)
        for _ in range(12):
            if fly.state is not State.FLYING:
                break
            fly.update(DT, BOUNDS, None, hot)
        hot_effort = fly.effort_current
        return (
            fly.state is State.FLYING and hot_effort > calm_effort + 0.2,
            f"effort {calm_effort:.2f} -> {hot_effort:.2f}",
        )

    body_check("escape-DN activity mid-flight raises wing-beat effort", check_effort)

    def check_threat_posture():
        fly = Fly(0.0, 0.0)
        fly.state = State.WALKING
        fly.speed = 20.0
        fly.dart_cooldown = 99.0  # isolate the posture from darting
        threat = BrainSignals(wing_drive=0.9, walk_drive=0.4)
        for _ in range(40):
            fly.update(DT, BOUNDS, None, threat)
        x = fly.model.wings[0].euler_x
        return (
            fly.state is not State.FLYING and fly.wing_raise > 0.6 and x < -0.2,
            f"raise {fly.wing_raise:.2f}, wing tilt {x:.2f} rad",
        )

    body_check("threat while grounded raises the wings (no takeoff)", check_threat_posture)

    def check_landing():
        fly = Fly(0.0, 0.0)
        fly.state = State.IDLE
        fly.start_flight(BOUNDS, escape=True)
        prev_scale, prev_z = fly.scale, fly.z
        max_ds = max_dz = 0.0
        post, frames, landed = 20, 0, False
        while post > 0 and frames < 600:
            frames += 1
            fly.update(DT, BOUNDS, None, BrainSignals())
            max_ds = max(max_ds, abs(fly.scale - prev_scale))
            max_dz = max(max_dz, abs(fly.z - prev_z))
            prev_scale, prev_z = fly.scale, fly.z
            if fly.state is not State.FLYING:
                landed = True
                post -= 1
        return (
            landed and max_ds < 0.2 and max_dz < 25,
            f"landed={'yes' if landed else 'NO'}, max per-frame dscale {max_ds:.2f}, dz {max_dz:.1f}",
        )

    body_check("landing is smooth: no scale/height snap at touchdown", check_landing)

    def check_circadian():
        night, dawn = circadian_activity(3), circadian_activity(9)
        siesta, dusk = circadian_activity(14), circadian_activity(18)
        ok = night < 0.4 and dawn > 0.9 and 0.3 < siesta < 0.7 and dusk > 0.9
        return ok, f"3h {night:.2f}, 9h {dawn:.2f}, 14h {siesta:.2f}, 18h {dusk:.2f}"

    body_check("circadian curve: siesta + night dips, dawn/dusk peaks", check_circadian)

    # ---- sugar (an addition, not upstream) ----
    def check_feeding():
        fly = Fly(0.0, 0.0)
        fly.state = State.IDLE
        s = BrainSignals(sugar_contact=True)
        fed = False
        for _ in range(400):
            fly.update(DT, BOUNDS, None, s)
            fed = fed or fly.state is State.FEEDING
            if fly.satiety > 0.9:
                break
        return (fed and fly.satiety > 0.9 and fly.proboscis_ext > 0.8,
                f"fed={fed} satiety={fly.satiety:.2f} proboscis={fly.proboscis_ext:.2f}")

    body_check("sugar contact -> feeding, proboscis out, satiety rises", check_feeding)

    def check_sated():
        """A full fly should walk past a drop rather than stop at it."""
        fly = Fly(0.0, 0.0)
        fly.state = State.WALKING
        fly.speed = 30.0
        fly.satiety = 1.0
        s = BrainSignals(sugar_contact=True, walk_drive=0.6, satiety=1.0)
        for _ in range(120):
            fly.update(DT, BOUNDS, None, s)
            if fly.state is State.FEEDING:
                return False, "a sated fly stopped to feed"
        return True, f"ignored the drop (satiety {fly.satiety:.2f})"

    body_check("sated fly ignores sugar", check_sated)

    def check_escape_beats_food():
        """Escape must outrank a meal: the GF spike wins."""
        fly = Fly(0.0, 0.0)
        fly.state = State.IDLE
        feeding = BrainSignals(sugar_contact=True)
        for _ in range(90):
            fly.update(DT, BOUNDS, None, feeding)
        if fly.state is not State.FEEDING:
            return False, f"never started feeding ({fly.state})"
        alarm = BrainSignals(sugar_contact=True, escape=True)
        fly.update(DT, BOUNDS, None, alarm)
        return fly.state is State.FLYING, f"GF spike while feeding -> {fly.state}"

    body_check("escape outranks feeding", check_escape_beats_food)

    def check_odour_steering():
        """The odour bearing must actually move the real DNa rates.

        This is the check that keeps the feature honest: approach is produced by
        injecting current onto DNa01/DNa02 and DNp09 and letting the network
        respond, so if the network stops responding the feature is a lie.
        """
        sim = LIFSim(circuit, seed=seed)
        builder = SignalBuilder()
        sim.step(600)
        sim.consume_gf()
        field = SugarField()
        field.add(0.0, 600.0)  # straight to the fly's left when heading = 0
        smelled = sugar_sense(field, 0.0, 0.0, 0.0, 0.0)
        sim.sugar_l = smelled.steer_left
        sim.sugar_r = smelled.steer_right
        sim.sugar_appetite = smelled.appetite
        bias = 0.0
        for _ in range(120):
            sim.step(16)
            bias = builder.make(sim, DT).turn_bias
        return (smelled.steer_left > smelled.steer_right and bias > 0.05,
                f"drive L/R {smelled.steer_left:.2f}/{smelled.steer_right:.2f} "
                f"-> turn_bias {bias:+.2f} (CCW = toward the smell)")

    body_check("odour bearing -> real DNa steering response", check_odour_steering)

    print("ALL BEHAVIOR TESTS PASS" if failures == 0 else f"{failures} FAILURES")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run(_seed_from_argv(sys.argv[1:])))
