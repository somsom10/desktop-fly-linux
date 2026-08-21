"""Sugar drops the fly can smell, walk to, and feed on.

**This is an addition, not part of upstream.** It is worth being precise about
what is real here, in the spirit of upstream's "What's modeled vs. measured":

The 668-neuron circuit extracted from FlyWire v783 is an *escape / steering /
locomotion* circuit. It contains **no gustatory pathway** — no sugar gustatory
receptor neurons, no MN9 proboscis motor neuron, no Fdg feeding neurons. So:

* **Modeled**: the odour field itself, its falloff, and the transduction from
  "sugar concentration and bearing" into injected current. Also the feeding
  decision on contact, because contact chemoreception has no substrate in this
  circuit.
* **Real**: what that current is injected *into* and everything downstream of
  it — the drive lands on the actual DNa01/DNa02 steering neurons and the actual
  DNp09 walking command neuron, and the turn and the gait that come out are
  produced by the real network through real synapses.

That is exactly the split upstream already draws for the cursor: "the sensory
transduction (cursor -> looming value) [is a] standard modeling choice layered
on the real graph", while everything past the sensory population is FlyWire data.

Flies find sugar by odour at a distance and by *contact* chemoreception with the
tarsi and labellum, so the field has two ranges: a broad odour plume that biases
steering, and a small contact radius where feeding actually starts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Scene units are pixels (upstream's orthographic camera is 1 unit = 1 px).
# Odour falloff. Sized so a drop is smellable across a whole monitor: the
# neural response measured on this circuit has a floor (drive below ~0.1 moves
# the DNa rates not at all), so too tight a plume makes sugar simply invisible.
PLUME_SIGMA = 450.0
CONTACT_RADIUS = 16.0  # tarsal/labellar contact -> feeding can start
FEED_RATE = 0.16  # fraction of a drop consumed per second of feeding
DROP_AMOUNT = 1.0
DROP_RADIUS = 7.5


@dataclass
class SugarDrop:
    x: float
    y: float
    amount: float = DROP_AMOUNT
    radius: float = DROP_RADIUS
    # visual only: drops settle into a puddle over the first moment
    age: float = 0.0

    @property
    def spent(self) -> bool:
        return self.amount <= 0.02

    @property
    def draw_radius(self) -> float:
        settle = min(1.0, self.age / 0.35)
        return self.radius * (0.35 + 0.65 * settle) * (0.45 + 0.55 * self.amount)


@dataclass
class SugarField:
    """Every sugar drop currently on this monitor, and how the fly senses them."""

    drops: list[SugarDrop] = field(default_factory=list)
    max_drops: int = 12

    def add(self, x: float, y: float) -> SugarDrop:
        drop = SugarDrop(x=x, y=y)
        self.drops.append(drop)
        while len(self.drops) > self.max_drops:
            self.drops.pop(0)
        return drop

    def clear(self) -> None:
        self.drops.clear()

    def __len__(self) -> int:
        return len(self.drops)

    def tick(self, dt: float) -> None:
        for d in self.drops:
            d.age += dt
        self.drops = [d for d in self.drops if not d.spent]

    # -------------------------------------------------------------- sensing

    def concentration_at(self, x: float, y: float) -> float:
        """Total smell strength at a point, 0..1-ish (drops sum)."""
        total = 0.0
        for d in self.drops:
            dx, dy = d.x - x, d.y - y
            total += d.amount * math.exp(-(dx * dx + dy * dy) / (2 * PLUME_SIGMA * PLUME_SIGMA))
        return min(1.0, total)

    def attractant_vector(self, x: float, y: float) -> tuple[float, float, float]:
        """Unit vector toward the smell, plus the concentration driving it.

        Each drop pulls along its own bearing, weighted by how strongly it is
        smelled from here; the sum is the direction a chemotaxing fly would
        steer toward.
        """
        vx = vy = 0.0
        for d in self.drops:
            dx, dy = d.x - x, d.y - y
            dist = math.hypot(dx, dy)
            if dist < 1e-3:
                continue
            w = d.amount * math.exp(-(dist * dist) / (2 * PLUME_SIGMA * PLUME_SIGMA))
            vx += dx / dist * w
            vy += dy / dist * w
        mag = math.hypot(vx, vy)
        if mag < 1e-6:
            return 0.0, 0.0, 0.0
        return vx / mag, vy / mag, min(1.0, mag)

    def contact(self, x: float, y: float) -> SugarDrop | None:
        """The drop the fly is standing on, if any."""
        best = None
        best_d = CONTACT_RADIUS
        for d in self.drops:
            dist = math.hypot(d.x - x, d.y - y)
            if dist <= max(best_d, d.radius):
                if best is None or dist < best_d:
                    best, best_d = d, dist
        return best

    def consume(self, drop: SugarDrop, dt: float) -> float:
        """Eat from a drop; returns how much was actually taken."""
        take = min(drop.amount, FEED_RATE * dt)
        drop.amount -= take
        return take


@dataclass
class SugarSense:
    """What the sugar field presents to the nervous system this frame."""

    steer_left: float = 0.0  # -> DNa01/DNa02 left  (real neurons)
    steer_right: float = 0.0  # -> DNa01/DNa02 right (real neurons)
    appetite: float = 0.0  # -> DNp09 walking command (real neuron)
    smell: float = 0.0  # modeled signal, for the body/UI only
    contact: SugarDrop | None = None


def _angle_diff(frm: float, to: float) -> float:
    d = math.fmod(to - frm, 2 * math.pi)
    if d > math.pi:
        d -= 2 * math.pi
    if d < -math.pi:
        d += 2 * math.pi
    return d


def sense(field: SugarField, x: float, y: float, heading: float,
          satiety: float = 0.0) -> SugarSense:
    """Transduce the odour field into drive for the real command neurons.

    Purely differential: with the smell dead ahead there is no steering drive at
    all, only appetite, so the fly is nudged off a straight line solely when it
    is actually off course.  Driving the *left* DNa turns counter-clockwise
    (upstream's own behaviour test asserts this), which is the correct way to
    turn toward something on the left.

    A sated fly is not interested: everything scales with ``1 - satiety``.
    """
    out = SugarSense()
    if not field.drops:
        return out
    out.smell = field.concentration_at(x, y)
    out.contact = field.contact(x, y)
    vx, vy, mag = field.attractant_vector(x, y)
    hunger = max(0.0, 1.0 - satiety)
    if mag <= 0.0 or hunger <= 0.0:
        return out
    bearing = _angle_diff(heading, math.atan2(vy, vx))
    t = bearing / math.pi  # -1 (hard right) .. +1 (hard left)
    # Expand small bearing errors. The DNa response has a threshold, so a
    # linear map leaves mild course errors uncorrected and the fly wanders past
    # the drop instead of closing on it.
    shaped = math.copysign(abs(t) ** 0.6, t)
    drive = mag * hunger
    out.steer_left = min(1.0, drive * max(0.0, shaped))
    out.steer_right = min(1.0, drive * max(0.0, -shaped))
    # walk keenly when facing the food, less when it is off to one side
    out.appetite = min(1.0, drive * (0.4 + 0.6 * max(0.0, math.cos(bearing))))
    return out
