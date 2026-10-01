"""Start PiDeck on localhost.

Run from the repository root:

    python -m backend
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> None:
    import uvicorn

    from backend.config import get_settings

    settings = get_settings()
    settings.validate_bind()
    uvicorn.run(
        "backend.app:app",
        host=settings.pideck_host,
        port=settings.pideck_port,
        reload=settings.pideck_dev,
        reload_dirs=[str(_ROOT / "backend")] if settings.pideck_dev else None,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1,localhost",
        log_level="info",
    )


if __name__ == "__main__":
    main()
