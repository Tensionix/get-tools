"""The words of the gates: why an operation may not run now, in the language of the caller.

A gate of the manifest (`parameters.gate: "module:function"`) answers with a key - `gate_adk_missing`
- and the console door turns the key into words (`cli.gate_text`), which the window shows in the
status line and in a notice. The words lived with the window on NiceGUI (`ui_nicegui\\labels.py`);
they belong to the engine: the door must answer in words whatever window calls it, and with no
window at all.

A key with no words here is answered as it is: a gate that says something the table does not know
is still told, not silenced.
"""
from __future__ import annotations

WORDS: dict[str, dict[str, str]] = {
    "en": {
        "gate_adk_missing": (
            "Download is off: Windows ADK Deployment Tools are not installed. Without them the UUP dump converter builds the image "
            "with the system DISM, which on this machine cannot put updates and editions in. Press \"Install ADK\" on this page."
        ),
        "gate_admin_missing": (
            "The image build is off: it needs administrator rights, and this program runs without them. In the window, click "
            "\"Rights: standard\" in the header - the window restarts with the rights. The console command is run from a console "
            "opened as administrator."
        ),
    },
    "ru": {
        "gate_adk_missing": (
            "Скачивание закрыто: не установлены Windows ADK Deployment Tools. Без них конвертер UUP dump собирает образ системным "
            "DISM, а тот на этой машине не вшивает обновления и редакции. Нажмите «Установить ADK» на этой странице."
        ),
        "gate_admin_missing": (
            "Сборка образа закрыта: ей нужны права администратора, а программа запущена без них. В окне щёлкните "
            "«Права: обычные» в заголовке - окно перезапустится с правами. Консольную команду запускают из консоли, "
            "открытой от администратора."
        ),
    },
}


def gate_text(key: str, language: str) -> str:
    """The words of a gate in that language; in English when the language has none; the key itself when nobody has."""
    return str(WORDS.get(language, {}).get(key) or WORDS["en"].get(key) or key)
