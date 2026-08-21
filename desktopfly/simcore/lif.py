"""Leaky-integrate-and-fire simulation of the FlyWire escape/steering circuit.

A faithful port of upstream's ``Sim.swift``.  Upstream steps 668 neurons in a
scalar Swift loop at 1 kHz; here each millisecond is a handful of NumPy
operations over length-668 arrays, which costs roughly the same wall-clock and
keeps the per-millisecond semantics identical.

Ordering inside one millisecond is load-bearing and matches upstream exactly:

1. leak + per-neuron baseline + noise (refractory neurons leak only)
2. sensory injection: loom, gait proprioception, air puff, click stimulation
3. delivery of inhibition scheduled 4 ms ago
4. threshold crossing -> spike, reset, 2 ms refractory
5. propagation: excitation lands this millisecond, inhibition is queued

Step 5 is why the giant fiber can fire at all: the LC->GF electrical drive is
instantaneous while ~1,200 synapses of feedforward inhibition arrive 4 ms late.
"""

from __future__ import annotations

import numpy as np

from .data import Circuit

# group codes for fast per-millisecond spike counting
_G_NONE, _G_LOOM, _G_DNAL, _G_DNAR, _G_MDN, _G_FWD, _G_GROOM, _G_ESCW, _G_GF = range(9)
_N_GROUPS = 9


class LIFSim:
    # --- parameters (upstream Sim.swift) ---
    DECAY = np.float32(0.9512)  # exp(-1/20): 20 ms membrane tau at a 1 ms step
    THRESHOLD = np.float32(1.0)
    REFRACTORY_MS = np.float32(2)
    WEIGHT_SCALE = np.float32(0.0008)
    P_NOISE = np.float32(0.0022)
    NOISE_KICK = np.float32(0.42)
    LOOM_GAIN = np.float32(0.30)
    RATE_ALPHA = np.float32(1.0 / 120.0)
    GAP_JUNCTION_BOOST = np.float32(6.0)
    INH_DELAY_MS = 4
    V_FLOOR = np.float32(-2.0)

    def __init__(self, circuit: Circuit, spike_bus=None, seed: int | None = None):
        self.rng = np.random.default_rng(seed)
        self.spike_bus = spike_bus
        self.n = circuit.n
        self.roles = list(circuit.roles)
        self.types = list(circuit.types)
        self.positions = circuit.positions

        self.v = np.zeros(self.n, dtype=np.float32)
        self.refr = np.zeros(self.n, dtype=np.float32)

        # --- groups ---
        roles = np.asarray(circuit.roles)
        sides = np.asarray(circuit.sides)
        types = np.asarray(circuit.types)
        loom_mask = (roles == "lc4") | (roles == "lplc2")
        dna_mask = (roles == "dna01") | (roles == "dna02")
        self.loom_left = np.flatnonzero(loom_mask & (sides == "left"))
        self.loom_right = np.flatnonzero(loom_mask & (sides != "left"))
        self.gf = np.flatnonzero(roles == "gf")
        self.dna_l = np.flatnonzero(dna_mask & (sides == "left"))
        self.dna_r = np.flatnonzero(dna_mask & (sides != "left"))
        self.mdn = np.flatnonzero(roles == "mdn")
        self.fwd = np.flatnonzero(roles == "dnp09")
        self.groom = np.flatnonzero(roles == "dng11")
        self.escw = np.flatnonzero(roles == "escw")
        self.ascend = np.flatnonzero((roles == "other") & (types == "ascending"))
        self.sens = np.flatnonzero((roles == "other") & (types == "sensory"))
        self.ascend_phase = self.rng.uniform(0, 2 * np.pi, len(self.ascend)).astype(np.float32)

        codes = np.full(self.n, _G_NONE, dtype=np.int32)
        codes[loom_mask] = _G_LOOM
        codes[self.dna_l] = _G_DNAL
        codes[self.dna_r] = _G_DNAR
        codes[self.mdn] = _G_MDN
        codes[self.fwd] = _G_FWD
        codes[self.groom] = _G_GROOM
        codes[self.escw] = _G_ESCW
        codes[self.gf] = _G_GF
        self._codes = codes

        # --- heterogeneous baseline drive ---
        # Interneurons crackle at a few Hz; command DNs get deterministic,
        # side-symmetric baselines so that any left/right asymmetry has to come
        # from the wiring rather than from the random draw.
        base = np.full(self.n, 0.002, dtype=np.float32)  # gf: quiet unless driven
        base[roles == "other"] = self.rng.uniform(0.010, 0.070, int((roles == "other").sum()))
        base[loom_mask] = 0.004
        base[dna_mask | (roles == "mdn") | (roles == "dng11") | (roles == "escw")] = 0.036
        base[roles == "dnp09"] = 0.038
        self.baseline = base

        # --- CSR adjacency with pre-scaled, signed weights ---
        edges = circuit.edges
        pre = edges[:, 0].astype(np.int64)
        post = edges[:, 1].astype(np.int64)
        weight = edges[:, 2].astype(np.float32) * self.WEIGHT_SCALE
        # LC4/LPLC2 -> GF and the wind (JO sensory) -> GF pathways couple through
        # electrical synapses, which chemical synapse counts under-represent.
        is_gf = np.zeros(self.n, dtype=bool)
        is_gf[self.gf] = True
        electrical = loom_mask | ((roles == "other") & (types == "sensory"))
        weight = np.where(electrical[pre] & is_gf[post], weight * self.GAP_JUNCTION_BOOST, weight)

        order = np.argsort(pre, kind="stable")
        self.col_idx = post[order]
        self.w = weight[order]
        counts = np.bincount(pre, minlength=self.n)
        self.row_start = np.zeros(self.n + 1, dtype=np.int64)
        np.cumsum(counts, out=self.row_start[1:])
        self._w_pos = self.w >= 0

        # --- delayed-inhibition ring buffer ---
        self.inh_queue = np.zeros((self.INH_DELAY_MS + 1, self.n), dtype=np.float32)
        self.q_head = 0

        # --- inputs (written each frame by the coordinator) ---
        self.loom_l = 0.0
        self.loom_r = 0.0
        self.gait_drive = 0.0
        self.gait_phase = 0.0
        self.air_puff = 0.0
        self.activity_scale = 1.0
        self.sensory_gate = 1.0

        # --- outputs ---
        self.rate_loom = 0.0
        self.rate_dna_l = 0.0
        self.rate_dna_r = 0.0
        self.rate_mdn = 0.0
        self.rate_fwd = 0.0
        self.rate_groom = 0.0
        self.rate_escw = 0.0
        self.rate_pop = 0.0
        self._gf_latch = False
        self.sim_ms = 0
        self.total_spikes = 0

        self._burst_until = 0
        self._burst_next = 12_000
        self._pending_stims: list[tuple[np.ndarray, float, int]] = []
        self._active_stims: list[tuple[np.ndarray, float, int]] = []

        # scratch buffers reused every millisecond
        self._n_recip = np.float32(1000.0)

    # ------------------------------------------------------------------ API

    def stimulate(self, indices, strength: float, duration_ms: int) -> None:
        """'Optogenetic' stimulation, as fired by brain-window clicks."""
        idx = np.asarray(indices, dtype=np.int64)
        if idx.size == 0:
            return
        self._pending_stims.append((idx, float(strength), int(duration_ms)))
        if len(self._pending_stims) > 8:
            self._pending_stims.pop(0)

    def consume_gf(self) -> bool:
        """True if the giant fiber spiked since the last call (latching)."""
        s = self._gf_latch
        self._gf_latch = False
        return s

    def step(self, ms: int) -> None:
        if ms <= 0:
            return

        for idx, strength, dur in self._pending_stims:
            self._active_stims.append((idx, strength, self.sim_ms + dur))
        self._pending_stims.clear()
        self._active_stims = [s for s in self._active_stims if self.sim_ms < s[2]]

        v = self.v
        refr = self.refr
        spiked_sample: list[tuple[int, bool]] = []

        for _ in range(ms):
            self.sim_ms += 1
            if self.sim_ms >= self._burst_next:
                self._burst_until = self.sim_ms + 400
                self._burst_next = self.sim_ms + int(self.rng.integers(15_000, 40_001))
            p = (self.P_NOISE * 6 if self.sim_ms < self._burst_until else self.P_NOISE)
            p = p * self.activity_scale

            # 1. leak everywhere; baseline + noise only for non-refractory cells
            resting = refr <= 0
            v *= self.DECAY
            v[resting] += self.baseline[resting] * np.float32(self.activity_scale)
            kick = resting & (self.rng.random(self.n) < p)
            v[kick] += self.NOISE_KICK
            np.subtract(refr, 1, out=refr, where=~resting)

            # 2. sensory injection
            if self.loom_l > 0.001:
                v[self.loom_left] += np.float32(self.loom_l * self.LOOM_GAIN * self.sensory_gate)
            if self.loom_r > 0.001:
                v[self.loom_right] += np.float32(self.loom_r * self.LOOM_GAIN * self.sensory_gate)
            if self.gait_drive > 0.001:
                ph = np.float32(self.gait_phase * 2 * np.pi)
                v[self.ascend] += np.float32(self.gait_drive * 0.09) * (
                    0.5 + 0.5 * np.sin(ph + self.ascend_phase)
                )
            if self.air_puff > 0.001:
                v[self.sens] += np.float32(self.air_puff * 0.12 * self.sensory_gate)
            for idx, strength, until in self._active_stims:
                if self.sim_ms < until:
                    v[idx] += np.float32(strength)

            # 3. inhibition scheduled 4 ms ago
            slot = self.inh_queue[self.q_head]
            if slot.any():
                np.add(v, slot, out=v)
                np.maximum(v, self.V_FLOOR, out=v)
                slot.fill(0)

            # 4. threshold crossing
            fired = (refr <= 0) & (v >= self.THRESHOLD)
            spiked = np.flatnonzero(fired)
            if spiked.size:
                v[spiked] = 0
                refr[spiked] = self.REFRACTORY_MS
                self.total_spikes += spiked.size

                # 5. propagate along the CSR rows of the neurons that fired
                starts = self.row_start[spiked]
                counts = self.row_start[spiked + 1] - starts
                total = int(counts.sum())
                if total:
                    prefix = np.concatenate(([0], np.cumsum(counts)[:-1]))
                    gather = np.repeat(starts - prefix, counts) + np.arange(total)
                    cols = self.col_idx[gather]
                    ws = self.w[gather]
                    pos = self._w_pos[gather]
                    if pos.any():
                        exc = np.bincount(cols[pos], weights=ws[pos], minlength=self.n)
                        np.add(v, exc.astype(np.float32), out=v)
                    neg = ~pos
                    if neg.any():
                        inh_slot = (self.q_head + self.INH_DELAY_MS) % self.inh_queue.shape[0]
                        inh = np.bincount(cols[neg], weights=ws[neg], minlength=self.n)
                        self.inh_queue[inh_slot] += inh.astype(np.float32)

            self.q_head = (self.q_head + 1) % self.inh_queue.shape[0]

            # 6. per-group rates (Hz per neuron, EMA with tau = 120 ms)
            if spiked.size:
                gc = np.bincount(self._codes[spiked], minlength=_N_GROUPS)
                if gc[_G_GF]:
                    self._gf_latch = True
            else:
                gc = _ZERO_GROUPS
            a = float(self.RATE_ALPHA)
            self.rate_loom += (gc[_G_LOOM] * 1000.0 / max(1, len(self.loom_left) + len(self.loom_right)) - self.rate_loom) * a
            self.rate_dna_l += (gc[_G_DNAL] * 1000.0 / max(1, len(self.dna_l)) - self.rate_dna_l) * a
            self.rate_dna_r += (gc[_G_DNAR] * 1000.0 / max(1, len(self.dna_r)) - self.rate_dna_r) * a
            self.rate_mdn += (gc[_G_MDN] * 1000.0 / max(1, len(self.mdn)) - self.rate_mdn) * a
            self.rate_fwd += (gc[_G_FWD] * 1000.0 / max(1, len(self.fwd)) - self.rate_fwd) * a
            self.rate_groom += (gc[_G_GROOM] * 1000.0 / max(1, len(self.groom)) - self.rate_groom) * a
            self.rate_escw += (gc[_G_ESCW] * 1000.0 / max(1, len(self.escw)) - self.rate_escw) * a
            self.rate_pop += (spiked.size * 1000.0 / max(1, self.n) - self.rate_pop) * a

            if self.spike_bus is not None and spiked.size:
                stride = max(1, spiked.size // 12)
                for i in range(0, spiked.size, stride):
                    j = int(spiked[i])
                    spiked_sample.append((j, self._codes[j] == _G_GF))

        if self.spike_bus is not None and spiked_sample:
            self.spike_bus.push(spiked_sample)


_ZERO_GROUPS = np.zeros(_N_GROUPS, dtype=np.int64)


class SpikeBus:
    """Hand-off of sampled spikes from the sim to the brain window.

    Upstream needs a lock because the sim runs on SceneKit's render thread;
    here everything shares one GLib main loop, so a plain list suffices.
    """

    def __init__(self, limit: int = 256):
        self._events: list[tuple[int, bool]] = []
        self._limit = limit

    def push(self, events) -> None:
        self._events.extend(events)
        if len(self._events) > self._limit:
            del self._events[: len(self._events) - self._limit]

    def pop_all(self) -> list[tuple[int, bool]]:
        e = self._events
        self._events = []
        return e
