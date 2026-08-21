"""Loading of the derived FlyWire v783 data files.

Straight port of ``findDataDir`` / ``loadBrainData`` from upstream's
``Sim.swift``.  The JSON files themselves are used unmodified — they are the
CC BY-NC 4.0 derived data that ships with upstream, and nothing about them is
platform-specific.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

import numpy as np


@dataclass
class BrainPoints:
    """23,210 real neuron soma positions, for the brain window."""

    classes: list[str]
    positions: np.ndarray  # (N, 3) float32
    class_index: np.ndarray  # (N,) int32

    def __len__(self) -> int:
        return len(self.positions)


@dataclass
class Circuit:
    """The 668-neuron simulated sub-circuit and its signed synapse graph."""

    ids: list[str]
    types: list[str]
    roles: list[str]
    sides: list[str]
    positions: np.ndarray  # (n, 3) float32
    edges: np.ndarray  # (E, 3) float32: [pre, post, signed synapse count]
    source: str = ""

    @property
    def n(self) -> int:
        return len(self.roles)


def find_data_dir(explicit: str | None = None) -> str:
    """Locate ``data/`` the way upstream does: next to the program, then cwd.

    The repo's root ``data/`` is a symlink into ``upstream/data/``; the
    ``upstream/data`` candidates below are the fallback for checkouts where
    that symlink did not survive — a ZIP download, a Windows clone, or
    ``git config core.symlinks=false``.  Without them a broken link is fatal.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root = os.path.dirname(here)
    candidates += [
        os.path.join(root, "data"),
        os.path.join(root, "upstream", "data"),
        os.path.join(here, "data"),
        os.path.join(root, "share", "desktopfly", "data"),
        os.path.join(sys.prefix, "share", "desktopfly", "data"),
        os.path.join(os.getcwd(), "data"),
        os.path.join(os.getcwd(), "upstream", "data"),
    ]
    for c in candidates:
        if os.path.exists(os.path.join(c, "circuit.json")):
            return c
    raise FileNotFoundError(
        "no data/ directory containing circuit.json — see README "
        "'Regenerating the data', or clone upstream/ which ships it"
    )


def load_brain_data(data_dir: str | None = None) -> tuple[BrainPoints, Circuit]:
    d = find_data_dir(data_dir)
    with open(os.path.join(d, "brain_points.json")) as f:
        praw = json.load(f)
    with open(os.path.join(d, "circuit.json")) as f:
        craw = json.load(f)

    pts = np.asarray(praw["points"], dtype=np.float32)
    points = BrainPoints(
        classes=list(praw["classes"]),
        positions=np.ascontiguousarray(pts[:, :3]),
        class_index=pts[:, 3].astype(np.int32),
    )

    neurons = craw["neurons"]
    circuit = Circuit(
        ids=[nr["id"] for nr in neurons],
        types=[nr["type"] for nr in neurons],
        roles=[nr["role"] for nr in neurons],
        sides=[nr["side"] for nr in neurons],
        positions=np.asarray([nr["pos"] for nr in neurons], dtype=np.float32),
        edges=np.asarray(craw["edges"], dtype=np.float32),
        source=craw.get("source", ""),
    )
    return points, circuit
