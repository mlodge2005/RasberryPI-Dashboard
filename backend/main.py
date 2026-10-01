"""Uvicorn entry point.

From the repository root:

    uvicorn backend.main:app --host 127.0.0.1 --port 8080

The supported production command is `python -m backend`, which refuses a public bind.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.app import create_app

app = create_app()
