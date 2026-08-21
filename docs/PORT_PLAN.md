# DesktopFly → Linux: port plan and record

Upstream is ~2,550 lines of Swift across five files plus `etl.py` and derived
data. The port splits cleanly into three layers of very different difficulty,
and that split drove the whole plan.

| layer | upstream | difficulty | approach | status |
|---|---|---|---|---|
| simulation | `Sim.swift` | easy | direct translation to NumPy | **done, validated** |
| behaviour | `FlyModel.swift` | easy | direct translation | **done, validated** |
| rendering | SceneKit/Metal | medium | rewrite: analytic 2D renderer | **done** |
| desktop ecology | Cocoa/CGWindowList | hard | rewrite on X11/XWayland + D-Bus | **done, with limits** |

## Layout

```
pretty_fly/
├── run.sh                     launcher (scrubs the VS Code snap env)
├── data -> upstream/data      the CC BY-NC 4.0 derived FlyWire files
├── upstream/                  pristine clone of the macOS original (reference)
├── desktopfly/
│   ├── cli.py                 --simtest/--behaviortest/--snapshot/--brainshot
│   ├── app.py                 Coordinator + AppDelegate equivalent
│   ├── simcore/               data.py, lif.py, signals.py   <- the connectome
│   ├── body/                  model.py (skeleton), fly.py (behaviour)
│   ├── render/                geometry.py, fly_renderer.py, brain_renderer.py
│   ├── platform/              overlay, brain_window, tray, environment, taps
│   └── tests/                 simtest.py, behaviortest.py
├── tools/                     desnap.sh, shot.sh, overlay_check.py, flakiness.py
└── docs/                      PORT_PLAN.md, PARITY.md
```

`upstream/` is never edited. `desktopfly/` mirrors upstream's file boundaries so
the two can be diffed by eye when upstream moves.

## The simulation

`Sim.swift` steps 668 neurons in a scalar loop at 1 kHz. `simcore/lif.py` makes
each millisecond a handful of NumPy operations over length-668 arrays. The
per-millisecond ordering is load-bearing and preserved exactly:

1. leak + baseline + noise (refractory cells leak only)
2. sensory injection (loom, gait proprioception, air puff, click stimulation)
3. delivery of inhibition scheduled 4 ms earlier
4. threshold crossing → spike, reset, 2 ms refractory
5. propagation — excitation lands immediately, inhibition is queued

Step 5 is the whole trick: the LC→GF electrical drive (×6 gap-junction boost) is
instantaneous while ~1,200 synapses of feedforward inhibition arrive 4 ms late,
which is why slow approaches are tolerated and fast lunges trigger escape.

Two places needed care rather than transcription:

- **Clamping.** Swift clamps `v` to ≥ −2 on every individual synaptic write.
  The vectorised version accumulates with `np.bincount` and clamps once. This is
  equivalent: excitatory weights are non-negative and `v` can never already be
  below the floor, so per-edge clamping is a no-op for them; inhibition is
  accumulated into the delay queue and clamped once on delivery, exactly as
  upstream does.
- **Noise.** Upstream draws a random number only for non-refractory neurons.
  Drawing for all and masking is statistically identical and vectorises.

Measured: **~9.4× realtime**, so a 16 ms frame's 16 sim steps cost well under 2 ms.

## Rendering

Upstream's overlay camera is **orthographic**, parked on +Z, `orthographicScale
= height/2` — one scene unit is one pixel. That makes an exact analytic renderer
possible, and it is both simpler and better-looking at fly size than rasterising
a mesh would be:

- an ellipsoid under orthographic projection is *exactly* an ellipse, whose
  semi-axes are the singular values of the projected 2×3 transform (`geometry.
  ellipse_from_matrix`);
- capsules (leg segments) are round-capped thick lines, placed by forward
  kinematics down upstream's coxa→knee→ankle→tarsus chain;
- the wings are planar outlines transformed into 3D and projected.

So the fly is ~25 cairo paths and needs no depth buffer: with a fixed top-down
view, a fixed part order matches the Z order.

The brain window *is* perspective (46° FOV), so it divides by depth. Its 23,210
points are scattered into a float accumulator with `np.bincount` and blended
additively — which is also why it needs no depth sorting, matching upstream's
`writesToDepthBuffer = false`. Measured **67 fps** at its real 340×280 size.

The abdomen's dark tergite bands are upstream's 64×128 texture converted back to
normalised body-Y spans, and are drawn along the **body axis** — not the
projected ellipse's major axis, which coincides only when the fly happens to be
axis-aligned.

### Why no GI/cairo bridge

`python3-gi-cairo` is deliberately not required. That
package supplies the foreign-struct converter for `cairo.Context` and
`cairo.Region`, without which GTK's `draw` signal and
`input_shape_combine_region` are both unusable. Rather than depend on it, the
port draws into a plain pycairo `ImageSurface`, blits through `GdkPixbuf`, and
sets input shapes with raw XShape calls. This removes a dependency rather than
adding one, and still runs the overlay at a full 60 fps.

## The desktop overlay

Upstream uses one full-screen click-through `NSWindow` at `.floating` level.
On GNOME/Wayland:

- **wlr-layer-shell does not exist on Mutter**, so the usual Wayland overlay
  protocol is unavailable. The port therefore targets **XWayland**
  (`GDK_BACKEND=x11`), where an **override-redirect** window bypasses the window
  manager and Mutter stacks it above ordinary windows. Verified by screenshot.
- **Per-fly windows, not full-screen.** Painting a 1920×1880 ARGB surface every
  frame to show a 40 px fly is wasteful, so each fly gets its own 160×160 window
  that is *moved* to follow it. Render cost becomes independent of screen size.
- **Click-through** is an empty XShape *input* region. This must be applied
  **after** the window maps: GDK rewrites the input shape during map, so a shape
  set in the `realize` handler is silently discarded. That bug was caught by
  querying `shape_get_rectangles` rather than by assuming it worked — without
  the fix, a 160×160 square would have swallowed clicks wherever the fly went.

## Known limitations

**Native Wayland windows are invisible.** `_NET_CLIENT_LIST` enumerates XWayland
clients only, and XInput2 raw events only report input routed through XWayland.
No unprivileged Wayland client can enumerate other clients' geometry or observe
their input — the protocol deliberately has no such capability. Consequences:
the fly cannot land on a native Wayland window's top edge, is not startled by a
click into one, and does not react to it appearing. X11/XWayland apps (including
VS Code and most Electron apps) work fully.

The only ways around this are a **GNOME Shell extension** (full window access via
`Meta.get_window_actors()`, but it means rewriting everything above the sim in
GJS/Clutter and running the 1 kHz loop out-of-process over D-Bus to avoid
stuttering the compositor) or running a plain X11 session.

**Fractional scaling softens the fly.** On a monitor at scale 1.0 the fly is
pixel-exact. On a fractionally-scaled monitor (this machine's laptop panel is
2880×1800 shown at 1280×800, i.e. 2.25×) Mutter renders XWayland content at
logical resolution and upscales it, so the fly is slightly soft there. Nothing
the app can do about it from inside the X window; the system-level option is
Mutter's `xwayland-native-scaling` experimental feature, which is the user's
call because it affects every X11 app.

**Typing is inferred, not read.** macOS can ask "how long since the last
keyDown"; GNOME exposes only a combined idle counter. The port infers a
keystroke from the idle counter resetting while the pointer has not moved. That
keeps upstream's "knows *when*, never *which*" property intact.

**No shadow-casting light.** Upstream uses a real SceneKit deferred shadow. The
port draws a soft elliptical shadow that slides away and fades with altitude —
visually equivalent at this size, but it is a drawn approximation.
