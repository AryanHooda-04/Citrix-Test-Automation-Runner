from __future__ import annotations

import os
import sys
from pathlib import Path


base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
tcl_dir = base_dir / "_tcl_data"
tk_dir = base_dir / "_tk_data"

if not tcl_dir.exists():
    tcl_dir = base_dir / "tcl" / "tcl8.6"
if not tk_dir.exists():
    tk_dir = base_dir / "tcl" / "tk8.6"

if tcl_dir.exists():
    os.environ["TCL_LIBRARY"] = str(tcl_dir)
if tk_dir.exists():
    os.environ["TK_LIBRARY"] = str(tk_dir)
