#!/bin/bash
# Capture the real compositor output (native Wayland windows included, which
# xwd/x11grab cannot see). The org.gnome.Shell.Screenshot D-Bus method is
# AccessDenied on GNOME 46, but the gnome-screenshot client is permitted.
exec gnome-screenshot -f "${1:-/tmp/shot.png}"
