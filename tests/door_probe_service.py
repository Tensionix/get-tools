"""Services of a made-up project: what tests/test_cli_door.py runs through the console door.

Nothing here touches WinGet, the network or the program folder.
"""
from __future__ import annotations

import time
from typing import Any


def done(context) -> dict[str, Any]:
    context.activity("a live line")
    context.progress(0.5)
    context.log("probe: " + str(context.operation.parameters.get("word", "")))
    return {"word": context.operation.parameters.get("word", "")}


def writes_report(context) -> dict[str, Any]:
    """An operation with something to leave in its report folder."""
    (context.report_dir / "summary.txt").write_text("probe", encoding="utf-8")
    return {"report": str(context.report_dir)}


def coloured(context) -> None:
    """A service writes in colour: a console shows it, a caller by a pipe gets the text alone."""
    context.log("\x1b[33m[WARN] yellow\x1b[0m")
    context.activity("\x1b[32m  ███▒▒  1.0 MB / 2.0 MB\x1b[0m")


def machine_word(field) -> str:
    """A default that follows the machine."""
    return "machine"


def no_such_option(field) -> str:
    return "nothing the field offers"


NONSENSE_CALLS: list[int] = []


def nonsense(root, values) -> list:
    """A list whose provider fails with a ValueError of its own: a catalogue that answered nonsense."""
    NONSENSE_CALLS.append(1)
    raise ValueError("the catalogue answered nonsense (JSON did not parse)")


def broken(context) -> None:
    raise RuntimeError("the probe broke")


def waits_for_stop(context) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if context.cancelled():
            raise RuntimeError("Cancelled: the probe was stopped")
        time.sleep(0.05)
    raise RuntimeError("nobody stopped the probe")


def breaks_beside_a_stop_file(context) -> None:
    """The stop file lies there, and something else breaks: a failure, not a stop."""
    raise RuntimeError("the disk is full")


def takes_secret(context) -> dict[str, Any]:
    return {"length": len(str(context.operation.parameters.get("probe_token", "")))}


def versions(root, values) -> list[dict[str, str]]:
    product = str(values.get("product", ""))
    return [{"value": f"{product}-1", "label": f"{product} 1"}, {"value": f"{product}-2", "label": f"{product} 2"}]


def reads_source(context) -> dict[str, Any]:
    """An import: it reads the file of its name from the source it was given."""
    from pathlib import Path

    file = Path(str(context.operation.parameters.get("input_path") or "")) / "winget-export.json"
    return {"read": file.read_text(encoding="utf-8"), "target": str(context.operation.parameters.get("output_path") or "")}


def lying_in_source(root, values) -> list[dict[str, str]]:
    """A list of what lies in the source: one card a folder."""
    from pathlib import Path

    source = Path(str(values.get("input_path") or ""))
    return [{"value": f"input:{item.name}", "label": item.name} for item in sorted(source.iterdir()) if item.is_dir()]


def gate_by_folders(values) -> str:
    """Closed until the source holds something and a target is named."""
    from pathlib import Path

    source = Path(str(values.get("input_path") or ""))
    return "" if source.is_dir() and any(source.iterdir()) and values.get("output_path") else "probe_source_empty"


def gate(values) -> str:
    return "" if values.get("product") == "open" else "probe_gate_closed"
