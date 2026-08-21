#!/bin/bash
# Launch desktopfly with a clean environment.
# See tools/desnap.sh — Claude Code / VS Code snap sessions poison GTK env vars.
cd "$(dirname "$0")"
exec ./tools/desnap.sh python3 -m desktopfly "$@"
