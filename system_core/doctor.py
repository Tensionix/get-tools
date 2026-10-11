from __future__ import annotations

from pathlib import Path
import importlib
import platform
import sys


REQUIRED_MODULES = [
    ("yaml", "pyyaml"),
    ("rich", "rich"),
    ("tqdm", "tqdm"),
]


def check_module(import_name: str) -> tuple[bool, str]:
    try:
        module = importlib.import_module(import_name)
        version = getattr(module, "__version__", "unknown")
        return True, str(version)
    except Exception as exc:
        return False, exc.__class__.__name__


def detect_python_mode(root: Path) -> str:
    if (root / "runtime" / "python.exe").exists():
        return "portable-runtime"
    if (root / "runtime" / "python" / "python.exe").exists():
        return "portable-runtime"
    return "system-python"


def check_manifest_operations(root: Path) -> tuple[bool, list[tuple[str, str, str]]]:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from system_core.core.manifest import CommandNode, load_manifest  # noqa: WPS433
    except Exception as exc:
        return False, [("(loader)", "MANIFEST_FAIL", f"{exc.__class__.__name__}: {exc}")]

    manifest_path = root / "config" / "tool_manifest.yaml"
    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:
        return False, [("(loader)", "MANIFEST_FAIL", f"{exc.__class__.__name__}: {exc}")]

    rows: list[tuple[str, str, str]] = []
    all_ok = True

    def add_service(operation_id: str, service: str) -> None:
        nonlocal all_ok
        if ":" not in service:
            rows.append((operation_id, "BAD_SYNTAX", service))
            all_ok = False
            return

        module_name, function_name = service.split(":", 1)
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            rows.append((operation_id, "IMPORT_FAIL", f"{module_name} ({exc.__class__.__name__})"))
            all_ok = False
            return

        target = getattr(module, function_name, None)
        if target is None:
            rows.append((operation_id, "MISSING_FUNC", f"{module_name}:{function_name}"))
            all_ok = False
            return
        if not callable(target):
            rows.append((operation_id, "NOT_CALLABLE", f"{module_name}:{function_name}"))
            all_ok = False
            return
        rows.append((operation_id, "OK", service))

    def walk_node(node: CommandNode) -> None:
        if node.children:
            for child in node.children:
                walk_node(child)
            return
        add_service(node.id, node.service)

    for operation in [*manifest.operations, *manifest.maintenance_operations]:
        add_service(operation.id, operation.service)
    for group in manifest.operation_groups:
        walk_node(group)

    return all_ok, rows


def check_cmd_encoding(root: Path) -> tuple[bool, list[tuple[str, bool, str]]]:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from system_core.core.cmd_encoding import check_cmd_files  # noqa: WPS433
    except Exception as exc:
        return False, [("(loader)", False, f"{exc.__class__.__name__}: {exc}")]

    rows: list[tuple[str, bool, str]] = []
    all_ok = True
    for result in check_cmd_files(root):
        try:
            relative = str(result.path.resolve().relative_to(root.resolve()))
        except ValueError:
            relative = str(result.path)

        detail = result.summary()
        if result.error:
            detail = f"{detail} {result.error}"
        rows.append((relative, result.ok, detail))
        if not result.ok:
            all_ok = False

    return all_ok, rows


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    print("======================================================================")
    print("AUDION GET TOOLS - DOCTOR")
    print("======================================================================")
    print(f"Project root : {root}")
    print(f"Executable   : {sys.executable}")
    print(f"Python       : {sys.version.split()[0]}")
    print(f"Python mode  : {detect_python_mode(root)}")
    print(f"Platform     : {platform.platform()}")
    print()

    failed = False

    print("[Required modules]")
    for import_name, package_name in REQUIRED_MODULES:
        ok, detail = check_module(import_name)
        status = "OK" if ok else "FAIL"
        print(f"  - {package_name:<12} : {status:<4} {detail}")
        if not ok:
            failed = True

    print()
    print("[Manifest operations]")
    manifest_ok, manifest_rows = check_manifest_operations(root)
    for operation_id, status, detail in manifest_rows:
        print(f"  - {operation_id:<28} : {status:<13} {detail}")
    if not manifest_ok:
        failed = True


    print()
    print("[CMD encoding]")
    cmd_ok, cmd_rows = check_cmd_encoding(root)
    for relative, result_ok, detail in cmd_rows:
        status = "OK" if result_ok else "FAIL"
        print(f"  - {relative:<62} : {status:<4} {detail}")
    if not cmd_ok:
        failed = True

    print()
    if failed:
        print("[RESULT] One or more checks failed.")
        return 1

    print("[RESULT] The environment looks good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
