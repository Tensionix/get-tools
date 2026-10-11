"""A service that fetches one link: what tests/test_audit_2_17.py runs to see a download end short.

It calls the download every service of the program uses (`system_core/services/downloads.py`), the
way a service calls it - with the context of a run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from system_core.services.downloads import download_file_to


def fetch(context) -> dict[str, Any]:
    parameters = context.operation.parameters
    asset = download_file_to(context, str(parameters["url"]), Path(str(parameters["target"])), "fixture")
    return {"path": str(asset.path), "size": asset.size, "sha256": asset.sha256}
