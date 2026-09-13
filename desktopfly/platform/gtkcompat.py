"""Single place where the GI versions get pinned.

``gi.require_version`` must run before anything imports ``gi.repository.Gtk``,
otherwise gi silently picks the newest typelib on the system (GTK 4 here) and
the next ``require_version("Gtk", "3.0")`` raises.  Importing Gtk/Gdk/GLib from
this module instead of from ``gi.repository`` makes the ordering impossible to
get wrong.
"""

from __future__ import annotations

import os

# The overlay relies on X11 specifics (override-redirect, XShape input regions)
# that have no Wayland equivalent for an unprivileged client, so pin XWayland.
# This is an assignment, not setdefault: several Wayland sessions already export
# GDK_BACKEND (Hyprland/Omarchy ships "wayland,x11,*"), and setdefault would
# leave that in place -- GTK then picks the Wayland backend and the overlay
# dies in _on_map with "'GdkWaylandWindow' object has no attribute 'get_xid'".
os.environ["GDK_BACKEND"] = "x11"

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")

from gi.repository import Gdk, GdkPixbuf, GdkX11, Gio, GLib, Gtk  # noqa: E402,F401

__all__ = ["Gtk", "Gdk", "GdkX11", "GdkPixbuf", "Gio", "GLib"]
