"""The console door: it sends a service what the window on NiceGUI sent, and it refuses a wrong call.

    runtime\\python.exe tests\\test_cli_door.py

Started as a file: the embedded Python of the project does not look for modules in the current
folder, so `-m unittest tests.test_cli_door` finds nothing. Written for `unittest` on purpose:
the runtime of a fresh build has no pytest, and pytest runs these all the same.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system_core import cli  # noqa: E402
from system_core.core import operation_model as model  # noqa: E402
from system_core.core import window_layout  # noqa: E402
from system_core.core.live_line import Speed, download_line, frame_line  # noqa: E402
from system_core.core.jobs import redact_parameters  # noqa: E402
from system_core.core.manifest import load_manifest  # noqa: E402

RECORD = ROOT / "tests" / "former_window_parameters.json"
SERVICE = "tests.door_probe_service"

PROBE_MANIFEST = f"""
tool: {{id: probe, name: Probe}}
operation_groups:
  - id: say_word
    title: Say
    service: "{SERVICE}:done"
    fields:
      - {{id: word, type: text, label: Word, default: hello}}
      - {{id: loud, type: checkbox, label: Loud, default: false}}
      - id: colour
        type: radio
        label: Colour
        options: [{{value: red, label: Red}}, {{value: blue, label: Blue}}]
      - id: picks
        type: checkboxes
        label: Picks
        min_selected: 1
        visible_when: {{colour: blue}}
        options: [{{value: a, label: A}}, {{value: b, label: B}}]
  - id: by_machine
    title: By the machine
    service: "{SERVICE}:done"
    fields:
      - id: word
        type: radio
        label: Word
        default: plain
        default_from: "{SERVICE}:machine_word"
        options: [{{value: plain, label: Plain}}, {{value: machine, label: Machine}}]
      - id: odd
        type: radio
        label: Odd
        default: plain
        default_from: "{SERVICE}:no_such_option"
        options: [{{value: plain, label: Plain}}, {{value: machine, label: Machine}}]
  - id: breaks
    title: Breaks
    service: "{SERVICE}:broken"
  - id: colours
    title: Colours
    service: "{SERVICE}:coloured"
  - id: waits
    title: Waits
    service: "{SERVICE}:waits_for_stop"
  - id: breaks_beside
    title: Breaks beside a stop file
    service: "{SERVICE}:breaks_beside_a_stop_file"
  - id: shop
    title: Shop
    fields:
      - id: product
        type: radio
        label: Product
        options: [{{value: closed, label: Closed}}, {{value: open, label: Open}}]
      - id: version
        type: checkboxes
        label: Version
        options_source: "{SERVICE}:versions"
        depends_on: [product]
      - id: nonsense
        type: radio
        label: Nonsense
        options_source: "{SERVICE}:nonsense"
    children:
      - id: buy
        title: Buy
        service: "{SERVICE}:done"
        parameters: {{gate: "{SERVICE}:gate", validate_fields: false}}
  - id: import_probe
    title: Import
    service: "{SERVICE}:reads_source"
    parameters: {{gate: "{SERVICE}:gate_by_folders"}}
    fields:
      - id: packages
        type: checkboxes
        label: Packages
        options_source: "{SERVICE}:lying_in_source"
maintenance_operations:
  - id: keep_token
    title: Keep the token
    service: "{SERVICE}:takes_secret"
    parameters:
      prompt: {{field: probe_token, secret: true}}
"""


class Door(unittest.TestCase):
    def setUp(self) -> None:
        self.was = os.getcwd()
        self.folder = tempfile.TemporaryDirectory()
        self.project = Path(self.folder.name) / "project"
        (self.project / "config").mkdir(parents=True)
        (self.project / "config" / "tool_manifest.yaml").write_text(PROBE_MANIFEST, encoding="utf-8")
        (self.project / "config" / "version.json").write_text('{"version": "0.0.1"}', encoding="utf-8")
        cli._nobody_listens = False

    def tearDown(self) -> None:
        os.chdir(self.was)
        self.folder.cleanup()

    def call(self, *arguments: str, stdin: str = "") -> tuple[int, list[str], str]:
        out, err = io.StringIO(), io.StringIO()
        keep = sys.stdin
        sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--root", str(self.project), "--language", "en", *arguments])
        finally:
            sys.stdin = keep
        return code, out.getvalue().splitlines(), err.getvalue()

    @staticmethod
    def service_lines(lines: list[str]) -> list[dict]:
        return [json.loads(line[len(cli.CONTROL):]) for line in lines if line.startswith(cli.CONTROL)]

    def test_a_run_tells_its_start_its_course_and_its_end(self) -> None:
        result = self.project / "result.json"
        code, lines, _ = self.call("run", "--operation", "say_word", "--set", "word=mew", "--result", str(result))
        self.assertEqual(code, cli.EXIT_OK)
        told = self.service_lines(lines)
        self.assertEqual(told[0]["started"]["operation"], "say_word")
        self.assertIn({"activity": "a live line"}, told)
        self.assertIn({"progress": 0.5}, told)
        self.assertEqual(told[-1]["finished"]["outcome"], "done")
        self.assertIn("probe: mew", lines)
        written = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual((written["outcome"], written["data"]), ("done", {"word": "mew"}))

    def test_a_caller_by_a_pipe_gets_no_colour_codes(self) -> None:
        code, lines, _ = self.call("run", "--operation", "colours")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("[WARN] yellow", lines)
        self.assertIn({"activity": "  ███▒▒  1.0 MB / 2.0 MB"}, self.service_lines(lines))
        self.assertFalse([line for line in lines if "\x1b" in line or "\\u001b" in line], lines)

    def test_a_caller_that_paints_gets_the_colour_codes_and_a_clean_live_line(self) -> None:
        code, lines, _ = self.call("run", "--operation", "colours", "--colours")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("\x1b[33m[WARN] yellow\x1b[0m", lines)
        self.assertIn({"activity": "  ███▒▒  1.0 MB / 2.0 MB"}, self.service_lines(lines))

    def test_a_default_may_follow_the_machine(self) -> None:
        code, lines, _ = self.call("describe")
        told = json.loads(lines[-1])
        fields = {field["id"]: field for field in told["forms"][next(node for node in told["nodes"] if node["id"] == "by_machine")["form"]]}
        # The window is told the default the door would use; what is not an option of the field is no default.
        self.assertEqual((fields["word"]["default"], fields["odd"]["default"]), ("machine", "plain"))
        result = self.project / "result.json"
        self.assertEqual(self.call("run", "--operation", "by_machine", "--result", str(result))[0], cli.EXIT_OK)
        self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["data"], {"word": "machine"})
        # A value given by the caller stands over what the machine wants.
        self.call("run", "--operation", "by_machine", "--set", "word=plain", "--result", str(result))
        self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["data"], {"word": "plain"})

    def test_a_failure_is_code_one(self) -> None:
        code, lines, _ = self.call("run", "--operation", "breaks")
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual(self.service_lines(lines)[-1]["finished"]["outcome"], "failed")

    def test_a_stop_is_a_stop_and_leaves_no_traceback(self) -> None:
        stop = self.project / "stop"
        stop.write_text("", encoding="utf-8")
        code, lines, _ = self.call("run", "--operation", "waits", "--cancel-file", str(stop))
        self.assertEqual(code, cli.EXIT_STOPPED)
        self.assertEqual(self.service_lines(lines)[-1]["finished"]["outcome"], "stopped")
        self.assertFalse([line for line in lines if "Traceback" in line], lines)

    def test_a_failure_beside_a_stop_file_stays_a_failure(self) -> None:
        stop = self.project / "stop"
        stop.write_text("", encoding="utf-8")
        code, lines, _ = self.call("run", "--operation", "breaks_beside", "--cancel-file", str(stop))
        self.assertEqual(code, cli.EXIT_FAILED)
        self.assertEqual(self.service_lines(lines)[-1]["finished"]["outcome"], "failed")

    def test_wrong_calls_run_nothing(self) -> None:
        for arguments in (
            ("run", "--operation", "no_such"),
            ("run", "--operation", "say_word", "--set", "nonsense=1"),
            ("run", "--operation", "say_word", "--set", "colour=green"),
            ("run", "--operation", "say_word", "--set", "loud=maybe"),
            # `picks` is in sight for blue and wants one tick.
            ("run", "--operation", "say_word", "--set", "colour=blue"),
            ("run", "--operation", "shop"),
            ("run", "--operation", "keep_token", "--secret-stdin", "other_name"),
        ):
            code, lines, error = self.call(*arguments)
            self.assertEqual(code, cli.EXIT_USAGE, arguments)
            self.assertFalse(self.service_lines(lines), arguments)
            self.assertIn("[ERR]", error)

    def test_a_hidden_group_of_ticks_is_not_asked_for(self) -> None:
        code, _, _ = self.call("run", "--operation", "say_word", "--set", "colour=red")
        self.assertEqual(code, cli.EXIT_OK)

    def test_a_secret_comes_by_the_standard_input_and_is_not_shown(self) -> None:
        result = self.project / "result.json"
        code, lines, _ = self.call("run", "--operation", "keep_token", "--secret-stdin", "probe_token",
                                   "--result", str(result), stdin="s3cret-value\n")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(result.read_text(encoding="utf-8"))["data"], {"length": 12})
        self.assertFalse([line for line in lines if "s3cret-value" in line])
        logs = "".join(path.read_text(encoding="utf-8") for path in (self.project / "logs").glob("*.log"))
        self.assertNotIn("s3cret-value", logs)
        self.assertIn("***REDACTED***", logs)

    def test_a_list_is_asked_of_its_provider_with_the_values_of_the_form(self) -> None:
        code, lines, _ = self.call("options", "--operation", "shop", "--field", "version", "--set", "product=open")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual([option["value"] for option in json.loads(lines[-1])["options"]], ["open-1", "open-2"])
        # Nothing set: the provider still sees the default of the field its list depends on.
        code, lines, _ = self.call("options", "--operation", "shop", "--field", "version")
        self.assertEqual([option["value"] for option in json.loads(lines[-1])["options"]], ["closed-1", "closed-2"])
        code, lines, _ = self.call("options", "--operation", "shop", "--field", "product")
        self.assertEqual([option["value"] for option in json.loads(lines[-1])["options"]], ["closed", "open"])

    def test_the_refusal_of_a_list_is_told_in_its_providers_own_words(self) -> None:
        # The provider fails with a ValueError of its own. It is called once, and what it said is the answer -
        # not "missing 2 required positional arguments" of a second call with fewer of them.
        from tests import door_probe_service

        door_probe_service.NONSENSE_CALLS.clear()
        code, lines, _ = self.call("options", "--operation", "shop", "--field", "nonsense")
        self.assertEqual(code, cli.EXIT_FAILED)
        told = json.loads(lines[-1])
        self.assertEqual(told["error"], "ValueError: the catalogue answered nonsense (JSON did not parse)")
        self.assertEqual((told["options"], len(door_probe_service.NONSENSE_CALLS)), ([], 1))

    def test_a_gate_answers_and_holds_a_run(self) -> None:
        code, lines, _ = self.call("gate", "--operation", "buy")
        self.assertEqual((code, json.loads(lines[-1])["key"]), (cli.EXIT_OK, "probe_gate_closed"))
        code, lines, _ = self.call("gate", "--operation", "buy", "--set", "product=open")
        self.assertFalse(json.loads(lines[-1])["blocked"])
        self.assertEqual(self.call("run", "--operation", "buy")[0], cli.EXIT_USAGE)
        self.assertEqual(self.call("run", "--operation", "buy", "--set", "product=open")[0], cli.EXIT_OK)

    def test_a_list_and_a_gate_look_into_the_source_the_run_will_read(self) -> None:
        # The audit of 10.10.2026, finding 3: `options` took no --source, and a list of what lies in the source
        # always showed `input` of the program.
        source = Path(self.folder.name) / "Chosen Source"
        (source / "NetCard").mkdir(parents=True)
        (source / "winget-export.json").write_text('{"packages": 3}', encoding="utf-8")
        target = Path(self.folder.name) / "Chosen Target"

        code, lines, _ = self.call("options", "--operation", "import_probe", "--field", "packages", "--source", str(source))
        self.assertEqual((code, [option["value"] for option in json.loads(lines[-1])["options"]]), (cli.EXIT_OK, ["input:NetCard"]))
        # Without the key it is `input` of the project, as for a run.
        (self.project / "input" / "FromInput").mkdir(parents=True)
        code, lines, _ = self.call("options", "--operation", "import_probe", "--field", "packages")
        self.assertEqual([option["value"] for option in json.loads(lines[-1])["options"]], ["input:FromInput"])

        (self.project / "input" / "FromInput").rmdir()
        self.assertTrue(json.loads(self.call("gate", "--operation", "import_probe")[1][-1])["blocked"])
        code, lines, _ = self.call("gate", "--operation", "import_probe", "--source", str(source), "--target", str(target))
        self.assertEqual((code, json.loads(lines[-1])["blocked"]), (cli.EXIT_OK, False))

        result = Path(self.folder.name) / "result.json"
        code, _, _ = self.call("run", "--operation", "import_probe", "--source", str(source), "--target", str(target), "--result", str(result))
        told = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual((code, told["data"]["read"], told["data"]["target"]), (cli.EXIT_OK, '{"packages": 3}', str(target)))
        # A path quoted with a backslash at its end is a wrong call of a list too, not a crash.
        self.assertEqual(self.call("options", "--operation", "import_probe", "--field", "packages", "--source", 'E:\\My Files" --x')[0], cli.EXIT_USAGE)

    def test_the_drivers_for_the_image_are_listed_from_the_chosen_source(self) -> None:
        # The program's own list: a driver package under the chosen folder is a card `input:` of that folder.
        from system_core.services import vendor_service

        source = Path(self.folder.name) / "Drivers Here"
        (source / "NetCard").mkdir(parents=True)
        (source / "NetCard" / "net.inf").write_text("; a driver", encoding="utf-8")
        kept = vendor_service.list_machine_drivers
        vendor_service.list_machine_drivers = lambda: []  # the driver store of this machine is not asked: pnputil takes seconds
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["--language", "en", "options", "--operation", "vendor_downloads", "--field", "driver_packages", "--source", str(source)])
        finally:
            vendor_service.list_machine_drivers = kept
        cards = [option["value"] for option in json.loads(out.getvalue().splitlines()[-1])["options"] if option.get("value")]
        self.assertEqual((code, cards), (cli.EXIT_OK, ["input:NetCard"]))

    def test_a_gate_is_told_in_words_with_no_window_to_take_them_from(self) -> None:
        # The words of the gates lived with the window on NiceGUI; a door that could not import it answered with the key.
        import builtins

        for language, start in (("en", "Download is off: Windows ADK Deployment Tools are not installed"),
                                ("ru", "Скачивание закрыто: не установлены Windows ADK Deployment Tools")):
            self.assertTrue(cli.gate_text("gate_adk_missing", language).startswith(start), language)
        self.assertEqual(cli.gate_text("probe_gate_closed", "ru"), "probe_gate_closed")
        # Every key a gate of the program may answer with has its words in both languages.
        from system_core.core import gate_words
        from system_core.services import vendor_service
        import inspect
        import re

        keys = set(re.findall(r'return "(gate_[a-z_]+)"', inspect.getsource(vendor_service.download_gate)))
        self.assertTrue(keys)
        for key in keys:
            for language in ("en", "ru"):
                self.assertIn(key, gate_words.WORDS[language], (key, language))
        # ... and the door does not reach for the former window to say them.
        real_import = builtins.__import__

        def no_former_window(name, *args, **kwargs):
            if name.startswith("system_core.ui_nicegui"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        builtins.__import__ = no_former_window
        try:
            self.assertIn("Windows ADK", cli.gate_text("gate_adk_missing", "en"))
        finally:
            builtins.__import__ = real_import

    def test_a_call_that_names_no_language_speaks_the_one_chosen_in_the_window(self) -> None:
        import argparse

        paths = cli.get_project_paths(self.project)
        asked = argparse.Namespace(language="")
        settings = paths.config / "window_settings.json"
        self.assertFalse(settings.exists())
        self.assertEqual(cli.language_of(asked, paths), "en")
        # The window writes its settings with a byte order mark or without; "Lang" is "" until a button was pressed.
        for written, wanted in (('{"Lang": "ru"}', "ru"), ('\ufeff{"Lang": "ru"}', "ru"), ('{"Lang": ""}', "en"),
                                ('{"Look": "vault"}', "en"), ('[]', "en"), ('not json', "en")):
            settings.write_text(written, encoding="utf-8")
            self.assertEqual(cli.language_of(asked, paths), wanted, written)
        # What the caller names is taken first.
        self.assertEqual(cli.language_of(argparse.Namespace(language="en"), paths), "en")
        settings.unlink()

    def test_a_list_of_models_is_a_drop_down_with_pins(self) -> None:
        # The owner, 11.10.2026: "the list of models has to be a select. With pins and unpins, refreshed by the keys."
        import tempfile as temporary

        from system_core.services import winget_ai_service as ai

        manifest = load_manifest(ROOT / "config" / "tool_manifest.yaml")
        node = model.find_node(manifest, "ai_llm_planning", runs=False)
        told = {row["id"]: row for row in model.describe_node(node)["fields"]}
        for field, key in (("openai_model", "openai_api_key_ref"), ("gemini_model", "gemini_api_key_ref")):
            self.assertEqual((told[field]["view"], told[field]["depends_on"], bool(told[field]["pins"])), ("dropdown", [key], True), field)
        # The keys and the short choices stay rows of buttons.
        self.assertNotIn("view", told["openai_api_key_ref"])

        with temporary.TemporaryDirectory() as folder:
            cache = ai.MODEL_CACHE_PATH
            ai.MODEL_CACHE_PATH = Path(folder) / "models.json"
            try:
                def pin(*words: str) -> tuple[int, dict]:
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                        code = cli.main(["--language", "en", "pin", "--operation", "ai_llm_planning", *words])
                    lines = [line for line in out.getvalue().splitlines() if line.startswith("{")]
                    return code, json.loads(lines[-1]) if lines else {}

                self.assertEqual(pin("--field", "openai_model", "--value", "gpt-b"), (cli.EXIT_OK, {"field": "openai_model", "value": "gpt-b", "pinned": True, "ok": True}))
                self.assertEqual(pin("--field", "openai_model", "--value", "gpt-a")[0], cli.EXIT_OK)
                self.assertEqual(ai._pinned_models("openai"), ["gpt-a", "gpt-b"])
                # The pinned stand first and are marked - with a live answer of the provider too; the default
                # line keeps the head, a word of trouble the foot; a pinned model the key does not see stays.
                live = [ai._option("", "Auto"), ai._option("gpt-c", "gpt-c"), ai._option("gpt-b", "gpt-b - fine"), ai._option("__request_failed__", "no")]
                ordered = ai._pinned_first("openai", live)
                self.assertEqual([(row["value"], bool(row.get("pinned"))) for row in ordered],
                                 [("", False), ("gpt-a", True), ("gpt-b", True), ("gpt-c", False), ("__request_failed__", False)])
                self.assertEqual(ordered[2]["label"], "gpt-b - fine")
                # The mark is a boolean: the window reads it as one.
                self.assertIs(ordered[1]["pinned"], True)
                # The pin comes off.
                self.assertEqual(pin("--field", "openai_model", "--value", "gpt-a", "--off")[1]["pinned"], False)
                self.assertEqual(ai._pinned_models("openai"), ["gpt-b"])
                self.assertEqual(ai._pinned_models("gemini"), [])
                # A word of trouble is no option to pin, and a field without pins has none.
                code, said = pin("--field", "openai_model", "--value", "__request_failed__")
                self.assertEqual((code, said["ok"]), (cli.EXIT_FAILED, False))
                self.assertEqual(pin("--field", "provider", "--value", "openai")[0], cli.EXIT_USAGE)
                self.assertEqual(pin("--field", "no_such_field", "--value", "x")[0], cli.EXIT_USAGE)
            finally:
                ai.MODEL_CACHE_PATH = cache

    def test_the_command_line_of_the_terminal_has_its_operation(self) -> None:
        # The owner, 11.10.2026: a terminal one can type a command into. The window runs it as it runs any operation.
        manifest = load_manifest(ROOT / "config" / "tool_manifest.yaml")
        told = model.describe(manifest)
        terminal = next(node for node in told["card_operations"] if node["id"] == "terminal_command")
        self.assertTrue(terminal["runs"])
        fields = told["forms"][terminal["form"]] if terminal.get("form") else terminal["fields"]
        self.assertEqual([(field["id"], field["kind"]) for field in fields], [("shell", "select"), ("command", "text"), ("cwd", "text")])
        node = model.find_node(manifest, "terminal_command")
        got = model.build_parameters(node, {"shell": "cmd", "command": "echo ok"}, "", "", "en")
        self.assertEqual((got["shell"], got["command"], got["cwd"]), ("cmd", "echo ok", ""))
        # It stands on no page and is counted with no section: the layout has nothing to say of it.
        self.assertEqual(window_layout.problems(window_layout.load(ROOT / "config"), manifest), [])

    def test_describe_asks_no_provider(self) -> None:
        code, lines, _ = self.call("describe")
        self.assertEqual(code, cli.EXIT_OK)
        told = json.loads(lines[-1])
        shop = next(node for node in told["nodes"] if node["id"] == "shop")
        # A form is told once: the node names it, and its child, which has the same fields, names the same one.
        self.assertEqual((shop["form"], shop["children"][0]["form"]), ("shop", "shop"))
        self.assertNotIn("fields", shop)
        version = next(field for field in told["forms"]["shop"] if field["id"] == "version")
        self.assertEqual((version["options_source"], version["depends_on"]), (f"{SERVICE}:versions", ["product"]))
        self.assertNotIn("options", version)
        self.assertEqual(told["maintenance"][0]["prompt"], "probe_token")


class FormerWindow(unittest.TestCase):
    """The door gives a service the parameters the window on NiceGUI gave it."""

    def setUp(self) -> None:
        # The record was made of the defaults the manifest writes: what a machine wants instead is none of its business.
        model.machine_defaults = False

    def tearDown(self) -> None:
        model.machine_defaults = True

    def test_every_operation_gets_what_the_former_window_sent(self) -> None:
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        manifest = load_manifest(ROOT / "config" / "tool_manifest.yaml")
        compared, skipped = 0, []
        for operation_id, entry in sorted(record.items()):
            node = model.find_node(manifest, operation_id)
            signature = [f"{model.field_id(field)}:{field.get('type', 'text')}" for field in node.fields] if node else None
            if signature != entry["signature"]:
                # Changed in the manifest since the record was taken: it no longer answers to it.
                skipped.append(operation_id)
                continue
            for language in ("en", "ru"):
                for values, wanted in (({}, entry[language]["defaults"]),
                                       (entry[language]["values"], entry[language]["changed"])):
                    got = model.build_parameters(node, values, "<root>\\input", "<root>\\output", language)
                    got = json.loads(json.dumps(got, ensure_ascii=False, default=str).replace(
                        json.dumps(str(ROOT))[1:-1], "<root>"))
                    self.assertEqual(got, wanted, f"{operation_id} [{language}]")
            compared += 1
        self.assertGreaterEqual(compared, len(record) // 2, f"skipped: {skipped}")

    def test_the_door_knows_the_whole_manifest(self) -> None:
        told = model.describe(load_manifest(ROOT / "config" / "tool_manifest.yaml"))

        def commands(node) -> int:
            return sum(commands(child) for child in node["children"]) if node["children"] else int(node["runs"])

        self.assertTrue(told["nodes"])
        for node in [*told["nodes"], *told["maintenance"], *told["card_operations"]]:
            self.assertTrue(node["children"] or node["runs"], node["id"])
        self.assertEqual(sum(commands(node) for node in told["nodes"]) + len(told["maintenance"]),
                         len(json.loads(RECORD.read_text(encoding="utf-8"))))


class Layout(unittest.TestCase):
    """config/window_layout.yaml: every operation of the manifest has its place in the new window, or a reason."""

    def setUp(self) -> None:
        self.manifest = load_manifest(ROOT / "config" / "tool_manifest.yaml")
        self.layout = window_layout.load(ROOT / "config")

    def test_nothing_is_wrong_with_the_layout(self) -> None:
        self.assertEqual(window_layout.problems(self.layout, self.manifest), [])

    def test_a_page_draws_no_field_it_sets_itself_and_a_guest_knows_its_card(self) -> None:
        told = window_layout.describe(self.layout, self.manifest)
        self.assertEqual([section["id"] for section in told["sections"]],
                         ["winget", "windows", "media", "system", "drivers", "hardware"])
        monitor = next(page for section in told["sections"] for page in section["pages"] if page["id"] == "monitor")
        host, guest = monitor["operations"]
        self.assertEqual(monitor["fixed"], {"vendor": "techpowerup", "tpu_kind": "monitor"})
        self.assertFalse({"vendor", "tpu_kind"} & {field["id"] for field in host["fields"]})
        self.assertEqual((host["beside"], guest["beside"]), ("", host["id"]))
        winutil = next(operation for section in told["sections"] for page in section["pages"]
                       for operation in page["operations"] if operation["id"] == "vendor_winutil")
        self.assertEqual((winutil["fields"], winutil["direct"]), ([], True))

    def test_a_wrong_layout_is_told(self) -> None:
        broken = {"sections": [{"id": "s", "pages": [{"id": "p", "fixed": {"vendor": "nobody", "no_field": 1},
                                                      "cards": [{"op": "vendor_download"}, {"op": "no_such"}]}]}],
                  "shared": ["vendor_extract", "no_such_field"]}
        found = "\n".join(window_layout.problems(broken, self.manifest))
        for words in ("does not have: 'no_such'", "stands nowhere in the window: 'vendor_link'",
                      "fixes vendor='nobody'", "fixes 'no_field'", "`shared` names a field no operation has: 'no_such_field'"):
            self.assertIn(words, found)
        self.assertNotIn("'vendor_extract'", found)

    def test_the_fields_that_are_one_for_the_window_are_told(self) -> None:
        # The audit of 10.10.2026, finding 2: a value belongs to its page; three fields of unpacking are the window's.
        told = window_layout.describe(self.layout, self.manifest)
        self.assertEqual(told["shared"], ["vendor_extract", "vendor_delete_zip", "vendor_flat"])
        # The token has its button in the header and asks for its value; the clearing of the two folders
        # stands beside the row of the downloads; nothing waits for a place any more (finding 6).
        self.assertIn("github_token", [operation["id"] for operation in told["service"]])
        token = next(operation for operation in told["service"] if operation["id"] == "github_token")
        self.assertEqual((token["prompt"], token["parameters"]["prompt"]["secret"]), ("github_token", True))
        self.assertEqual([(operation["id"], operation["tone"], operation["kind"]) for operation in told["folders"]],
                         [("cleanup_input_output", "burn", "dangerous")])
        self.assertEqual(told["pending"], {})
        # DRIVERS stand by the kind of device, each page with a row of makers (the owner, 11.10.2026).
        drivers = next(section for section in told["sections"] if section["id"] == "drivers")
        self.assertEqual([page["id"] for page in drivers["pages"]], ["gpu", "chipset", "wifi", "bluetooth", "ethernet", "audio", "this_machine"])
        makers = {page["id"]: [choice["id"] for choice in page["choices"]] for page in drivers["pages"]}
        self.assertEqual(makers["gpu"], ["nvidia", "amd", "intel", "qualcomm"])
        # Intel and AMD first, then Realtek, then the rest by how often they are met.
        self.assertEqual(makers["chipset"], ["intel", "amd", "intel_me", "intel_rst", "intel_npu"])
        self.assertEqual(makers["wifi"], ["intel", "realtek", "mediatek", "qualcomm", "killer", "broadcom"])
        self.assertEqual(makers["ethernet"], ["intel", "realtek", "killer", "marvell", "broadcom"])
        self.assertEqual((makers["audio"], makers["this_machine"]), (["realtek"], []))
        for page in drivers["pages"]:
            # A page opens on one of its makers - the one this PC has a device of, else the first; a page without makers on none.
            self.assertIn(page["chosen"], makers[page["id"]] or [""], page["id"])
            for choice in page["choices"]:
                self.assertTrue(choice["title"] and choice["about"] and choice["about_ru"] and choice["values"].get("vendor"), (page["id"], choice["id"]))
        # Every other page has no row of makers.
        self.assertTrue(all(not page["choices"] and page["chosen"] == "" for section in told["sections"] if section["id"] != "drivers" for page in section["pages"]))
        # The sources of a kind of device do not overlap: a maker of a page is told once, and the catalog of Windows Update
        # keeps every driver it had - each stands on a page of its kind.
        from system_core.vendors import mscatalog

        placed = {(choice["values"]["msc_kind"], choice["values"][f"msc_{choice['values']['msc_kind']}"])
                  for page in drivers["pages"] for choice in page["choices"] if choice["values"].get("vendor") == "mscatalog"}
        self.assertEqual(placed, {(kind, name) for kind, drivers_of in mscatalog.KINDS.items() for name in drivers_of})
        # A card may name the order of the blocks of its form: the plan of the AI page asks first, then instructs,
        # then chooses the model (the owner, 11.10.2026). Every block named is a block the form has.
        ai = next(page for section in told["sections"] for page in section["pages"] if (section["id"], page["id"]) == ("winget", "ai"))
        plan = next(operation for operation in ai["operations"] if operation["id"] == "ai_plan_packages")
        self.assertEqual(plan["groups"], ["request", "prompt", "provider", "generation"])
        described = model.describe(self.manifest)
        fields = described["forms"][plan["form"]] if "forms" in described and plan.get("form") else plan["fields"]
        self.assertTrue(set(plan["groups"]) <= {field["group"] for field in fields}, sorted({field["group"] for field in fields}))
        self.assertTrue(all("groups" not in operation for operation in ai["operations"] if operation["id"] != "ai_plan_packages"))
        # A page says whether its operations read a folder the person may choose (finding 3).
        self.assertEqual(sorted(f"{section['id']}/{page['id']}" for section in told["sections"] for page in section["pages"] if page["source"]),
                         ["windows/image", "winget/transfer"])
        # ... and whether it has one search for all its lists (finding 4).
        self.assertEqual(sorted(f"{section['id']}/{page['id']}" for section in told["sections"] for page in section["pages"] if page["filter"]),
                         ["winget/catalogue", "winget/installed"])
        download = next(operation for section in told["sections"] for page in section["pages"]
                        for operation in page["operations"] if operation["id"] == "vendor_download")
        self.assertTrue(set(told["shared"]) <= {field["id"] for field in download["fields"]})
        self.assertEqual(window_layout.describe({}, self.manifest)["shared"], [])


class Description(unittest.TestCase):
    """`describe` of the program itself: every form told once (the audit of 10.10.2026, finding 14)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest(ROOT / "config" / "tool_manifest.yaml")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cls.code = cli.main(["--language", "en", "describe"])
        cls.line = out.getvalue().splitlines()[-1]
        cls.told = json.loads(cls.line)

    def test_the_answer_is_small(self) -> None:
        # 3.8 MB before: the form of the downloads was told 33 times, 75 KB each.
        self.assertEqual(self.code, cli.EXIT_OK)
        self.assertLess(len(self.line.encode("utf-8")), 700_000)
        self.assertNotIn('"fields"', self.line)

    def test_every_place_has_the_fields_it_had(self) -> None:
        forms = self.told["forms"]

        def places(layout: dict) -> dict[str, dict]:
            found = {f"{section['id']}/{page['id']}/{place['id']}": place for section in layout["sections"] for page in section["pages"] for place in page["operations"]}
            found.update({f"service/{place['id']}": place for place in layout["service"]})
            found.update({f"folders/{place['id']}": place for place in layout["folders"]})
            return found

        whole = places(window_layout.describe(window_layout.load(ROOT / "config"), self.manifest))  # every place with its own fields
        shared = places(self.told["layout"])
        self.assertEqual(sorted(shared), sorted(whole))
        self.assertGreaterEqual(len(shared), 74)
        for where, place in shared.items():
            put_together = [field for field in forms.get(place["form"], []) if field["id"] not in place["dropped"]]
            self.assertEqual(json.dumps(put_together, default=str, sort_keys=True), json.dumps(whole[where]["fields"], default=str, sort_keys=True), where)
            self.assertEqual(place["direct"], bool(place["runs"]) and not put_together, where)
        # A page drops the fields it sets by itself, and nothing else.
        resolve = shared["media/blackmagic/vendor_download"]
        self.assertEqual((resolve["form"], resolve["dropped"]), ("vendor_downloads", ["vendor"]))
        # ... and the fields any of its choices sets, whichever of them is on.
        wifi = shared["drivers/wifi/vendor_download"]
        self.assertEqual(sorted(wifi["dropped"]), ["msc_kind", "msc_wifi", "tpu_driver", "tpu_kind", "vendor"])

    def test_every_node_has_the_fields_it_had(self) -> None:
        def flat(nodes: list[dict]) -> dict[str, dict]:
            found: dict[str, dict] = {}
            for node in nodes:
                found[node["id"]] = node
                found.update(flat(node.get("children") or []))
            return found

        whole = model.describe(self.manifest)
        for part in ("nodes", "maintenance", "card_operations"):
            was, now = flat(whole[part]), flat(self.told[part])
            self.assertEqual(sorted(now), sorted(was))
            for name, node in now.items():
                self.assertEqual(json.dumps(self.told["forms"].get(node["form"], []), default=str, sort_keys=True),
                                 json.dumps(was[name]["fields"], default=str, sort_keys=True), name)
        # The name of a form is the first node of the manifest that has it.
        self.assertTrue(all(name in flat(self.told["nodes"]) | flat(self.told["maintenance"]) | flat(self.told["card_operations"]) for name in self.told["forms"]))


class LiveLine(unittest.TestCase):
    def test_a_download_says_how_far_how_much_and_how_fast(self) -> None:
        mb = 1024 * 1024
        self.assertEqual(download_line("Vivaldi 8.2", 132.0 * mb, 148.9 * mb, 8.6 * mb),
                         "[DOWNLOAD] Vivaldi 8.2: 88% · 132.0 / 148.9 MB · 8.6 MB/s")
        self.assertEqual(download_line("a.iso", 3 * 1024 * mb, 6 * 1024 * mb), "[DOWNLOAD] a.iso: 50% · 3.0 / 6.0 GB")
        self.assertEqual(download_line("a.zip", 5 * mb, 0, 512 * 1024), "[DOWNLOAD] a.zip: 5.0 MB · 512.0 KB/s")
        self.assertEqual(download_line("tiny", 100, 900), "[DOWNLOAD] tiny: 11% · 100 / 900 B")

    def test_a_frame_of_another_program_becomes_the_same_line(self) -> None:
        speed = Speed()
        first = frame_line("Vivaldi.Vivaldi", "  ██████▒▒▒▒  10.0 MB / 100 MB", speed, now=0.0)
        self.assertEqual(first, "[DOWNLOAD] Vivaldi.Vivaldi: 10% · 10.0 / 100.0 MB")
        second = frame_line("Vivaldi.Vivaldi", "  ███████▒▒▒  30.0 MB / 100 MB", speed, now=2.0)
        self.assertEqual(second, "[DOWNLOAD] Vivaldi.Vivaldi: 30% · 30.0 / 100.0 MB · 10.0 MB/s")
        # The next file of the same run: the speed is measured anew.
        third = frame_line("Vivaldi.Vivaldi", "  █▒▒▒▒▒▒▒▒▒  1.0 MB / 50.0 MB", speed, now=2.2)
        self.assertEqual(third, "[DOWNLOAD] Vivaldi.Vivaldi: 2% · 1.0 / 50.0 MB")
        self.assertEqual(frame_line("Vivaldi.Vivaldi", "  ██████▒▒▒▒  45%"), "Vivaldi.Vivaldi: 45%")
        self.assertEqual(frame_line("Vivaldi.Vivaldi", "  Starting package install..."), "Starting package install...")


class Secrets(unittest.TestCase):
    def test_a_token_is_replaced_and_a_count_of_tokens_is_not(self) -> None:
        shown = redact_parameters({"github_token": "abc", "llm_max_output_tokens": 4000, "empty_token": "",
                                   "nested": [{"api_key": "k"}], "word": "plain"})
        self.assertEqual(shown, {"github_token": "***REDACTED***", "llm_max_output_tokens": 4000, "empty_token": "",
                                 "nested": [{"api_key": "***REDACTED***"}], "word": "plain"})


if __name__ == "__main__":
    unittest.main()
