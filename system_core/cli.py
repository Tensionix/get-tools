"""The console door of Audion Get Tools.

A button of the window and the command line run the same thing: an operation of the manifest, by
the same runner (`execute_operation`), with the same defaults of its fields. Before the door an
operation could be run "as in the window" only from the window - the window called the service
itself.

  python system_core/cli.py describe
  python system_core/cli.py run --operation preview_available_updates
  python system_core/cli.py run --operation install_package_by_id --set package_id=7zip.7zip
  python system_core/cli.py options --operation vendor_downloads --field vendor_versions --set vendor=nvidia
  python system_core/cli.py gate --operation vendor_download --set vendor=uupdump
  python system_core/cli.py link --package 7zip.7zip
  python system_core/cli.py version

The output of `run`: a plain line is a line of the operation's log; a line that starts with
`::get::` is a service line and carries JSON - the start, the share of the work done, the one live
line under the log (`activity`), the end. The outcome goes to the file of `--result`. A console
gets the lines in the colours the services write them in; a caller that reads them by a pipe gets
the text without the colour codes, or with them when it asks (`--colours`) - the window does, and
shows them. The live line never carries them. The live line of a download is one for every service
(`core/live_line.py`): `[DOWNLOAD] name: 88% · 132.0 / 148.9 MB · 8.6 MB/s`.

`describe`, `options`, `gate` and `link` answer with one line of JSON. `describe` asks no provider
for its list: the lists of this program are WinGet scans and the catalogues of vendors, seconds
each - `options` asks for one list. It tells every form once: `forms` is a name and its fields, a
node carries `form`, and a place of the layout `form` and `dropped` - the fields its page sets by
itself (`operation_model.share_forms` says how to put them together).

Outcomes and exit codes: `done` 0; `failed` 1; `stopped` 130; a wrong call - 2, and nothing is run.

Paths: a relative `--source` or `--target` is counted from the project folder, as the window
shows them; a relative `--params`, `--result` or `--cancel-file` - from the folder of the caller.
`options` and `gate` take the two folders as `run` does: a list that shows what lies in the source
looks where the run will read.

Stopping: the caller creates the file of `--cancel-file`. The operation sees it at its next safe
point and stops by itself.

A secret (the GitHub token) is never an argument: `--secret-stdin NAME` reads it from the standard
input, one line, and the log shows it replaced.
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system_core.core import operation_model as model  # noqa: E402
from system_core.core import gate_words, window_layout  # noqa: E402
from system_core.core.ansi import strip_ansi  # noqa: E402
from system_core.core.jobs import execute_operation  # noqa: E402
from system_core.core.manifest import Operation, load_manifest  # noqa: E402
from system_core.core.paths import ensure_project_dirs, get_project_paths  # noqa: E402

CONTROL = "::get::"
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_STOPPED = 0, 1, 2, 130

# Nobody reads the output any more - the caller closed its end of the pipe, or Windows is going
# down. The operation goes on without it: its own log file is written all the same, and an install
# ended in the middle would leave more behind than one that finishes unseen. That is a caller that
# stopped listening. The window is another matter: it keeps this process in its job, and a window
# that fell takes the operation with it; what has to outlive the window - the consoles of an image
# build, WinUtil - leaves that job when it is started (`external_tools_service.start_on_its_own`).
_nobody_listens = False


def say(line: str) -> None:
    global _nobody_listens
    if _nobody_listens:
        return
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        _nobody_listens = True


def control(payload: dict[str, Any]) -> None:
    say(CONTROL + json.dumps(payload, ensure_ascii=False, default=str))


# `run --colours`: the caller paints the colour codes of the services itself.
_colours = False


def tell(line: str) -> None:
    """
    A line of the operation's log. A console shows the colours a service writes its lines in. A
    caller that reads the output by a pipe would get them as escape codes in the middle of its
    text, so it gets the text alone - unless it asked for the codes (`--colours`) to paint them.
    """
    say(str(line) if _colours or sys.stdout.isatty() else strip_ansi(str(line)))


def usage_error(message: str) -> int:
    try:
        print("[ERR] " + message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass
    return EXIT_USAGE


def project(root: str | None):
    paths = get_project_paths(Path(root).resolve() if root else ROOT)
    manifest = paths.config / "tool_manifest.yaml"
    if not manifest.is_file():
        raise ValueError(f"not a project folder, no manifest: {manifest}")
    try:
        return paths, load_manifest(manifest)
    except Exception as error:  # noqa: BLE001 - whatever the manifest stumbles over, nothing was run
        raise ValueError(f"the manifest does not read: {manifest}: {error}") from error


def project_version(paths) -> str:
    try:
        return str(json.loads((paths.config / "version.json").read_text(encoding="utf-8")).get("version", ""))
    except (OSError, ValueError):
        return ""


def language_of(args: argparse.Namespace, paths) -> str:
    """The language named by the caller; without it - the one chosen in the window, English when none was."""
    if args.language:
        return model.safe_language(args.language)
    try:
        # The window keeps its settings in config\window_settings.json; "Lang" is "" until a language button was pressed.
        chosen = json.loads((paths.config / "window_settings.json").read_text(encoding="utf-8-sig")).get("Lang", "")
    except (OSError, ValueError, AttributeError):  # settings that do not read are no reason to refuse a run
        chosen = ""
    return model.safe_language(str(chosen))


def command_describe(args: argparse.Namespace) -> int:
    paths, manifest = project(args.root)
    payload = model.describe(manifest)
    payload["version"] = project_version(paths)
    payload["language"] = language_of(args, paths)
    payload["layout"] = window_layout.describe(window_layout.load(paths.config), manifest)
    payload["paths"] = {"root": str(paths.root), "input": str(paths.input), "output": str(paths.output),
                        "logs": str(paths.logs), "report": str(paths.report), "config": str(paths.config),
                        "workspace": str(paths.workspace)}
    # Every form once: the same fields stand under dozens of buttons (operation_model.share_forms).
    say(json.dumps(model.share_forms(payload), ensure_ascii=False, default=str))
    return EXIT_OK


def read_values(args: argparse.Namespace, node) -> dict[str, Any]:
    """The values of the fields: the file of `--params`, over it every `--set name=value`."""
    values: dict[str, Any] = {}
    if args.params:
        loaded = json.loads(Path(args.params).read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError("--params: a JSON object \"field name: value\" is expected")
        values.update(loaded)
    fields = {model.field_id(field): field for field in node.fields if model.field_id(field)}
    for item in args.set or []:
        name, separator, text = item.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"--set: name=value is expected, got {item!r}")
        if name not in fields:
            raise ValueError(f"--set: the operation has no field {name!r}")
        try:
            values[name] = model.coerce(fields[name], text)
        except ValueError as error:
            raise ValueError(f"--set {name}: {error}") from error
    return values


def write_result(path: str, payload: dict[str, Any]) -> str:
    """The outcome into its file, whole or not at all; the reason when it could not be written."""
    if not path:
        return ""
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        draft = target.with_name(target.name + ".tmp")
        draft.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        draft.replace(target)
        return ""
    except OSError as error:
        return str(error)


def workbench_path(text: str, fallback: Path, root: Path) -> Path:
    """Source or Target as named by the caller; a relative one is counted from the project folder.

    A variable of the environment is read here, before the path is tied to the project: left for
    the service, `%TEMP%\\x` became `<project>\\C:\\...\\Temp\\x`.
    """
    if not text:
        return fallback
    cleaned = text.strip().strip('"').strip()
    if '"' in cleaned:
        # `"E:\My Files\" --target D:\Out` reaches a program as one argument with a quote
        # inside: the backslash before the closing quote glued the next key to the path.
        raise ValueError(f"a quotation mark inside a path - was the path quoted with a backslash at its end? {text!r}")
    path = Path(os.path.expandvars(cleaned)).expanduser()
    return path if path.is_absolute() else root / path


def workbench_values(args: argparse.Namespace, paths) -> dict[str, str]:
    """Source and Target as a list or a gate reads them: under the names a service gets them by (`build_parameters`).

    A list that shows what lies in the source - driver packages for the image - must look where
    the run will read; without these it always looked into `input` of the program.
    """
    return {"input_path": str(workbench_path(args.source, paths.input, paths.root)),
            "output_path": str(workbench_path(args.target, paths.output, paths.root))}


def own_file(text: str) -> str:
    """A file of the caller - the values, the outcome, the stop signal - as a full path, counted from where it called."""
    return str(Path(text).expanduser().resolve()) if text else ""


def with_parameters(node, parameters: dict[str, Any]) -> Operation:
    return node.to_operation(parameters) if hasattr(node, "to_operation") else dataclasses.replace(node, parameters=parameters)


def command_run(args: argparse.Namespace) -> int:
    global _colours
    _colours = bool(args.colours)
    paths, manifest = project(args.root)
    node = model.find_node(manifest, args.operation)
    if node is None:
        return usage_error(f"the manifest has no operation {args.operation!r}")
    language = language_of(args, paths)
    # Before the door moves to the project folder: these three are counted from the caller's.
    args.params, args.result, args.cancel_file = own_file(args.params), own_file(args.result), own_file(args.cancel_file)
    try:
        values = read_values(args, node)
        source = workbench_path(args.source, paths.input, paths.root)
        target = workbench_path(args.target, paths.output, paths.root)
    except (OSError, ValueError) as error:
        return usage_error(str(error))
    if args.secret_stdin:
        if args.secret_stdin != model.prompt_field(node):
            return usage_error(f"the operation asks for no value named {args.secret_stdin!r}")
        values[args.secret_stdin] = sys.stdin.readline().rstrip("\r\n")
    unknown = model.unknown_values(node, values)
    if unknown:
        return usage_error("the operation has no fields: " + ", ".join(unknown))
    short = model.short_selections(node, values, language)
    if short:
        return usage_error("fewer ticks than the field asks for: " + ", ".join(short))
    wrong = model.wrong_choices(node, values)
    if wrong:
        return usage_error("not one of the options of the field: " + ", ".join(wrong))
    # The gate sees the two folders too, as `gate` gives them: one answer for the question and for the run.
    blocked = model.gate_key(node, model.form_values(node, values, language) | {"input_path": str(source), "output_path": str(target)})
    if blocked:
        return usage_error(f"the operation may not run now: {gate_text(blocked, language)}")

    ensure_project_dirs(paths)
    # Where the services have always been run from: the program folder.
    os.chdir(ROOT)
    cancel_file = Path(args.cancel_file) if args.cancel_file else None

    operation = with_parameters(node, model.build_parameters(node, values, source, target, language))
    run_paths = dataclasses.replace(paths, input=source, output=target)

    control({"started": {"operation": operation.id, "source": str(source), "target": str(target)}})
    began = time.monotonic()
    result = execute_operation(run_paths, operation, tell,
                               lambda value: control({"progress": round(float(value), 4)}),
                               (lambda: cancel_file.exists()) if cancel_file is not None else None,
                               lambda text: control({"activity": strip_ansi(str(text))}))
    # A stop is what the runner says it is: the service asked whether to stop, was told "yes" and
    # left. A stop signal that lies there while something else broke does not make a failure a stop.
    stopped = bool(result.data.get("cancelled"))
    outcome = "stopped" if stopped else "done" if result.ok else "failed"
    seconds = round(time.monotonic() - began, 1)
    trouble = write_result(args.result, {"operation": operation.id, "outcome": outcome, "ok": result.ok,
                                         "message": result.message, "data": result.data,
                                         "source": str(source), "target": str(target), "seconds": seconds})
    if trouble:
        say(f"[WARN] The outcome could not be written to {args.result}: {trouble}")
    control({"finished": {"outcome": outcome, "message": result.message, "seconds": seconds}})
    return EXIT_STOPPED if stopped else EXIT_OK if result.ok else EXIT_FAILED


def form_node(args: argparse.Namespace, manifest):
    """The node a question is about: it need not run anything - a form may stand on the parent of its buttons."""
    node = model.find_node(manifest, args.operation, runs=False)
    if node is None:
        raise ValueError(f"the manifest has no node {args.operation!r}")
    return node


def command_options(args: argparse.Namespace) -> int:
    paths, manifest = project(args.root)
    node = form_node(args, manifest)
    field = next((item for item in node.fields if model.field_id(item) == args.field), None)
    if field is None:
        return usage_error(f"the node {node.id!r} has no field {args.field!r}")
    args.params = own_file(args.params)
    try:
        values = model.form_values(node, read_values(args, node), language_of(args, paths)) | workbench_values(args, paths)
    except (OSError, ValueError) as error:
        return usage_error(str(error))
    source = model.option_source(field, node.id)
    if not source:
        options = field.get("options", [])
        listed = model.describe_options(field, options) if isinstance(options, list) else []
        say(json.dumps({"field": args.field, "source": "", "options": listed}, ensure_ascii=False, default=str))
        return EXIT_OK
    os.chdir(ROOT)
    try:
        options = model.resolve_options(source, paths.root, values)
    except Exception as error:  # noqa: BLE001 - the reason is the answer: a window shows it in place of the list
        say(json.dumps({"field": args.field, "source": source, "options": [],
                        "error": f"{error.__class__.__name__}: {error}"}, ensure_ascii=False))
        return EXIT_FAILED
    say(json.dumps({"field": args.field, "source": source, "options": model.describe_options(field, options)},
                   ensure_ascii=False, default=str))
    return EXIT_OK


def command_pin(args: argparse.Namespace) -> int:
    """Pin an option of a list to its head, or take the pin off: the function the field names in `pins` does it."""
    _, manifest = project(args.root)
    node = form_node(args, manifest)
    field = next((item for item in node.fields if model.field_id(item) == args.field), None)
    if field is None:
        return usage_error(f"the node {node.id!r} has no field {args.field!r}")
    source = str(field.get("pins") or "").strip()
    if ":" not in source:
        return usage_error(f"the field {args.field!r} has no pins")
    os.chdir(ROOT)
    told = {"field": args.field, "value": args.value, "pinned": not args.off}
    try:
        module_name, function_name = source.split(":", 1)
        getattr(importlib.import_module(module_name), function_name)(model.field_id(field), args.value, not args.off)
    except Exception as error:  # noqa: BLE001 - the reason is the answer
        say(json.dumps(told | {"ok": False, "error": f"{error.__class__.__name__}: {error}"}, ensure_ascii=False))
        return EXIT_FAILED
    say(json.dumps(told | {"ok": True}, ensure_ascii=False))
    return EXIT_OK


def gate_text(key: str, language: str) -> str:
    """The words of a gate, in the language of the caller (`core/gate_words.py`); a key with no words stays a key."""
    return gate_words.gate_text(key, language)


def command_gate(args: argparse.Namespace) -> int:
    paths, manifest = project(args.root)
    node = form_node(args, manifest)
    args.params = own_file(args.params)
    language = language_of(args, paths)
    try:
        values = model.form_values(node, read_values(args, node), language) | workbench_values(args, paths)
    except (OSError, ValueError) as error:
        return usage_error(str(error))
    os.chdir(ROOT)
    key = model.gate_key(node, values)
    say(json.dumps({"operation": node.id, "blocked": bool(key), "key": key,
                    "text": gate_text(key, language) if key else ""}, ensure_ascii=False))
    return EXIT_OK


def command_link(args: argparse.Namespace) -> int:
    project(args.root)
    os.chdir(ROOT)
    from system_core.services.winget_service import package_page_link

    try:
        link = package_page_link(args.package) or {}
    except Exception as error:  # noqa: BLE001 - the page was not found: an answer, not a crash
        say(json.dumps({"package": args.package, "url": "", "error": f"{error.__class__.__name__}: {error}"}, ensure_ascii=False))
        return EXIT_FAILED
    say(json.dumps({"package": args.package, **{key: value for key, value in dict(link).items()}},
                   ensure_ascii=False, default=str))
    return EXIT_OK if dict(link).get("url") else EXIT_FAILED


def command_version(args: argparse.Namespace) -> int:
    paths, _manifest = project(args.root)
    say(project_version(paths))
    return EXIT_OK


def add_workbench_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source", default="", help="Source: where the operation reads from; input by default")
    parser.add_argument("--target", default="", help="Target: where downloads go; output by default")


def add_form_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--params", default="", help="A JSON file with the values of the fields by their names")
    parser.add_argument("--set", action="append", metavar="NAME=VALUE", help="The value of one field; may be repeated")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="get_cli", description="Audion Get Tools: the console door.")
    parser.add_argument("--root", default="", help="The project folder, when it is not the one the door lies in")
    parser.add_argument("--language", default="", choices=["", "en", "ru"],
                        help="The language of the interface; by default the one the window was left in")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("describe", help="The manifest as JSON: sections, operations, fields").set_defaults(func=command_describe)

    run = commands.add_parser("run", help="Run an operation of the manifest")
    run.add_argument("--operation", required=True, help="The id of the operation")
    add_workbench_arguments(run)
    add_form_arguments(run)
    run.add_argument("--secret-stdin", default="", metavar="NAME",
                     help="Read the value the operation asks for in a pop-up from the standard input")
    run.add_argument("--result", default="", help="Where to write the outcome of the operation (JSON)")
    run.add_argument("--cancel-file", default="", help="The stop signal: once the file appears, the operation stops")
    run.add_argument("--colours", action="store_true",
                     help="Leave the colour codes of the services in the lines: for a caller that paints them")
    run.set_defaults(func=command_run)

    options = commands.add_parser("options", help="The list of options of one field, asked of its provider")
    options.add_argument("--operation", required=True, help="The id of the node that carries the field")
    options.add_argument("--field", required=True, help="The id of the field")
    add_workbench_arguments(options)
    add_form_arguments(options)
    options.set_defaults(func=command_options)

    pin = commands.add_parser("pin", help="Pin an option of a list to its head, or take the pin off")
    pin.add_argument("--operation", required=True, help="The id of the node that carries the field")
    pin.add_argument("--field", required=True, help="The id of the field")
    pin.add_argument("--value", required=True, help="The value of the option")
    pin.add_argument("--off", action="store_true", help="Take the pin off")
    pin.set_defaults(func=command_pin)

    gate = commands.add_parser("gate", help="Whether an action may run now, and why not")
    gate.add_argument("--operation", required=True, help="The id of the action")
    add_workbench_arguments(gate)
    add_form_arguments(gate)
    gate.set_defaults(func=command_gate)

    link = commands.add_parser("link", help="The download page of a package")
    link.add_argument("--package", required=True, help="The WinGet id of the package")
    link.set_defaults(func=command_link)

    commands.add_parser("version", help="The version number").set_defaults(func=command_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code not in (0, None) else EXIT_OK
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.func(args))
    except (OSError, ValueError) as error:
        # A project that is not one - no manifest, a manifest that does not read: nothing was
        # run, and that is a wrong call, not an operation that failed.
        return usage_error(f"{error.__class__.__name__}: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
