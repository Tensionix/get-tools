"""Whether this process runs with administrator rights."""
from __future__ import annotations

import ctypes


def is_elevated() -> bool:
    """True when Windows says the process is an administrator's; False when it cannot be asked."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return False
