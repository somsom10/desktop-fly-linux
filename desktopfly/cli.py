"""Command-line entry point, mirroring upstream's ``DesktopFly`` argument modes."""

from __future__ import annotations

import argparse
import sys

USAGE = """desktopfly — a 3D fruit fly on your Linux desktop, driven by a live
spiking simulation of the real FlyWire v783 connectome."""


def _snapshot(path: str) -> int:
    """Offscreen fly render — upstream's ``--snapshot``.

    Upstream frames this with a perspective 3/4 'hero' camera. The Linux
    renderer is analytic and orthographic (see render/geometry.py), so this is
    the same body drawn from the overlay's top-down view, magnified.
    """
    import cairo

    from .body.fly import Fly, State
    from .render.fly_renderer import FlyRenderer

    size = 720
    fly = Fly(0.0, 0.0)
    fly.state = State.IDLE
    fly.heading = 3.14159265 / 2
    # upstream's posed legs, so the snapshot is not a flat rest pose
    for leg, angle, lift in zip(
        fly.model.legs,
        [0.25, -0.2, -0.22, 0.28, 0.2, -0.25],
        [0.35, 0.0, 0.0, 0.3, 0.0, 0.35],
    ):
        leg.angle, leg.lift = angle, lift
    fly.sync_node()

    renderer = FlyRenderer(size, zoom=8.0)
    surface = renderer.render(fly)
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(out)
    cr.set_source_rgb(0.94, 0.94, 0.94)
    cr.paint()
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    out.write_to_png(path)
    print(f"snapshot written to {path}")
    return 0


def _brainshot(path: str) -> int:
    """Offscreen brain render — upstream's ``--brainshot``."""
    import numpy as np
    from PIL import Image

    from .render.brain_renderer import BrainRenderer
    from .simcore.data import load_brain_data
    from .simcore.lif import LIFSim, SpikeBus

    points, circuit = load_brain_data()
    bus = SpikeBus()
    sim = LIFSim(circuit, spike_bus=bus, seed=3)
    renderer = BrainRenderer(points, sim, 720, 560)
    renderer.paused = True
    renderer.angle = 0.5  # upstream freezes the brainshot at eulerAngles y=0.5
    sim.step(800)
    sim.stimulate(sim.gf, 0.5, 40)
    sim.step(60)
    renderer.drain_spikes()
    rgba = renderer.render(1 / 30)
    Image.fromarray(np.ascontiguousarray(rgba[:, :, :3])).save(path)
    print(f"brainshot written to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="desktopfly", description=USAGE)
    parser.add_argument("--simtest", action="store_true",
                        help="circuit invariants (GF silence, loom latency, siesta)")
    parser.add_argument("--behaviortest", action="store_true",
                        help="17 end-to-end sim->body checks")
    parser.add_argument("--snapshot", metavar="PNG", help="offscreen fly render")
    parser.add_argument("--brainshot", metavar="PNG", help="offscreen brain render")
    parser.add_argument("--monitor", type=int, default=None,
                        help="index of the monitor to live on (see --list-monitors)")
    parser.add_argument("--list-monitors", action="store_true")
    parser.add_argument("--no-brain", action="store_true", help="start with the brain window hidden")
    parser.add_argument("--sugar-modifier", default="ctrl",
                        choices=["ctrl", "shift", "alt", "super", "none"],
                        help="modifier held with right-click to leave sugar (default: ctrl)")
    parser.add_argument("--seed", type=int, default=None, help="seed the simulation for reproducibility")
    parser.add_argument("--no-seed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.simtest:
        from .tests.simtest import run
        return run(args.seed if args.seed is not None else (None if args.no_seed else _default_seed("simtest")))
    if args.behaviortest:
        from .tests.behaviortest import run
        return run(args.seed if args.seed is not None else (None if args.no_seed else _default_seed("behaviortest")))
    if args.snapshot:
        return _snapshot(args.snapshot)
    if args.brainshot:
        return _brainshot(args.brainshot)

    from .platform.overlay import monitors
    if args.list_monitors:
        for m in monitors():
            star = " (primary)" if m["primary"] else ""
            print(f"[{m['index']}] {m['name']} {m['width']}x{m['height']} "
                  f"at {m['x']},{m['y']}{star}")
        return 0

    from .app import DesktopFly
    app = DesktopFly(monitor_index=args.monitor, show_brain=not args.no_brain,
                     seed=args.seed, sugar_modifier=args.sugar_modifier)
    print(f"desktopfly: {app.data_info}", flush=True)
    print(f"living on monitor [{app.monitor_index}] {app.monitor['name']}; "
          f"quit from the 🪰 tray menu or with Ctrl-C", flush=True)
    mod = args.sugar_modifier
    combo = "right-click" if mod == "none" else f"{mod}+right-click"
    if getattr(app.sugar_grab, "error", None):
        print(f"warning: {app.sugar_grab.error}", flush=True)
    print(f"sugar: {combo} to leave a drop (or the tray menu)", flush=True)
    if not app.tray.available:
        print("note: no AppIndicator tray available — quit with Ctrl-C", flush=True)
    try:
        app.run()
    except KeyboardInterrupt:
        print()
    return 0


def _default_seed(which: str) -> int:
    from .tests import behaviortest, simtest
    return simtest.DEFAULT_SEED if which == "simtest" else behaviortest.DEFAULT_SEED


if __name__ == "__main__":
    sys.exit(main())
