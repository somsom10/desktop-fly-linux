# Test parity with upstream

Upstream's `CLAUDE.md` calls its two suites "the ground truth" and requires both
to pass after any change to sim or behaviour code. Both are ported check for
check, and both pass.

## `--simtest` — circuit invariants

Six phases, ported from `runSimtest()`. Upstream's stated invariants and this
port's measured values:

| invariant (upstream `CLAUDE.md`) | required | measured here |
|---|---|---|
| GF silent over 4 s of rest | 0 spikes | **0** |
| GF fires after abrupt loom | ≤ ~10 ms | **4 ms** |
| walk-drive duty | 20–50 % | **36 %** |
| siesta (scale 0.84) walk-drive | > 3 % | **30 %** |
| GF cluster stimulation → spike | yes | **yes** |

The 4 ms latency is the number that matters most: it is the race between the
×6-boosted LC→GF electrical drive and ~1,200 synapses of feedforward inhibition
delayed by 4 ms. Reproducing it means the delay queue, the weight signs, the
gap-junction boost and the operating point all survived the translation.

Full current output is reproduced at the bottom of this file.

## `--behaviortest` — 17 end-to-end checks

Seven scenarios stimulate a real neuron population and assert the body reacts;
ten body-level checks exercise terrain, sleep, thermal tempo, flight, landing
and the circadian curve. **All 17 pass.**

## Deliberate deviation: the suites are seeded

Upstream runs both suites unseeded, which makes several checks genuinely flaky —
the fly's 0.5 %/s spontaneous-takeoff roll, the `0.9 × dt` ledge-latch roll, and
per-instance variation in the randomised interneuron baselines. Measured over
**60 unseeded runs** of the ported behaviour suite:

```
runs: 60  all-17-pass: 46 (77%)
   15.0%  ledge attach + follow window edge
    8.3%  DNp09 stim -> walks, speed rises (capped)
    1.7%  thermal tempo scales walking speed
```

This is a property of upstream's design, not of the port: the behaviour is
faithfully unchanged, and the same rolls exist in the Swift original. But a
suite that fails 23 % of the time cannot detect a regression, so **the port
seeds both suites by default** (`DEFAULT_SEED = 20260821`). A red result now
always means a real regression.

```sh
./run.sh --behaviortest              # deterministic (default)
./run.sh --behaviortest --no-seed    # sample the stochastic space, as upstream does
./run.sh --behaviortest --seed 1234  # a specific instance
RUNS=60 ./tools/desnap.sh python3 tools/flakiness.py   # re-measure the flake rates
```

Nothing in `simcore/` or `body/` reads the seed at runtime; it only feeds
`numpy.random.default_rng` and `random.seed` at construction.

## What is *not* covered by either suite

Upstream's suites are headless, so they say nothing about the parts that had to
be rewritten. Those were verified by other means:

| area | how it was verified |
|---|---|
| overlay stacking, transparency | screenshotted the live compositor (`tools/shot.sh`) |
| click-through | queried the XShape input region — found and fixed a real bug |
| overlay follows the fly | tracked the override-redirect window's X geometry over time |
| window terrain | spawned a floating window, confirmed a ledge appeared with correct scene coords |
| idle / thermal senses | compared against `org.gnome.Mutter.IdleMonitor` and `/sys/class/thermal` |
| XI2 raw taps | probed with a 1 px synthetic pointer move (never a synthetic click) |
| renderer fidelity | compared against upstream's `assets/fly.png` and `assets/brain.png` |

## Current `--simtest` output

```
circuit: 668 neurons | loom L/R: 162/152 | GF: 2 | DNa L/R: 2/2 | MDN: 4 | DNp09: 2 | DNg11: 6 | escW: 6 | ascend: 27 | sens: 16
spontaneous 4s: pop 5.30 Hz/neuron, LC 0.0 Hz, DNa02 L/R 4.3/5.8 Hz, MDN 0.8 Hz, GF spikes: 0
abrupt loom 0.4s: LC rate 180.1 Hz, GF spikes 2, first at 4 ms
behavior 20s: walk-drive on 36%, groom-drive on 7%, DNp09 0.0-13.8 Hz, pop 6.2 Hz
siesta 15s (scale 0.84): walk-drive on 30%
air puff 1s: GF spikes 21
left-eye loom: DNa L-R rate diff +0.1 -> -4.4 Hz, LC 31.4 Hz
click probes: GF cluster -> spike yes, DNg11 cluster -> groom rate 200 Hz
PASS: GF silent at rest, fires on loom; locomotor drive fluctuates; stim works; siesta alive
```

