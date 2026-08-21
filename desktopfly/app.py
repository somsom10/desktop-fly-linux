"""Application wiring: the render loop, the senses, and the overlay windows.

Port of ``Coordinator`` and ``AppDelegate`` from upstream's ``main.swift``.

Upstream splits work across SceneKit's render thread and the AppKit main thread,
with a lock and a pending-action queue between them. Here everything — sim step,
body update, rendering, timers, menu actions — runs on the single GLib main
loop, so that machinery is unnecessary; the sim is fast enough (about 9x
realtime) that a 16 ms frame has ample budget for its 16 one-millisecond steps.
"""

from __future__ import annotations

import math
import time
from datetime import datetime

from .body.fly import Fly, State
from .platform.brain_window import BrainWindow
from .platform.environment import (
    IdleSense,
    TypingSense,
    WindowSense,
    circadian_activity,
    thermal_tempo,
)
from .platform.gtkcompat import GLib, Gtk
from .platform.overlay import Overlay, monitors, pointer_position
from .platform.taps import TapSense
from .platform.tray import Tray
from .render.brain_renderer import BrainRenderer
from .render.fly_renderer import SIZE as TILE, FlyRenderer
from .simcore.data import load_brain_data
from .simcore.lif import LIFSim, SpikeBus
from .simcore.signals import BrainSignals, SignalBuilder, clampf

FRAME_MS = 16
AMBIENT_EVERY = 2  # ambient senses at ~30 Hz
WINDOW_POLL_S = 0.7


class FlyView:
    """One fly plus the overlay window that carries it."""

    def __init__(self, fly: Fly):
        self.fly = fly
        self.overlay = Overlay(TILE, TILE)
        self.renderer = FlyRenderer(TILE)

    def draw(self, monitor: dict) -> None:
        surface = self.renderer.render(self.fly)
        self.overlay.blit(surface)
        cx = monitor["x"] + monitor["width"] / 2 + self.fly.pos_x
        cy = monitor["y"] + monitor["height"] / 2 - self.fly.pos_y
        self.overlay.move(cx - TILE / 2, cy - TILE / 2)

    def destroy(self) -> None:
        self.overlay.destroy()


class DesktopFly:
    def __init__(self, monitor_index: int | None = None, show_brain: bool = True,
                 seed: int | None = None):
        self.monitors = monitors()
        if not self.monitors:
            raise RuntimeError("no monitors reported by GDK")
        idx = monitor_index if monitor_index is not None else self._default_monitor()
        self.monitor_index = max(0, min(idx, len(self.monitors) - 1))

        points, circuit = load_brain_data()
        self.spike_bus = SpikeBus()
        self.sim = LIFSim(circuit, spike_bus=self.spike_bus, seed=seed)
        self.data_info = (
            f"FlyWire v783 · {len(points)} somas · "
            f"circuit {circuit.n}n/{len(circuit.edges)}e"
        )
        self.signal_builder = SignalBuilder()

        self.views: list[FlyView] = [FlyView(Fly(*self._random_start()))]
        self.paused = False

        self.brain_renderer = BrainRenderer(points, self.sim)
        self.brain = BrainWindow(self.brain_renderer, self.sim, on_close=self.quit)
        self.brain.place_bottom_right(self.monitor)
        if show_brain:
            self.brain.show()

        self.window_sense = WindowSense()
        self.idle_sense = IdleSense()
        self.typing_sense = TypingSense(self.idle_sense)
        self.tap_sense = TapSense()
        self.terrain = []
        self.typing_level = 0.0
        self.sleepy = False
        self.tempo = 1.0
        self.activity = 1.0
        self._window_loom_l = 0.0
        self._window_loom_r = 0.0
        self._loom_override = 0.0
        self._prev_mouse = None
        self._mouse_vel = [0.0, 0.0]
        self._ms_accumulator = 0.0
        self._last_t = None
        self._frame = 0
        self._last_window_poll = 0.0

        self.tray = self._build_tray()

    # --------------------------------------------------------- geometry

    @property
    def monitor(self) -> dict:
        return self.monitors[self.monitor_index]

    @property
    def bounds(self) -> tuple[float, float]:
        m = self.monitor
        return (float(m["width"]), float(m["height"]))

    def _default_monitor(self) -> int:
        for i, m in enumerate(self.monitors):
            if m["primary"]:
                return i
        return 0

    def _random_start(self) -> tuple[float, float]:
        import random

        w, h = self.bounds if hasattr(self, "monitor_index") else (800, 600)
        return random.uniform(-w / 2 + 100, w / 2 - 100), random.uniform(-h / 2 + 100, h / 2 - 100)

    def _scene_from_screen(self, px: float, py: float) -> tuple[float, float]:
        m = self.monitor
        return (px - (m["x"] + m["width"] / 2), (m["y"] + m["height"] / 2) - py)

    # ------------------------------------------------------------- menu

    def _build_tray(self) -> Tray:
        actions = {
            "pause": ("Pause", lambda _i: self.toggle_pause()),
            "brain": ("Show/Hide Brain", lambda _i: self.brain.toggle()),
            "escape": ("Escape Test (loom)", lambda _i: self.escape_test()),
            "display": ("Move to Next Display", lambda _i: self.next_display()),
            "sep1": (None, None),
            "add": ("Add Fly", lambda _i: self.add_fly()),
            "remove": ("Remove Fly", lambda _i: self.remove_fly()),
            "scare": ("Scare Flies", lambda _i: self.scare_all()),
            "sep2": (None, None),
            "quit": ("Quit", lambda _i: self.quit()),
        }
        tray = Tray("Desktop Fly", self.data_info, actions)
        tray.set_visible("display", len(self.monitors) > 1)
        return tray

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self._last_t = None
        self.tray.set_label("pause", "Resume" if self.paused else "Pause")

    def escape_test(self) -> None:
        self._loom_override = 0.6

    def add_fly(self) -> None:
        self.views.append(FlyView(Fly(*self._random_start())))
        self.views[-1].overlay.raise_()

    def remove_fly(self) -> None:
        if len(self.views) > 1:  # fly #1 carries the brain
            self.views.pop().destroy()

    def scare_all(self) -> None:
        self._loom_override = 0.6  # a real stimulus into the real circuit
        for view in self.views[1:]:
            if view.fly.state is not State.FLYING:
                view.fly.start_flight(self.bounds)

    def next_display(self) -> None:
        if len(self.monitors) < 2:
            return
        self.monitor_index = (self.monitor_index + 1) % len(self.monitors)
        w, h = self.bounds
        self.terrain = []
        for view in self.views:
            view.fly.ledge = None
            view.fly.pos_x = clampf(view.fly.pos_x, -w / 2 + 40, w / 2 - 40)
            view.fly.pos_y = clampf(view.fly.pos_y, -h / 2 + 40, h / 2 - 40)
        self.brain.place_bottom_right(self.monitor)

    def quit(self) -> None:
        Gtk.main_quit()

    # ------------------------------------------------------------ senses

    def _poll_ambient(self, dt: float) -> None:
        pointer = pointer_position()
        self.typing_level += (self.typing_sense.poll(pointer, dt) - self.typing_level) * 0.15
        idle = self.idle_sense.seconds()
        now = datetime.now()
        hour = now.hour + now.minute / 60.0
        self.sleepy = (idle > 600 and (hour >= 22 or hour < 6)) or idle > 1800
        self.tempo = thermal_tempo()
        self.activity = circadian_activity(hour)

    def _poll_windows(self) -> None:
        m = self.monitor
        snap = self.window_sense.poll((m["x"], m["y"], m["width"], m["height"]))
        self.terrain = snap.ledges
        if not self.views:
            return
        fly = self.views[0].fly
        for centre, _size in snap.new_windows:
            d = math.hypot(centre[0] - fly.pos_x, centre[1] - fly.pos_y)
            strength = clampf(1 - d / 480, 0, 1) * 0.75
            if strength > 0.08:
                self._inject_window_loom(strength, centre, fly)

    def _inject_window_loom(self, strength: float, centre, fly) -> None:
        rel = (centre[0] - fly.pos_x, centre[1] - fly.pos_y)
        dist = max(1.0, math.hypot(*rel))
        f = (math.cos(fly.heading), math.sin(fly.heading))
        cross_z = (f[0] * rel[1] - f[1] * rel[0]) / dist
        self._window_loom_l = max(self._window_loom_l,
                                  strength * clampf(0.5 + 0.5 * cross_z, 0.12, 1))
        self._window_loom_r = max(self._window_loom_r,
                                  strength * clampf(0.5 - 0.5 * cross_z, 0.12, 1))

    def _inject_tap(self, mouse, fly) -> None:
        d = math.hypot(mouse[0] - fly.pos_x, mouse[1] - fly.pos_y)
        strength = clampf(1 - d / 520, 0, 1)
        if strength > 0.05:
            self.sim.stimulate(self.sim.sens, 0.15 + strength * 0.35, 130)

    def _compute_loom(self, fly, mouse, dt):
        """Cursor kinematics -> per-eye looming drive + air puff.

        This is the sensory transduction step; everything downstream of the
        LC4/LPLC2 population is the real connectome.
        """
        if mouse is None:
            return 0.0, 0.0, 0.0
        if self._prev_mouse is not None and dt > 0:
            vx = (mouse[0] - self._prev_mouse[0]) / dt
            vy = (mouse[1] - self._prev_mouse[1]) / dt
            self._mouse_vel[0] += (vx - self._mouse_vel[0]) * 0.4
            self._mouse_vel[1] += (vy - self._mouse_vel[1]) * 0.4
        self._prev_mouse = mouse
        rel = (mouse[0] - fly.pos_x, mouse[1] - fly.pos_y)
        dist = max(20.0, math.hypot(*rel))
        # radial approach speed (positive = cursor closing in)
        approach = -(rel[0] * self._mouse_vel[0] + rel[1] * self._mouse_vel[1]) / dist
        loom = clampf(approach / dist * 6, 0, 1) * clampf(1 - dist / 800, 0, 1)
        loom += clampf((130 - dist) / 130, 0, 1) * 0.5  # hovering close = big object
        loom = clampf(loom + self._loom_override, 0, 1)
        f = (math.cos(fly.heading), math.sin(fly.heading))
        rd = (rel[0] / dist, rel[1] / dist)
        cross_z = f[0] * rd[1] - f[1] * rd[0]  # > 0: threat on the left
        lw = clampf(0.5 + 0.5 * cross_z, 0.12, 1)
        rw = clampf(0.5 - 0.5 * cross_z, 0.12, 1)
        puff = clampf(math.hypot(*self._mouse_vel) / 1500, 0, 1) * clampf(1 - dist / 500, 0, 1)
        return loom * lw, loom * rw, puff

    # ------------------------------------------------------------- loop

    def tick(self) -> bool:
        now = time.monotonic()
        if self.paused:
            self._last_t = now
            return True
        if self._last_t is None:
            self._last_t = now
            return True
        dt = min(0.05, max(0.0, now - self._last_t))
        self._last_t = now
        self._frame += 1

        if self._frame % AMBIENT_EVERY == 0:
            self._poll_ambient(dt * AMBIENT_EVERY)
        if now - self._last_window_poll > WINDOW_POLL_S:
            self._last_window_poll = now
            self._poll_windows()

        px, py = pointer_position()
        mouse = self._scene_from_screen(px, py)

        # left click anywhere = a tap on the fly's substrate -> sensory pathway
        for click in self.tap_sense.poll():
            if click.button == 1 and self.views:
                self._inject_tap(self._scene_from_screen(click.x, click.y), self.views[0].fly)

        signals = None
        if self.views:
            first = self.views[0].fly
            loom_l, loom_r, puff = self._compute_loom(first, mouse, dt)
            decay = math.exp(-4 * dt)
            self._window_loom_l *= decay
            self._window_loom_r *= decay
            self.sim.loom_l = max(loom_l, self._window_loom_l)
            self.sim.loom_r = max(loom_r, self._window_loom_r)
            self.sim.air_puff = max(puff, self.typing_level * 0.30)
            # body -> brain: leg proprioception from the current gait
            self.sim.gait_drive = first.walking_intensity
            self.sim.gait_phase = first.gait_phase
            # Circadian/sleep neuromodulation, compressed: the LIF neurons sit
            # just below threshold, so a raw multiplier silences them outright —
            # siesta must mean "less active", not comatose.
            self.sim.activity_scale = (1 - (1 - self.activity) * 0.35) * (
                0.75 if self.sleepy else 1.0
            )
            self.sim.sensory_gate = 0.55 if self.sleepy else 1.0
            self._loom_override = max(0.0, self._loom_override - dt * 1.2)

            self._ms_accumulator += dt * 1000
            steps = min(50, int(self._ms_accumulator))
            self._ms_accumulator -= steps
            self.sim.step(steps)

            signals = self.signal_builder.make(self.sim, dt)
            signals.tempo = self.tempo
            signals.sleep = self.sleepy

        mon = self.monitor
        for i, view in enumerate(self.views):
            view.fly.terrain = self.terrain
            view.fly.update(dt, self.bounds, mouse, signals if i == 0 else None)
            view.draw(mon)

        if self._frame % 2 == 0:
            self.brain.draw(dt * 2)
        return True

    def run(self) -> None:
        GLib.timeout_add(FRAME_MS, self.tick)
        Gtk.main()
