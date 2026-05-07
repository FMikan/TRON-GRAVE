#!/usr/bin/env python3
"""Frozen-exe entry point — routes to UI or extractor subprocess mode."""

import os
import sys

# Windowed PyInstaller exe has no console; silence stdout/stderr for the UI
# process. In subprocess mode stdout/stderr are pipes so they won't be None.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

if "--_run-extractor" in sys.argv:
    sys.argv.remove("--_run-extractor")
    # Force UTF-8 on the pipes — Windows defaults to cp1252 which can't
    # encode Croatian characters (č, ć, š, đ, ž).
    for _s in (sys.stdout, sys.stderr):
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    from grave_extractor import main as _extractor_main
    sys.exit(_extractor_main())
else:
    from grave_ui import main as _ui_main
    _ui_main()
