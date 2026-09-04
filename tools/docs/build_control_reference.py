"""Build the control reference of the user guides straight from config\\tool_manifest.yaml.

Every window, action, field, option, preset and tooltip the GUI shows comes from the
manifest, so the reference is generated rather than written: run this script after a
manifest change and the guides follow. The text lands between the markers

    <!-- controls:start -->  ...  <!-- controls:end -->

in docs\\USER_GUIDE_RU.md and docs\\USER_GUIDE_EN.md (created at the end of the file
when missing). Run: runtime\\python.exe tools\\docs\\build_control_reference.py
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config" / "tool_manifest.yaml"
GUIDES = {"ru": ROOT / "docs" / "USER_GUIDE_RU.md", "en": ROOT / "docs" / "USER_GUIDE_EN.md"}
START, END = "<!-- controls:start -->", "<!-- controls:end -->"

TEXT = {
    "ru": {
        "title": "## Справочник: каждое окно, каждый контрол, каждая подсказка",
        "intro": (
            "Этот раздел собран из `config\\tool_manifest.yaml` скриптом `tools\\docs\\build_control_reference.py`, "
            "поэтому подписи и подсказки здесь те же, что в окне, буква в букву. Порядок окон как в списке «Операции». "
            "У каждого контрола: тип, значение по умолчанию, подсказка (то, что всплывает при наведении) и, где есть, "
            "варианты выбора и условие показа."
        ),
        "kind": {"safe": "безопасное действие, подтверждения не просит", "dangerous": "меняет систему, перед запуском спрашивает подтверждение"},
        "actions": "Кнопки действий",
        "fields": "Поля",
        "type": {
            "radio": "ряд переключателей, один вариант", "tabs": "вкладки вверху формы", "checkboxes": "карточки с галочками, любой набор",
            "checkbox": "галочка", "profile_buttons": "ряд кнопок-пресетов: нажатие заполняет поля ниже", "toggle_buttons": "кнопки-переключатели групп",
            "text": "строка ввода", "textarea": "многострочный текст", "select": "выпадающий список", "number": "число",
            "markdown": "редактор текста", "path": "путь", "file": "файл", "folder": "папка",
        },
        "default": "По умолчанию", "tooltip": "Подсказка", "hint": "Пояснение под полем", "options": "Варианты", "presets": "Кнопки",
        "visible": "Показывается, когда", "hidden": "Скрытое поле: значение уходит в операцию, на экране его ведёт другой контрол.",
        "dynamic": "Список строится на лету: {source}", "min_selected": "Нужно отметить хотя бы {n}",
        "no_fields": "Полей нет: одна кнопка.", "empty": "пусто", "yes": "да", "no": "нет", "all": "все",
        "maintenance": "## Служебные процедуры", "maintenance_intro": "Кнопки из раздела «Обслуживание». Каждая запускается сразу, без формы.",
        "sets_note": "Список пакетов", "count": "{n} шт.", "sets": "наборы", "or": "или",
    },
    "en": {
        "title": "## Reference: every window, every control, every tooltip",
        "intro": (
            "This section is generated from `config\\tool_manifest.yaml` by `tools\\docs\\build_control_reference.py`, "
            "so the captions and tooltips here are the window's own, letter for letter. Windows follow the order of the "
            "Operations list. Every control shows its type, default, tooltip (what pops up on hover) and, where present, "
            "its options and the condition that shows it."
        ),
        "kind": {"safe": "safe action, no confirmation asked", "dangerous": "changes the system, asks for confirmation before it runs"},
        "actions": "Action buttons",
        "fields": "Fields",
        "type": {
            "radio": "a row of switches, one choice", "tabs": "tabs at the top of the form", "checkboxes": "cards with checkboxes, any set",
            "checkbox": "checkbox", "profile_buttons": "a row of preset buttons: a press fills the fields below", "toggle_buttons": "group toggle buttons",
            "text": "text box", "textarea": "multi-line text", "select": "drop-down list", "number": "number",
            "markdown": "text editor", "path": "path", "file": "file", "folder": "folder",
        },
        "default": "Default", "tooltip": "Tooltip", "hint": "Note under the field", "options": "Options", "presets": "Buttons",
        "visible": "Shown when", "hidden": "Hidden field: its value goes to the operation, another control drives it on screen.",
        "dynamic": "The list is built on the fly: {source}", "min_selected": "At least {n} must be ticked",
        "no_fields": "No fields: a single button.", "empty": "empty", "yes": "yes", "no": "no", "all": "all",
        "maintenance": "## Service procedures", "maintenance_intro": "Buttons of the Maintenance section. Each runs at once, without a form.",
        "sets_note": "Package list", "count": "{n} items", "sets": "sets", "or": "or",
    },
}


def lang_text(item: dict, key: str, lang: str) -> str:
    if lang == "ru":
        return str(item.get(f"{key}_ru") or item.get(key) or "").strip()
    return str(item.get(key) or "").strip()


def option_value(option) -> str:
    if isinstance(option, dict):
        return str(option.get("value", option.get("id", "")))
    return str(option)


def option_label(option, lang: str) -> str:
    if isinstance(option, dict):
        return lang_text(option, "label", lang) or option_value(option)
    return str(option)


def fmt_default(field: dict, lang: str, t: dict) -> str:
    kind = str(field.get("type", "text")).lower()
    value = field.get(f"default_{lang}", field.get("default"))
    options = field.get("options") or []
    if kind == "checkboxes":
        chosen = list(value) if isinstance(value, list) else [option_value(o) for o in options if isinstance(o, dict) and o.get("default")]
        if not chosen:
            return t["empty"]
        labels = {option_value(o): option_label(o, lang) for o in options if isinstance(o, dict)}
        names = [labels.get(str(v), str(v)) for v in chosen]
        return ", ".join(names) if len(names) <= 12 else f"{len(names)}: " + ", ".join(names[:12]) + " ..."
    if kind in {"checkbox", "bool"}:
        return t["yes"] if bool(value) else t["no"]
    if value in (None, ""):
        if options and isinstance(options[0], dict) and kind in {"radio", "tabs", "select"}:
            return option_label(options[0], lang)
        return t["empty"]
    if options:
        for o in options:
            if option_value(o) == str(value):
                return option_label(o, lang)
    return f"`{value}`"


def visible_when(field: dict, fields_by_id: dict, lang: str) -> str:
    cond = field.get("visible_when")
    if not isinstance(cond, dict) or not cond:
        return ""
    parts = []
    for key, wanted in cond.items():
        ref = fields_by_id.get(str(key), {})
        name = lang_text(ref, "label", lang) or str(key)
        wanted_list = wanted if isinstance(wanted, list) else [wanted]
        labels = []
        for w in wanted_list:
            label = next((option_label(o, lang) for o in (ref.get("options") or []) if option_value(o) == str(w)), str(w))
            labels.append(f"«{label}»" if lang == "ru" else f"'{label}'")
        parts.append(f"{name} = " + (" / ".join(labels)))
    return "; ".join(parts)


def render_field(field: dict, fields_by_id: dict, lang: str, t: dict, out: list[str]) -> None:
    kind = str(field.get("type", "text")).lower()
    label = lang_text(field, "label", lang) or field.get("id", "")
    type_name = t["type"].get(kind, kind)
    out.append(f"**{label}** — {type_name}.")
    lines = []
    if field.get("hidden"):
        lines.append(t["hidden"])
    tooltip = lang_text(field, "tooltip", lang)
    hint = lang_text(field, "hint", lang)
    if tooltip:
        lines.append(f"{t['tooltip']}: {tooltip}")
    if hint:
        lines.append(f"{t['hint']}: {hint}")
    if kind not in {"profile_buttons", "toggle_buttons"}:
        lines.append(f"{t['default']}: {fmt_default(field, lang, t)}")
    if field.get("options_source"):
        lines.append(t["dynamic"].format(source=f"`{field['options_source']}`"))
    if field.get("min_selected"):
        lines.append(t["min_selected"].format(n=field["min_selected"]))
    cond = visible_when(field, fields_by_id, lang)
    if cond:
        lines.append(f"{t['visible']}: {cond}")
    options = field.get("options") or []
    if options and kind in {"radio", "tabs", "select", "checkboxes"}:
        rows = []
        for o in options:
            name = option_label(o, lang)
            tip = lang_text(o, "tooltip", lang) if isinstance(o, dict) else ""
            rows.append(f"{name} — {tip}" if tip else name)
        if kind == "checkboxes" and len(rows) > 12:
            lines.append(f"{t['options']} ({t['count'].format(n=len(rows))}): " + "; ".join(rows))
        else:
            lines.append(f"{t['options']}: " + "; ".join(rows))
    if kind == "toggle_buttons":
        rows = []
        for o in field.get("options") or []:
            name = option_label(o, lang)
            tip = lang_text(o, "tooltip", lang) if isinstance(o, dict) else ""
            rows.append(f"**{name}** — {tip}" if tip else f"**{name}**")
        lines.append(f"{t['presets']}:")
        lines.extend(f"  - {r}" for r in rows)
    presets = field.get("presets") or []
    if presets:
        lines.append(f"{t['presets']}:")
        for p in presets:
            name = lang_text(p, "label", lang) or p.get("id", "")
            tip = lang_text(p, "tooltip", lang) or lang_text(p, "hint", lang)
            cond = visible_when(p, fields_by_id, lang)
            row = f"**{name}**" + (f" — {tip}" if tip else "")
            if cond:
                row += f" ({t['visible'].lower()}: {cond})"
            lines.append(f"  - {row}")
    for line in lines:
        out.append(f"- {line}" if not line.startswith("  - ") else line)
    out.append("")


def render_action(node: dict, lang: str, t: dict, out: list[str], level: int) -> None:
    title = lang_text(node, "title", lang) or node.get("id", "")
    kind = str(node.get("kind") or "").lower()
    out.append(f"{'#' * level} {title}")
    out.append("")
    desc = lang_text(node, "description", lang)
    tooltip = lang_text(node, "tooltip", lang)
    meta = []
    if kind in t["kind"]:
        meta.append(t["kind"][kind])
    params = node.get("parameters") or {}
    cond = params.get("visible_when") if isinstance(params, dict) else None
    if isinstance(cond, dict) and cond:
        meta.append(f"{t['visible'].lower()}: " + "; ".join(f"{k} = {v}" for k, v in cond.items()))
    if desc:
        out.append(desc)
        out.append("")
    if tooltip:
        out.append(f"{t['tooltip']}: {tooltip}")
        out.append("")
    if meta:
        out.append("_" + "; ".join(meta) + "_")
        out.append("")


def build(lang: str, manifest: dict) -> str:
    t = TEXT[lang]
    out = [t["title"], "", t["intro"], ""]
    for group in manifest.get("operation_groups", []):
        title = lang_text(group, "title", lang) or group["id"]
        out.append(f"### {title}")
        out.append("")
        desc = lang_text(group, "description", lang)
        tooltip = lang_text(group, "tooltip", lang)
        if desc:
            out.append(desc)
            out.append("")
        if tooltip:
            out.append(f"{t['tooltip']}: {tooltip}")
            out.append("")
        fields = group.get("fields") or []
        children = group.get("children") or []
        fields_by_id = {str(f.get("id")): f for f in fields if isinstance(f, dict)}
        if fields:
            out.append(f"#### {t['fields']}")
            out.append("")
            for field in fields:
                if isinstance(field, dict) and field.get("id"):
                    render_field(field, fields_by_id, lang, t, out)
        if children:
            out.append(f"#### {t['actions']}")
            out.append("")
            for child in children:
                render_action(child, lang, t, out, 5)
                child_fields = child.get("fields") or []
                by_id = {str(f.get("id")): f for f in child_fields if isinstance(f, dict)}
                by_id.update(fields_by_id)
                if child_fields:
                    for field in child_fields:
                        if isinstance(field, dict) and field.get("id"):
                            render_field(field, by_id, lang, t, out)
                elif not fields:
                    out.append(t["no_fields"])
                    out.append("")
    out.append(t["maintenance"])
    out.append("")
    out.append(t["maintenance_intro"])
    out.append("")
    for op in manifest.get("maintenance_operations", []):
        render_action(op, lang, t, out, 3)
    return "\n".join(out).rstrip() + "\n"


def install(path: Path, block: str) -> None:
    text = io.open(path, encoding="utf-8").read()
    payload = f"{START}\n{block}{END}\n"
    if START in text and END in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", lambda _m: payload, text, count=1, flags=re.S)
    else:
        text = text.rstrip("\n") + "\n\n" + payload
    io.open(path, "w", encoding="utf-8", newline="").write(text)


def main() -> int:
    manifest = yaml.safe_load(io.open(MANIFEST, encoding="utf-8"))
    for lang, path in GUIDES.items():
        block = build(lang, manifest)
        install(path, block)
        print(f"{path.name}: {block.count(chr(10))} lines of reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
