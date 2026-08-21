# Contributing to desktopfly (Linux)

Thanks for helping with the Linux port of
[`DenisSergeevitch/desktop-fly`](https://github.com/DenisSergeevitch/desktop-fly).
The macOS original is kept verbatim in `upstream/` and should not be edited: it
is the reference used to check behavioral parity. Its `CLAUDE.md` documents the
original project's invariants and tuning details, which still apply because
`simcore/` and `body/` are direct translations of that code.

## Development provenance

This port was created with Claude Code. Claude produced much of the
implementation and documentation under the repository owner's direction. The
original source, porting notes, deterministic tests, and commit boundaries are
kept in the repository so the implementation and the claims made about it can
be reviewed independently.

## Run and verify

```sh
./run.sh                    # the app
./run.sh --simtest          # circuit invariants
./run.sh --behaviortest     # 21 sim-to-body checks
./run.sh --list-monitors
```

Run both suites after changing simulation or behavior code. They are seeded by
default, so failures are reproducible; see `docs/PARITY.md`.

## Snap-packaged editor terminals

Some snap-packaged editors export `GTK_PATH`, `GDK_PIXBUF_MODULE_FILE`,
`XDG_DATA_HOME`, and related variables pointing at their bundled GTK. A system
PyGObject process launched from such a shell can fail with
`symbol lookup error: ... __libc_pthread_init`. Run the app through `run.sh`,
which invokes `tools/desnap.sh` to remove those conflicting variables.

Also, `gi.require_version` must run before any `gi.repository` import, or GI may
select GTK 4 before the code requests GTK 3. Import Gtk/Gdk/GLib from
`desktopfly.platform.gtkcompat`, not directly from `gi.repository`.

## File map

| Linux port | macOS upstream |
|---|---|
| `simcore/lif.py`, `data.py` | `Sim.swift` |
| `simcore/signals.py` | `SignalBuilder` in `main.swift` |
| `body/fly.py`, `body/model.py` | `FlyModel.swift` |
| `render/*` | `SceneKit` scene + `BrainView.swift` |
| `platform/environment.py` | `Environment.swift` |
| `platform/overlay.py`, `tray.py`, `taps.py` | `AppDelegate` in `main.swift` |
| `app.py` | `Coordinator` + `AppDelegate` |
| `tests/*` | `runSimtest()`, `runBehaviorTest()` |

## Port-specific constraints

- Set the overlay's input shape after `map-event`. GDK rewrites it while
  mapping, which otherwise makes the overlay swallow clicks.
- The renderer deliberately avoids the GI/cairo bridge. Draw into a pycairo
  `ImageSurface`, blit through `GdkPixbuf`, and shape through raw XShape.
- The simulation runs on the GLib main loop rather than a render thread, so the
  upstream lock and `enqueue` machinery is not needed unless the architecture
  becomes multithreaded.
- Abdomen bands follow the body Y axis, not the projected ellipse's major axis.
- The fly renderer is analytic and orthographic; the brain renderer uses
  perspective. See `docs/PORT_PLAN.md#rendering`.

## Sugar

Sugar is an addition to upstream. Its odour bearing injects current onto the
real DNa01/DNa02 and DNp09 neurons (`sim.sugar_l/r/appetite`), so approach comes
from the network. Feeding on contact is modeled separately: the 668-neuron
circuit has no gustatory pathway. Preserve that distinction when documenting
or changing the feature.

Sugar placement uses a passive X button grab because raw XI2 button events do
not include reliable modifier state. Remember the CapsLock and NumLock variants
when registering the grab. `TapSense` parses XI2 raw-event bytes directly;
python-xlib does not decode those events.

With no sugar present, all sugar inputs are zero-guarded so the simulation stays
bit-identical to the faithful port.

## Verification tooling

`tools/shot.sh` captures the live compositor. `tools/overlay_check.py` creates a
small test overlay, and `tools/flakiness.py` measures the unseeded behavior-test
failure rate. The headless suites cannot verify compositor behavior, so changes
to overlays, input shapes, terrain sensing, or rendering also need live desktop
testing.
