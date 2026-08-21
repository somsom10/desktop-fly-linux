"""Population firing rates -> body commands.

Port of ``BrainSignals`` and ``SignalBuilder`` from upstream's ``main.swift``.
Shared by the live loop and the behaviour test so both exercise the identical
mapping, exactly as upstream intends.
"""

from __future__ import annotations

from dataclasses import dataclass


def clampf(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


@dataclass
class BrainSignals:
    """What the brain tells the body each frame."""

    escape: bool = False  # giant fiber spiked -> takeoff NOW
    nervous: float = 0.0  # looming-detector population rate, 0..1
    turn_bias: float = 0.0  # rad/s steering from DNa01/DNa02 left-right difference
    backward: bool = False  # MDN burst -> backward walking
    walk_drive: float = 0.0  # DNp09 forward-walking command rate, ~0..1.5
    groom_drive: float = 0.0  # DNg11 grooming command rate, ~0..1.5
    wing_drive: float = 0.0  # DNp02/04/11 escape-manoeuvre DN rate, ~0..1.3
    arousal: float = 0.0  # whole-population activity, ~0..1
    tempo: float = 1.0  # thermal scaling of locomotion
    sleep: bool = False  # circadian + idle -> sleep-like state


class SignalBuilder:
    def __init__(self) -> None:
        self._dna_baseline = 0.0

    def make(self, sim, dt: float) -> BrainSignals:
        diff = sim.rate_dna_l - sim.rate_dna_r
        # Slow adaptation (tau ~8 s): the connectome's persistent left/right
        # wiring asymmetry is adapted out, so steady walking is straight and
        # only transient DNa asymmetries (visual, stimulation) steer.
        self._dna_baseline += (diff - self._dna_baseline) * min(1.0, dt / 8.0)
        s = BrainSignals()
        s.escape = sim.consume_gf()
        s.nervous = clampf(sim.rate_loom / 80.0, 0.0, 1.0)
        s.turn_bias = clampf((diff - self._dna_baseline) * 0.04, -1.0, 1.0)
        s.backward = sim.rate_mdn > 8
        s.walk_drive = clampf(sim.rate_fwd / 10.0, 0.0, 1.3)
        s.groom_drive = sim.rate_groom / 8.0
        s.wing_drive = clampf(sim.rate_escw / 10.0, 0.0, 1.3)
        s.arousal = clampf(sim.rate_pop / 20.0, 0.0, 1.0)
        return s
