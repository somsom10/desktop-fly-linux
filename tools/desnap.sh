#!/bin/bash
# Run a command with the VS Code snap's environment stripped out.
# The snap sets GTK_PATH/GDK_PIXBUF_MODULE_FILE/XDG_DATA_HOME/LOCPATH etc. to
# its own bundled libs, which segfault or fail symbol lookup against the
# system python3-gi. Anything that touches GTK/GDK must go through this.
unset LD_LIBRARY_PATH GTK_PATH GTK_EXE_PREFIX GDK_PIXBUF_MODULE_FILE \
      GDK_PIXBUF_MODULEDIR GSETTINGS_SCHEMA_DIR GIO_MODULE_DIR LOCPATH \
      GTK_IM_MODULE_FILE XDG_DATA_HOME PYTHONPATH PYTHONHOME
export XDG_DATA_DIRS="/usr/local/share:/usr/share:/var/lib/snapd/desktop"
export XDG_CONFIG_DIRS="${XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG:-/etc/xdg}"
export HOME="${SNAP_REAL_HOME:-$HOME}"
exec "$@"
