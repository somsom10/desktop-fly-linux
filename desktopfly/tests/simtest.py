"""Circuit invariants — port of ``runSimtest()`` from upstream's main.swift.

Same six phases, same pass criteria.  This is the oracle the port is validated
against: if these hold, the network the fly is running on behaves like the one
upstream ships.
"""

from __future__ import annotations

import sys

from ..simcore.data import load_brain_data
from ..simcore.lif import LIFSim


DEFAULT_SEED = 20260821


def _seed_from_argv(argv: list[str]) -> int | None:
    if "--no-seed" in argv:
        return None
    if "--seed" in argv:
        return int(argv[argv.index("--seed") + 1])
    return DEFAULT_SEED


def run(seed: int | None = DEFAULT_SEED) -> int:
    _points, circuit = load_brain_data()
    sim = LIFSim(circuit, seed=seed)
    print(
        f"circuit: {sim.n} neurons | loom L/R: {len(sim.loom_left)}/{len(sim.loom_right)}"
        f" | GF: {len(sim.gf)} | DNa L/R: {len(sim.dna_l)}/{len(sim.dna_r)}"
        f" | MDN: {len(sim.mdn)} | DNp09: {len(sim.fwd)} | DNg11: {len(sim.groom)}"
        f" | escW: {len(sim.escw)} | ascend: {len(sim.ascend)} | sens: {len(sim.sens)}"
    )

    # Phase 1: 4 s of spontaneous activity — the giant fiber must stay silent.
    gf_spont = 0
    for _ in range(40):
        sim.step(100)
        if sim.consume_gf():
            gf_spont += 1
    pop_hz = sim.total_spikes / 4.0 / sim.n
    print(
        f"spontaneous 4s: pop {pop_hz:.2f} Hz/neuron, LC {sim.rate_loom:.1f} Hz, "
        f"DNa02 L/R {sim.rate_dna_l:.1f}/{sim.rate_dna_r:.1f} Hz, "
        f"MDN {sim.rate_mdn:.1f} Hz, GF spikes: {gf_spont}"
    )

    # Phase 2: abrupt loom, as produced by a cursor lunge (step, not ramp).
    gf_latency_ms = -1
    gf_loom = 0
    for ms in range(400):
        sim.loom_l = 1.0
        sim.loom_r = 0.5
        sim.step(1)
        if sim.consume_gf():
            gf_loom += 1
            if gf_latency_ms < 0:
                gf_latency_ms = ms
    sim.loom_l = sim.loom_r = 0.0
    print(
        f"abrupt loom 0.4s: LC rate {sim.rate_loom:.1f} Hz, GF spikes {gf_loom}, "
        f"first at {gf_latency_ms} ms"
    )

    # Phase 3: 20 s with walking proprioception — do behaviour states emerge?
    walk_on = groom_on = samples = 0
    fwd_min, fwd_max = float("inf"), 0.0
    for ms in range(20_000):
        sim.gait_drive = 0.5
        sim.gait_phase = (ms % 125) / 125.0  # 8 Hz gait
        sim.step(1)
        if ms % 10 == 0:
            samples += 1
            if sim.rate_fwd / 10 > 0.22:
                walk_on += 1
            if sim.rate_groom / 8 > 0.5:
                groom_on += 1
            fwd_min = min(fwd_min, sim.rate_fwd)
            fwd_max = max(fwd_max, sim.rate_fwd)
    print(
        f"behavior 20s: walk-drive on {100 * walk_on / samples:.0f}%, "
        f"groom-drive on {100 * groom_on / samples:.0f}%, "
        f"DNp09 {fwd_min:.1f}-{fwd_max:.1f} Hz, pop {sim.rate_pop:.1f} Hz"
    )

    # Phase 3b: the midday siesta must slow the fly down, not paralyse it.
    sim.gait_drive = 0.0
    sim.activity_scale = 1 - (1 - 0.55) * 0.35  # = 0.84, the compressed siesta scale
    siesta_walk_on = siesta_samples = 0
    for ms in range(15_000):
        sim.step(1)
        if ms % 10 == 0:
            siesta_samples += 1
            if sim.rate_fwd / 10 > 0.22:
                siesta_walk_on += 1
    sim.activity_scale = 1.0
    siesta_pct = 100 * siesta_walk_on / siesta_samples
    print(f"siesta 15s (scale 0.84): walk-drive on {siesta_pct:.0f}%")

    # Phase 4: air puff (a fast cursor whoosh) — the wind startle pathway.
    gf_puff = 0
    for _ in range(1000):
        sim.air_puff = 1.0
        sim.step(1)
        if sim.consume_gf():
            gf_puff += 1
    sim.air_puff = 0.0
    print(f"air puff 1s: GF spikes {gf_puff}")

    # Phase 5: gentle left-eye-only loom — the steering response probe.
    for _ in range(500):
        sim.step(1)
        sim.consume_gf()
    diff0 = sim.rate_dna_l - sim.rate_dna_r
    for _ in range(1000):
        sim.loom_l = 0.30
        sim.loom_r = 0.0
        sim.step(1)
        sim.consume_gf()
    diff1 = sim.rate_dna_l - sim.rate_dna_r
    sim.loom_l = 0.0
    print(
        f"left-eye loom: DNa L-R rate diff {diff0:+.1f} -> {diff1:+.1f} Hz, "
        f"LC {sim.rate_loom:.1f} Hz"
    )

    # Phase 6: click-stimulation probes (what the brain window does).
    sim.stimulate(sim.gf, strength=0.5, duration_ms=40)
    sim.step(60)
    gf_stim = sim.consume_gf()
    sim.stimulate(sim.groom, strength=0.25, duration_ms=400)
    sim.step(400)
    groom_stim = sim.rate_groom
    sim.consume_gf()
    print(
        f"click probes: GF cluster -> spike {'yes' if gf_stim else 'NO'}, "
        f"DNg11 cluster -> groom rate {groom_stim:.0f} Hz"
    )

    ok = gf_spont == 0 and gf_loom > 0 and walk_on > 0 and gf_stim and siesta_pct > 3
    print(
        "PASS: GF silent at rest, fires on loom; locomotor drive fluctuates; "
        "stim works; siesta alive"
        if ok
        else "FAIL: tune weights/noise"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run(_seed_from_argv(sys.argv[1:])))
