"""An operation of the manifest as data: its fields, their defaults, the parameters of a run.

What a window knows about an operation without it being a matter of looks: the default of a
field, whether a field is in sight, where a list of options comes from, whether a button may be
pressed, and which parameters go to the service. While the window on NiceGUI held these rules
itself, an operation could be run "as in the window" only from the window. Here is one copy of
them - for the console door (`system_core/cli.py`) and for the window that calls the door.

The rules are carried over from that window (`ui_nicegui/app.py` of 2.17) unchanged; that they give
the same parameters for every operation of the manifest is checked by tests/test_cli_door.py against
the record made of that window before it was removed (tests/former_window_parameters.json).
"""
from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

from .manifest import CommandNode, Operation, ToolManifest

CHECKBOX_GROUP_KINDS = {"checkboxes", "multi_checkbox", "multicheckbox", "multi-select", "multiselect"}
CHECKBOX_KINDS = {"checkbox", "bool", "boolean", "toggle"}
NUMBER_KINDS = {"number", "int", "integer", "float"}
LANGUAGES = ("en", "ru")

# The blocks of the WinGet catalogue: a field of this name lists the packages of its block.
PACKAGE_FIELD_SUFFIXES = {
    "packages_system": "system",
    "packages_dev": "dev",
    "packages_ai": "ai",
    "packages_pkms": "pkms",
    "packages_office": "office",
    "packages_media_images": "media_images",
    "packages_media_audio": "media_audio",
    "packages_media_video": "media_video",
    "packages_network": "network",
    "packages_hardware": "hardware",
    "packages_msvc": "msvc",
    "packages_msvc_legacy": "msvc_legacy",
}
# Two operations show the catalogue through the eyes of the machine: what is not installed yet,
# and what has an update. Their lists come from WinGet, not from the manifest.
PACKAGE_LIST_PREFIXES = {
    "install_selected_packages": "missing_install",
    "update_selected_packages": "available_update",
}
WINGET_SERVICE = "system_core.services.winget_service"

# Operations the manifest does not list: the three small buttons of a package card run them for
# one package, and the command line of the terminal runs a command typed by a person. The window
# on NiceGUI made them up on the spot; here they have names.
CARD_OPERATIONS = {
    "package_download": Operation(
        id="package_download",
        title="Download the installer",
        title_ru="Скачать установщик",
        description="Download the installer of one package without running it.",
        description_ru="Скачать установщик одного пакета, не запуская его.",
        service=f"{WINGET_SERVICE}:download_package",
        fields=({"id": "download_package_id", "type": "text", "label": "Package ID", "label_ru": "ID пакета"},),
    ),
    "package_archive": Operation(
        id="package_archive",
        title="Download the portable build",
        title_ru="Скачать портативную сборку",
        description="Download the zip or standalone build of one package.",
        description_ru="Скачать zip или standalone-сборку одного пакета.",
        service=f"{WINGET_SERVICE}:download_package_archive",
        fields=({"id": "download_package_id", "type": "text", "label": "Package ID", "label_ru": "ID пакета"},),
    ),
    "terminal_command": Operation(
        id="terminal_command",
        title="Terminal command",
        title_ru="Команда терминала",
        description="Run a command in the shell chosen, from the folder of the program or from the one named.",
        description_ru="Выполнить команду в выбранной оболочке, из папки программы или из названной.",
        service=f"{WINGET_SERVICE}:terminal_command",
        fields=(
            {"id": "shell", "type": "select", "label": "Shell", "label_ru": "Оболочка", "default": "pwsh",
             "options": [{"value": "pwsh", "label": "PowerShell"}, {"value": "cmd", "label": "CMD"}]},
            {"id": "command", "type": "text", "label": "Command", "label_ru": "Команда", "default": ""},
            {"id": "cwd", "type": "text", "label": "Working folder", "label_ru": "Рабочая папка", "default": ""},
        ),
    ),
}


def field_id(field: dict[str, Any]) -> str:
    return str(field.get("id") or field.get("name") or "").strip()


def raw_kind(field: dict[str, Any]) -> str:
    return str(field.get("type", field.get("kind", "text"))).lower()


def is_checkbox_group(field: dict[str, Any]) -> bool:
    return raw_kind(field) in CHECKBOX_GROUP_KINDS


def safe_language(language: str) -> str:
    return language if language in LANGUAGES else "en"


# False - the defaults of the manifest alone, whatever machine this is (a check against a record made elsewhere).
machine_defaults = True


def machine_default(field: dict[str, Any]) -> Any:
    """What this machine wants a field to be, or None.

    `default_from: "module:function"` names a function that is given the field and reads the
    machine - which devices it has, what kind of case, what its owner keeps in a file of the
    program - and nothing else: it is asked whenever
    the form is described and whenever an operation runs without the field set. A function that
    fails, says nothing, or names what is not among the options of the field leaves the default
    of the manifest.
    """
    source = str(field.get("default_from") or "")
    if not machine_defaults or ":" not in source:
        return None
    try:
        import importlib

        module_name, function_name = source.split(":", 1)
        value = getattr(importlib.import_module(module_name), function_name)(field)
    except Exception:  # noqa: BLE001 - a default must never stand in the way of the form
        return None
    if value in (None, ""):
        return None
    options = field.get("options")
    if isinstance(options, list) and options and value not in [option_value(option) if isinstance(option, dict) else option for option in options]:
        return None
    return value


def field_default(field: dict[str, Any], language: str = "en") -> Any:
    """The value a field has before anyone touched it.

    A default that follows the machine (`default_from`) comes first. `default_ru` / `default_en`
    is a default that follows the language of the interface (the language of a Windows image is
    the natural case).
    """
    wanted = machine_default(field)
    if wanted is not None:
        return wanted
    localized = field.get(f"default_{language}")
    if localized is not None:
        return localized
    if "default" in field:
        return field["default"]
    options = field.get("options", [])
    if is_checkbox_group(field):
        if not isinstance(options, list):
            return []
        return [option.get("value", option.get("id", option.get("label")))
                for option in options if isinstance(option, dict) and option.get("default", False)]
    if isinstance(options, list) and options:
        first = options[0]
        if isinstance(first, dict):
            return first.get("value", first.get("id", ""))
        return first
    return ""


def form_values(node: CommandNode | Operation, values: dict[str, Any], language: str = "en") -> dict[str, Any]:
    """Every field of the operation with its default, overlaid by what was changed.

    An option source that depends on another field must see that field even before anyone
    touched it, otherwise the first list is built for no product.
    """
    result = {field_id(field): field_default(field, language) for field in node.fields if field_id(field)}
    result.update(values)
    return result


def matches(condition: Any, values: dict[str, Any]) -> bool:
    """`{key: value | [values]}`: every named field has one of its values."""
    if not isinstance(condition, dict) or not condition:
        return False
    for key, wanted in condition.items():
        accepted = wanted if isinstance(wanted, list) else [wanted]
        if not any(str(values.get(str(key))) == str(item) for item in accepted):
            return False
    return True


def field_is_visible(field: dict[str, Any], values: dict[str, Any]) -> bool:
    """Whether a field is in sight for these values of the form.

    `hidden: true` keeps a field in the values without drawing it; `hidden_when` hides it while
    every named field matches; `visible_when` hides it until every named field matches.
    """
    if bool(field.get("hidden", False)):
        return False
    if matches(field.get("hidden_when"), values):
        return False
    condition = field.get("visible_when")
    if not isinstance(condition, dict) or not condition:
        return True
    return matches(condition, values)


def node_is_visible(node: CommandNode | Operation, values: dict[str, Any]) -> bool:
    """A child action may carry `visible_when` in its parameters, like a field does."""
    condition = dict(node.parameters or {}).get("visible_when")
    if not isinstance(condition, dict) or not condition:
        return True
    return matches(condition, values)


def gate_key(node: CommandNode | Operation, values: dict[str, Any]) -> str:
    """Why the action may not run right now: a label key, or '' when it may.

    `gate: "module:function"` in the parameters names a function that gets the values of the
    form - the Windows build must not start until the ADK is installed. A gate that broke says
    so instead of opening the way.
    """
    source = str(dict(node.parameters or {}).get("gate") or "").strip()
    if ":" not in source:
        return ""
    try:
        module_name, function_name = source.split(":", 1)
        gate = getattr(importlib.import_module(module_name), function_name)
        return str(gate(dict(values)) or "").strip()
    except Exception as error:  # noqa: BLE001 - a broken gate must not hide the reason
        return f"{error.__class__.__name__}: {error}"


def build_parameters(node: CommandNode | Operation, values: dict[str, Any], source: str | Path,
                     target: str | Path, language: str) -> dict[str, Any]:
    """The parameters of a run: what the manifest sets, the values of the form, the defaults of the
    rest, the two paths of the workbench and the language of the interface."""
    parameters = dict(node.parameters)
    for field in node.fields:
        key = field_id(field)
        if key:
            parameters[key] = values.get(key, field_default(field, language))
    # Whatever the caller gave beyond the fields: the pop-up value of an action without a form
    # (`prompt` in its parameters) goes to the service under the name the manifest gives it.
    for key, value in values.items():
        parameters.setdefault(key, value)
    parameters["input_path"] = str(source)
    parameters["output_path"] = str(target)
    parameters["ui_language"] = language
    return parameters


def prompt_field(node: CommandNode | Operation) -> str:
    """The name of the value an action without a form asks for in a pop-up, or ''."""
    prompt = dict(node.parameters or {}).get("prompt")
    return str(prompt.get("field") or "").strip() if isinstance(prompt, dict) else ""


def unknown_values(node: CommandNode | Operation, values: dict[str, Any]) -> list[str]:
    """The names of values the operation has no field for: a mistyped name must not vanish quietly."""
    known = {field_id(field) for field in node.fields if field_id(field)}
    asked = prompt_field(node)
    if asked:
        known.add(asked)
    return sorted(key for key in values if key not in known)


def short_selections(node: CommandNode | Operation, values: dict[str, Any], language: str = "en") -> list[str]:
    """The groups of checkboxes in sight with fewer ticks than the field asks for (`min_selected`).

    An action that ignores the ticked list (installing a tool, say) declares
    `validate_fields: false` and runs even with nothing selected.
    """
    if dict(node.parameters or {}).get("validate_fields", True) is False:
        return []
    everything = form_values(node, values, language)
    short = []
    for field in node.fields:
        if not is_checkbox_group(field) or not field_is_visible(field, everything):
            continue
        wanted = int(field.get("min_selected", 0) or 0)
        chosen = everything.get(field_id(field))
        if wanted > 0 and (not isinstance(chosen, list) or len(chosen) < wanted):
            short.append(field_id(field))
    return short


def option_value(option: Any) -> Any:
    if isinstance(option, dict):
        return option.get("value", option.get("id", option.get("label", "")))
    return option


def wrong_choices(node: CommandNode | Operation, values: dict[str, Any]) -> list[str]:
    """Values that are none of the options the manifest lists for their field, as "field=value".

    A window cannot give such a value; a command line can. Only a field with its options written
    in the manifest is judged: a list that comes from a provider is not known without asking it.
    """
    wrong = []
    for field in node.fields:
        key = field_id(field)
        options = field.get("options")
        if key not in values or option_source(field, node.id) or not isinstance(options, list) or not options:
            continue
        kind = raw_kind(field)
        if kind not in CHECKBOX_GROUP_KINDS and kind not in {"radio", "select", "tabs"}:
            continue
        known = {str(option_value(option)) for option in options}
        given = values[key] if isinstance(values[key], (list, tuple)) else [values[key]]
        wrong.extend(f"{key}={item}" for item in given if str(item) not in known)
    return wrong


def coerce(field: dict[str, Any], text: str) -> Any:
    """A value of the command line (`--set name=value`) in the shape the field expects."""
    kind = raw_kind(field)
    if kind in CHECKBOX_KINDS:
        lowered = text.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
        raise ValueError("true or false is expected")
    if kind in NUMBER_KINDS:
        number = float(text.replace(",", "."))
        return int(number) if number.is_integer() else number
    if kind in CHECKBOX_GROUP_KINDS:
        return [item.strip() for item in text.split(",") if item.strip()]
    return text


def all_nodes(manifest: ToolManifest):
    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.children)

    yield from walk(manifest.operation_groups)
    yield from manifest.operations
    yield from manifest.maintenance_operations
    yield from CARD_OPERATIONS.values()


def find_node(manifest: ToolManifest, node_id: str, runs: bool = True) -> CommandNode | Operation | None:
    """A node of the manifest by its id; with `runs` - only one that has a service to run.

    A list of options or a gate may be asked of a node that only carries the form (the vendor
    downloads keep their fields on the parent of the buttons).
    """
    for node in all_nodes(manifest):
        if node.id == node_id and (node.service or not runs):
            return node
    return None


def option_source(field: dict[str, Any], operation_id: str = "") -> str:
    """Where the options of a field come from when the manifest does not list them: `module:function`."""
    configured = str(field.get("options_source") or field.get("source") or "").strip()
    if configured:
        return configured
    suffix = PACKAGE_FIELD_SUFFIXES.get(field_id(field))
    prefix = PACKAGE_LIST_PREFIXES.get(operation_id)
    return f"{WINGET_SERVICE}:{prefix}_{suffix}_options" if suffix and prefix else ""


def depends_on(field: dict[str, Any]) -> list[str]:
    value = field.get("depends_on") or []
    return [value] if isinstance(value, str) else [str(item) for item in value]


def resolve_options(source: str, root: Path, values: dict[str, Any]) -> list[Any]:
    """Ask a provider for its list: with the root and the values, with the root, or with nothing - as it takes them.

    How many arguments it takes is read from its signature before it is called, and it is called
    once: what it raises goes out as it is. The window on NiceGUI called it under `except
    (TypeError, ValueError)` and then again with fewer arguments - a provider that failed with
    one of those (a catalogue that answered nonsense is a ValueError) was called twice more, and
    the window was told of a missing argument instead of the provider's own words.
    """
    if ":" not in source:
        raise RuntimeError(f"Dynamic option source must use module:function syntax: {source}")
    module_name, function_name = source.split(":", 1)
    provider = getattr(importlib.import_module(module_name), function_name)
    try:
        count = len(inspect.signature(provider).parameters)
    except (TypeError, ValueError):
        count = 2  # a callable that shows no signature takes the whole of it
    if count >= 2:
        options = provider(root, dict(values))
    elif count == 1:
        options = provider(root)
    else:
        options = provider()
    if not isinstance(options, list):
        raise RuntimeError(f"Dynamic option source returned {type(options).__name__}, expected list.")
    return options


def describe_option(option: Any) -> dict[str, Any]:
    """An option of a choice as an object: the manifest and the providers write it either way."""
    if not isinstance(option, dict):
        return {"value": option, "label": str(option)}
    row = dict(option)
    row["value"] = option_value(option)
    row["label"] = str(option.get("label") or option.get("title") or row["value"])
    return row


def describe_options(field: dict[str, Any], options: list[Any]) -> list[dict[str, Any]]:
    """The options of a field as objects; those of a list of ticks with the looks of their cards -
    the tone, and the small buttons a package card carries."""
    rows = [describe_option(option) for option in options]
    if not is_checkbox_group(field):
        return rows
    from . import package_tones as looks

    key = field_id(field)
    for row in rows:
        if row.get("header"):
            continue
        caption = str(row.get("label_ru") or row.get("label") or "")
        row.setdefault("tone", looks.card_tone(field, key, row["value"], f"{row.get('label', '')} {caption}", PACKAGE_FIELD_SUFFIXES))
        row.update(looks.card_files(key, row["value"]))
    return rows


def describe_field(field: dict[str, Any], operation_id: str) -> dict[str, Any]:
    """A field as the manifest wrote it, plus where its list comes from.

    Nothing is asked of a provider here: the lists of this project are WinGet scans and the
    catalogues of vendors, seconds each. A window asks for a list when it shows the field.
    """
    row = dict(field)
    row["id"] = field_id(field)
    row["kind"] = raw_kind(field)
    # The block of the form the field stands in: the manifest names it `section`.
    row["group"] = str(field.get("section") or "parameters")
    # The window shows the default the door would use: the one this machine wants, when it wants one.
    wanted = machine_default(field)
    if wanted is not None:
        row["default"] = wanted
    if isinstance(field.get("options"), list):
        row["options"] = describe_options(field, field["options"])
    source = option_source(field, operation_id)
    if source:
        row["options_source"] = source
        row["depends_on"] = depends_on(field)
    return row


def describe_node(node: CommandNode | Operation, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """A section or an operation. `raw` is the same node as the manifest wrote it: the keys of
    looks - the sign, the section, the logical block - are read from there, the engine has no use
    for them."""
    raw = raw if isinstance(raw, dict) else {}
    children = tuple(getattr(node, "children", ()))
    raw_children = {str(item.get("id", "")).strip(): item for item in raw.get("children", []) if isinstance(item, dict)}
    looks = {key: raw[key] for key in ("icon", "section", "block", "block_ru", "tone") if key in raw}
    return {
        "id": node.id, "title": node.title, "title_ru": node.title_ru,
        "description": node.description, "description_ru": node.description_ru,
        "tooltip": getattr(node, "tooltip", ""), "tooltip_ru": getattr(node, "tooltip_ru", ""),
        "kind": node.kind, "runs": bool(node.service), "parameters": dict(node.parameters),
        "prompt": prompt_field(node), **looks,
        "fields": [describe_field(field, node.id) for field in node.fields if field_id(field)],
        "children": [describe_node(child, raw_children.get(child.id)) for child in children],
    }


def share_forms(told: dict[str, Any]) -> dict[str, Any]:
    """The description with every form told once: `forms` - a name and its fields - and the nodes naming theirs.

    The form of the downloads stands on fifteen pages, under two buttons on each, and under four
    more: told with every one of them, the answer of `describe` was 3.8 MB and took the window
    four seconds to get (the audit of 10.10.2026). Here a node carries `form` - the name of its
    form, "" when it has no field - in place of `fields`; a form is named by the first node of the
    manifest that has it. A place of the layout names the form of its node and `dropped` - the
    fields its page sets by itself and does not draw. A caller puts them together again:

        fields of a node  = forms[node["form"]]
        fields of a place = those of forms[place["form"]] whose id is not in place["dropped"]
    """
    forms: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}  # the fields of a form as text - its name
    of_node: dict[str, str] = {}  # the id of a node - the name of its form

    def name_of(fields: list[dict[str, Any]], asked_by: str) -> str:
        if not fields:
            return ""
        key = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
        if key not in names:
            name = asked_by
            while name in forms:  # a place whose fields are no form of a node: named apart
                name += "+"
            names[key] = name
            forms[name] = fields
        return names[key]

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            of_node[node["id"]] = node["form"] = name_of(node.pop("fields", []), node["id"])
            walk(node.get("children") or [])

    for part in ("nodes", "maintenance", "card_operations"):
        walk(told.get(part) or [])

    layout = told.get("layout") or {}
    places = [place for section in layout.get("sections") or [] for page in section.get("pages") or [] for place in page.get("operations") or []]
    for place in places + list(layout.get("service") or []) + list(layout.get("folders") or []):
        fields = place.pop("fields", [])
        dropped = set(place.get("dropped") or [])
        whole = forms.get(of_node.get(place["id"], ""), [])
        if not fields:
            place["form"], place["dropped"] = "", []
        elif [field for field in whole if field["id"] not in dropped] == fields:
            place["form"] = of_node[place["id"]]
        else:
            place["form"], place["dropped"] = name_of(fields, place["id"]), []
        place.pop("children", None)
    told["forms"] = forms
    return told


def describe(manifest: ToolManifest) -> dict[str, Any]:
    """The manifest taken apart: what a window draws its sections, operations and fields by."""
    def raw_by_id(key: str) -> dict[str, Any]:
        return {str(item.get("id", "")).strip(): item for item in manifest.raw.get(key, []) if isinstance(item, dict)}

    groups, maintenance = raw_by_id("operation_groups"), raw_by_id("maintenance_operations")
    return {
        "tool": dict(manifest.raw.get("tool", {})),
        "ui": dict(manifest.raw.get("ui", {})),
        "nodes": [describe_node(node, groups.get(node.id)) for node in manifest.operation_groups],
        "maintenance": [describe_node(node, maintenance.get(node.id)) for node in manifest.maintenance_operations],
        "card_operations": [describe_node(node) for node in CARD_OPERATIONS.values()],
    }
