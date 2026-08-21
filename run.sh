#!/bin/bash
# Launch desktopfly with a clean environment.
# See tools/desnap.sh — snap-packaged editors may export conflicting GTK vars.
cd "$(dirname "$0")"
exec ./tools/desnap.sh python3 -m desktopfly "$@"
