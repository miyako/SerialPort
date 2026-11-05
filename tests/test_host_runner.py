import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host_runner", ROOT / "scripts/test_4d.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
CONTRACT = json.loads((ROOT / "tests/host/contract.json").read_text(encoding="utf-8"))


def valid_status(compiled=False):
    status = {"success": True, "finished": True, "compiled": compiled,
              "cases": [{"ok": True, **copy.deepcopy(case)} for case in CONTRACT["feature"]["cases"]],
              "fileArgument": {"ok": True, "stdout": "file round trip", "stderr": "", "exitCode": 0},
              "folderArgument": {"ok": True, "stdout": "folder round trip", "stderr": "", "exitCode": 0}}
    def apply_assertions(value, assertions):
        for assertion in assertions:
            tokens = assertion["path"].split("/")[1:]
            if not tokens:
                if "equals" in assertion:
                    value = copy.deepcopy(assertion["equals"])
                continue
            destination = value
            for token in tokens[:-1]:
                destination = destination[int(token)] if isinstance(destination, list) else destination.setdefault(token, {})
            key = int(tokens[-1]) if isinstance(destination, list) else tokens[-1]
            if "equals" in assertion:
                destination[key] = copy.deepcopy(assertion["equals"])
            if "length" in assertion:
                destination[key] = [{} for _ in range(assertion["length"])]
            if "each" in assertion:
                destination[key] = [apply_assertions(entry, assertion["each"]) for entry in destination[key]]
        return value

    for section in ("feature", "execution"):
        status = apply_assertions(status, CONTRACT[section]["assertions"])
    assertions = {entry["path"]: entry for entry in CONTRACT["execution"]["assertions"]}
    rejected = assertions["/rejectedArguments"]["each"][0]["equals"]
    status["rejectedArguments"] = [{"type": i, "result": copy.deepcopy(rejected)} for i in range(9)]
    nul = assertions["/rejectedNul"]["each"][0]["equals"]
    status["rejectedNul"] = [copy.deepcopy(nul) for _ in range(3)]
    return status


class ContractTests(unittest.TestCase):
    def test_semantic_nondeterministic_cases_require_real_values_and_patterns(self):
        contract = {"schema": 1, "feature": {
            "casesPath": "/cases", "caseCount": 1,
            "caseAssertions": [{"path": "/value/id", "matches": "[a-z]{3}-[0-9]{3}"}],
            "assertions": [{"path": "/semanticChecks", "equals": True}]},
            "execution": {"successPaths": [], "assertions": [{"path": "/invalid/ok", "equals": False}]}}
        status = {"success": True, "finished": True, "compiled": False,
                  "cases": [{"ok": True, "stdout": "abc-123\n", "stderr": "", "exitCode": 0,
                             "value": {"id": "abc-123"}}],
                  "semanticChecks": True, "invalid": {"ok": False}}
        runner.validate_status(status, contract, False)
        status["cases"][0]["value"]["id"] = "bad"
        with self.assertRaisesRegex(ValueError, "pattern mismatch"):
            runner.validate_status(status, contract, False)
        contract["feature"]["caseAssertions"][0]["matches"] = "["
        with self.assertRaisesRegex(ValueError, "invalid host contract regex"):
            runner.validate_status(status, contract, False)

    def test_source_and_release_exact_mode(self):
        for compiled in (False, True):
            status = valid_status(compiled)
            self.assertIs(runner.validate_status(status, CONTRACT, compiled), status)
            with self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, not compiled)

    def test_completion_must_be_literal_true_and_mode_literal_boolean(self):
        for key in ("success", "finished", "compiled"):
            for value in (None, 0, 1, "true", "false", [], {}):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    status = valid_status()
                    status[key] = value
                    runner.validate_status(status, CONTRACT, False)
            status = valid_status()
            del status[key]
            with self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)
        for key in ("success", "finished"):
            status = valid_status()
            status[key] = False
            with self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)

    def test_feature_results_require_all_output_fields(self):
        for key in ("ok", "stdout", "stderr", "exitCode", "value"):
            status = valid_status()
            del status["cases"][0][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)
        for key, value in (("ok", 1), ("ok", False), ("stdout", ""), ("stdout", None),
                           ("stderr", None), ("exitCode", False), ("exitCode", 2), ("value", None)):
            status = valid_status()
            status["cases"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)

    def test_cases_must_match_all_contract_expectations(self):
        for cases in ([], None, {}, valid_status()["cases"][:-1], valid_status()["cases"] + [{}]):
            status = valid_status()
            status["cases"] = cases
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)
        status = valid_status()
        status["cases"][0]["value"] = {"unexpected": "wrong feature result"}
        with self.assertRaises(ValueError):
            runner.validate_status(status, CONTRACT, False)

    def test_every_fixture_failure_assertion_is_required(self):
        for section in ("feature", "execution"):
            for assertion in CONTRACT[section]["assertions"]:
                if "equals" not in assertion:
                    continue
                status = valid_status()
                tokens = assertion["path"].split("/")[1:]
                destination = status
                for token in tokens[:-1]:
                    destination = destination[int(token)] if isinstance(destination, list) else destination[token]
                del destination[tokens[-1]]
                with self.subTest(path=assertion["path"]), self.assertRaises(ValueError):
                    runner.validate_status(status, CONTRACT, False)

    def test_all_nine_typed_rejections_are_exact_prelaunch_results(self):
        for index in range(9):
            for change in ("ok", "message", "exitCode", "missing"):
                status = valid_status()
                result = status["rejectedArguments"][index]["result"]
                if change == "ok":
                    result["ok"] = True
                elif change == "message":
                    result["error"]["message"] = "wrong rejection"
                elif change == "exitCode":
                    result["exitCode"] = 0
                else:
                    del status["rejectedArguments"][index]["result"]
                with self.subTest(index=index, change=change), self.assertRaises(ValueError):
                    runner.validate_status(status, CONTRACT, False)
        status = valid_status()
        status["rejectedArguments"].pop()
        with self.assertRaises(ValueError):
            runner.validate_status(status, CONTRACT, False)

    def test_nul_and_null_inputs_reject_before_launch(self):
        for index in range(3):
            status = valid_status()
            status["rejectedNul"][index]["stdout"] = ""
            with self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)
        for path in ("missing", "unsupported", "launch"):
            status = valid_status()
            status[path]["exitCode"] = 0
            with self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)

    def test_malformed_boolean_protocol_results_require_error_and_retained_status(self):
        for index in range(2):
            for key in ("ok", "stdout", "stderr", "exitCode", "error"):
                status = valid_status()
                del status["protocolTypes"][index][key]
                with self.subTest(index=index, key=key), self.assertRaises(ValueError):
                    runner.validate_status(status, CONTRACT, False)
            status = valid_status()
            status["protocolTypes"][index]["value"] = {"protocol": True}
            with self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)

    def test_malformed_null_and_scalar_payloads_cannot_be_success_shaped(self):
        for path in ("/protocolNull", *(f"/protocolScalars/{index}" for index in range(5))):
            status = valid_status()
            runner._pointer(status, path)["ok"] = True
            with self.subTest(path=path), self.assertRaises(ValueError):
                runner.validate_status(status, CONTRACT, False)

    def test_execution_successes_preserve_streams_without_feature_value(self):
        status = valid_status()
        status["fileArgument"]["stderr"] = "diagnostic, not a failure"
        runner.validate_status(status, CONTRACT, False)
        status["folderArgument"]["error"] = {"code": "hidden_failure"}
        with self.assertRaises(ValueError):
            runner.validate_status(status, CONTRACT, False)

    def test_new_feature_contract_without_greeting_or_six_cases(self):
        contract = copy.deepcopy(CONTRACT)
        contract["feature"] = {
            "casesPath": "/converted",
            "cases": [{"stdout": "converted\n", "stderr": "", "exitCode": 0, "value": {"pages": 4}}],
            "assertions": [{"path": "/invalidDocument/error/code", "equals": "invalid_document"},
                           {"path": "/invalidDocument/ok", "equals": False},
                           {"path": "/invalidDocument/exitCode", "equals": 3}]}
        status = valid_status()
        del status["cases"]
        status["converted"] = [{"ok": True, **contract["feature"]["cases"][0]}]
        status["invalidDocument"] = {"ok": False, "exitCode": 3, "error": {"code": "invalid_document"}}
        runner.validate_status(status, contract, False)

    def test_contract_malformed_and_empty_sections_fail_closed(self):
        for mutation in (
                lambda c: c.update(schema=True),
                lambda c: c["feature"].update(cases=[]),
                lambda c: c["feature"].update(assertions=[]),
                lambda c: c["execution"].update(assertions=[]),
                lambda c: c["feature"]["cases"][0].pop("value"),
                lambda c: c["execution"].update(successPaths=None),
                lambda c: c["execution"]["assertions"].append({"path": "/success", "equal": True})):
            contract = copy.deepcopy(CONTRACT)
            mutation(contract)
            with self.assertRaises(ValueError):
                runner.validate_status(valid_status(), contract, False)

    def test_json_pointers_escape_and_strict_equality(self):
        runner._assertions({"a/b": {"~": [False]}},
                           [{"path": "/a~1b/~0/0", "equals": False}])
        with self.assertRaises(ValueError):
            runner._assertions({"ok": True}, [{"path": "/ok", "equals": 1}])


class HostRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "build" / (runner._host_path_prefix("4D host") + " " + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.root))
        config = json.loads((ROOT / "template.json").read_text())
        config["initialized"] = True
        self.config = config
        (self.root / "template.json").write_text(json.dumps(config), encoding="utf-8")
        shutil.copytree(ROOT / "tests/host", self.root / "tests/host")
        source = self.root / "component" / config["repository"]
        source.mkdir(parents=True)
        (source / "marker").write_text("source", encoding="utf-8")
        (source / "Project/DerivedData").mkdir(parents=True)
        (source / "Project/DerivedData/stale").write_text("compiled source cache")
        self.source = source
        platform = "Windows" if runner.os.name == "nt" else "macOS"
        binary = source / "Resources/bin" / platform / (config["cli"] + (".exe" if runner.os.name == "nt" else ""))
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"unit test native resource")
        self.output = self.root / "build/host-status.json"

    def fake_host(self, args, **kwargs):
        self.assertIsInstance(args, list)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs, {"check": True, "timeout": 120})
        self.assertIn("--startup-method=Smoke", args)
        request_path = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-param=")))
        self.assertIn("日本語", str(request_path))
        if runner.os.name == "nt":
            self.assertNotIn("'", request_path.name)
            self.assertNotIn('"', request_path.name)
        else:
            self.assertIn("'", request_path.name)
            self.assertIn('"', request_path.name)
        self.assertIn(" ", request_path.name)
        host = request_path.parent
        self.assertIn("日本語", host.parent.name)
        if runner.os.name == "nt":
            self.assertNotIn("'", host.parent.name)
            self.assertNotIn('"', host.parent.name)
        else:
            self.assertIn("'", host.parent.name)
            self.assertIn('"', host.parent.name)
        self.assertFalse((host / "environment4d.json").exists())
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(Path(request["executable"]).parent, host)
        self.assertEqual(Path(request["executable"]).read_bytes(), b"unit test native resource")
        self.assertEqual(Path(request["status"]).parent, host)
        self.assertEqual((host / "Components" / (self.config["repository"] + ".4dbase") / "marker").read_text(),
                         "release" if request["expectCompiled"] else "source")
        if not request["expectCompiled"]:
            self.assertFalse((host / "Components" / (self.config["repository"] + ".4dbase") /
                              "Project/DerivedData").exists())
        Path(request["status"]).write_text(json.dumps(valid_status(request["expectCompiled"])),
                                          encoding="utf-8")

    def run_host(self, component=None):
        return runner.test_4d(self.root, "/fake/tool4d", component)

    def test_quotes_are_posix_only_but_unicode_and_spaces_are_universal(self):
        for windows in (True, False):
            prefix = runner._host_path_prefix("host request", windows=windows)
            self.assertIn("日本語", prefix)
            self.assertIn(" ", prefix)
            self.assertEqual("'" in prefix, not windows)
            self.assertEqual('"' in prefix, not windows)

    def test_source_and_unpacked_release_staged_and_cleaned(self):
        release = self.root / "release.4dbase"
        shutil.copytree(self.source, release)
        (release / "marker").write_text("release")
        with patch.object(runner.subprocess, "run", side_effect=self.fake_host):
            self.assertFalse(self.run_host()["compiled"])
            self.assertTrue(self.run_host(release)["compiled"])
        self.assertTrue(self.output.is_file())
        self.assertEqual(list(self.output.parent.glob("4D host *")), [])

    def test_missing_output_cannot_reuse_stale_status(self):
        self.output.parent.mkdir()
        self.output.write_text(json.dumps(valid_status()))
        with patch.object(runner.subprocess, "run"), self.assertRaisesRegex(ValueError, "did not write"):
            self.run_host()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.glob("4D host *")), [])

    def test_malformed_incomplete_and_duplicate_json_fail(self):
        for text in ("not json", "null", "[]", '{"success":true}',
                     '{"success":false,"success":true}', '{"success":NaN}'):
            def malformed(args, **kwargs):
                request = json.loads(Path(next(a.split("=", 1)[1] for a in args if a.startswith("--user-param="))).read_text())
                Path(request["status"]).write_text(text)
            with self.subTest(text=text), patch.object(runner.subprocess, "run", side_effect=malformed):
                with self.assertRaises(ValueError):
                    self.run_host()
            self.assertEqual(list(self.output.parent.glob("4D host *")), [])

    def test_failed_process_propagates_and_cleans_staging(self):
        with patch.object(runner.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["tool4d"])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.run_host()
        self.assertEqual(list(self.output.parent.glob("4D host *")), [])

    def test_uninitialized_template_remains_forbidden(self):
        self.config["initialized"] = False
        (self.root / "template.json").write_text(json.dumps(self.config))
        with patch.object(runner.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "initialize"):
            self.run_host()
        run.assert_not_called()

    def test_symlink_inputs_rejected_before_launch(self):
        for base in (self.source, self.root / "tests/host"):
            link = base / "unsafe"
            link.symlink_to(self.root / "template.json")
            with patch.object(runner.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "symlinks"):
                self.run_host()
            run.assert_not_called()
            link.unlink()
        alias = self.root / "alias.4dbase"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.run_host(alias)

    def test_symlink_build_cannot_stage_outside_root(self):
        other = self.root / "other"
        other.mkdir()
        (self.root / "build").symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.run_host()
        self.assertEqual(list(other.iterdir()), [])

    def test_symlink_persistent_status_rejected_before_mutation(self):
        self.output.parent.mkdir()
        target = self.root / "untouched.json"
        target.write_text("must not modify")
        self.output.symlink_to(target)
        with patch.object(runner.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "symlinks"):
            self.run_host()
        run.assert_not_called()
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(target.read_text(), "must not modify")
        self.assertEqual(list(self.output.parent.glob("4D host *")), [])

    def test_root_symlink_ancestor_rejected_before_mutation(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with patch.object(runner.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "symlinks"):
            runner.test_4d(alias, "/fake/tool4d")
        run.assert_not_called()
        self.assertFalse(self.output.parent.exists())

    def test_symlink_status_output_rejected(self):
        def linked(args, **kwargs):
            request = json.loads(Path(next(a.split("=", 1)[1] for a in args if a.startswith("--user-param="))).read_text())
            Path(request["status"]).symlink_to(self.root / "template.json")
        with patch.object(runner.subprocess, "run", side_effect=linked), self.assertRaisesRegex(ValueError, "symlinks"):
            self.run_host()
