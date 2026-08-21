# desktopfly (Linux port) — agent notes

Linux port of `DenisSergeevitch/desktop-fly`. The macOS original is kept
verbatim in `upstream/` and is **never edited** — it is the reference. Read
`upstream/CLAUDE.md` first: its invariants and tuning gotchas are binding here
too, because `simcore/` and `body/` are direct translations of that code.

## Run and verify

```sh
./run.sh                    # the app
./run.sh --simtest          # circuit invariants (MUST pass after sim changes)
./run.sh --behaviortest     # 21 sim->body checks (MUST pass after behaviour changes)
./run.sh --list-monitors
```

**Always run both suites after any change.** They are seeded by default, so a
failure is a real regression, not bad luck — see `docs/PARITY.md`.

## Environment trap

Claude Code here runs inside the **VS Code snap**, which exports `GTK_PATH`,
`GDK_PIXBUF_MODULE_FILE`, `XDG_DATA_HOME` and friends pointing at its own
bundled GTK. Any system PyGObject process launched from this shell dies with
`symbol lookup error: ... __libc_pthread_init`. Everything GUI must go through
`tools/desnap.sh` (which `run.sh` does for you).

Also: `gi.require_version` must run before any `gi.repository` import, or gi
picks GTK 4 and the next `require_version("Gtk", "3.0")` raises. Import
Gtk/Gdk/GLib from `desktopfly.platform.gtkcompat`, never from `gi.repository`.

## File map (mirrors upstream's boundaries)

| here | upstream |
|---|---|
| `simcore/lif.py`, `data.py` | `Sim.swift` |
| `simcore/signals.py` | `SignalBuilder` in `main.swift` |
| `body/fly.py`, `body/model.py` | `FlyModel.swift` |
| `render/*` | `SceneKit` scene + `BrainView.swift` |
| `platform/environment.py` | `Environment.swift` |
| `platform/overlay.py`, `tray.py`, `taps.py` | `AppDelegate` in `main.swift` |
| `app.py` | `Coordinator` + `AppDelegate` |
| `tests/*` | `runSimtest()`, `runBehaviorTest()` |

## Port-specific gotchas

- **The overlay's input shape must be set after `map-event`.** GDK rewrites it
  during map, so a shape set in `realize` is silently discarded and the fly
  swallows clicks. Verify with `shape_get_rectangles(SK.Input)` — 0 rectangles
  means click-through is live.
- **No GI/cairo bridge** (`python3-gi-cairo` is absent). Never use GTK's `draw`
  signal or anything taking a `cairo.Region`. Draw into a pycairo
  `ImageSurface`, blit via `GdkPixbuf`, shape via raw XShape.
- **The sim runs on the GLib main loop**, not a render thread, so upstream's
  lock/`enqueue` machinery is deliberately absent. Do not reintroduce it without
  also introducing threads.
- **Abdomen bands follow the body Y axis**, not the projected ellipse's major
  axis; those coincide only when the fly is axis-aligned.
- **Renderer is orthographic and analytic** for the fly, perspective for the
  brain. Don't add a mesh rasteriser without a reason — see
  `docs/PORT_PLAN.md#rendering`.

## Sugar (an addition, not upstream)

`world/sugar.py` + `render/sugar_renderer.py` + the `FEEDING` state. Ctrl+
right-click drops sugar; the odour bearing is injected onto the **real**
DNa01/DNa02 and DNp09 neurons (`sim.sugar_l/r/appetite`), and approach comes out
of the network. Feeding on contact is modeled outright — verified that the
circuit has no gustatory pathway (its 16 sensory partners are all
mechanosensory; FlyWire's 334 gustatory neurons make zero synapses onto the
668). Don't describe sugar as connectome-driven without that qualification.

Drops are *interactive* overlays: `Overlay(on_click=...)` plus
`set_input_circle()` shapes the XShape input region down to the bead, so a drop
is clickable (click = wipe it up) while everything around it stays
click-through. The fly's own overlay is never interactive.

Sugar placement uses a **passive button grab** (`platform/taps.ButtonGrab`),
not raw XI2 events. Raw events carry no modifier state, so pairing one with Ctrl
means querying the keyboard afterwards — a race that loses whenever the key is
released quickly, which is exactly how the first version broke. A grab matches
the modifier in the server and consumes the click. Remember the CapsLock/NumLock
combos when registering a grab, or it stops matching with either lock on.

Also note `TapSense` must parse the XI2 raw event **bytes** by hand
(`_raw_button`): python-xlib does not decode raw events, and the original
`getattr(event.data, "detail", 1)` silently returned 1 for every click. Parsers
here return 0 on failure rather than a plausible default, for that reason.

When tuning it, measure the transfer function first — the DNa response has a
threshold and drive below ~0.1 does nothing at all, which is why `PLUME_SIGMA`
is 450 (screen-wide) and `sense()` shapes the bearing non-linearly. All sugar
inputs are zero-guarded, so with no drops the sim is bit-identical to the port.

## Verification tooling

`tools/shot.sh` screenshots the real compositor (`gnome-screenshot`; the
`org.gnome.Shell.Screenshot` D-Bus method is AccessDenied on GNOME 46).
`tools/overlay_check.py` parks a labelled test overlay. `tools/flakiness.py`
re-measures the suite's unseeded flake rates. Headless suites cannot see the
rewritten layers — verify those by screenshot and by querying X directly.
