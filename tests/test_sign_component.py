"""Mocked orchestration only: no certificates, Apple services or signing claims."""
from contextlib import contextmanager, ExitStack
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sign_component as signing


SECRETS = {name: "sentinel-" + name for name in signing.SECRET_NAMES}
SECRETS.update(NOTARYTOOL_TEAM_ID="TESTTEAM01",
               APPLE_DEVELOPER_ID_CERTIFICATE="c3ludGhldGlj")
IDENTITY = "A" * 40
IDENTITY_LIST = '  1) ' + IDENTITY + ' "Developer ID Application: Test Only (TESTTEAM01)"\n  1 valid identities found\n'
RECEIPT = {"id": "01234567-89ab-cdef-0123-456789abcdef", "status": "Accepted"}
METADATA = (
    "Authority=Developer ID Application: Test Only (TESTTEAM01)\n"
    "TeamIdentifier=TESTTEAM01\nTimestamp=Nov 6, 2026 at 01:00:00\n"
    "CodeDirectory v=20500 size=123 flags=0x10000(runtime) hashes=1\n"
)


def completed(stdout="", stderr=""):
    return subprocess.CompletedProcess([], 0, stdout, stderr)


def keychain_completed(arguments):
    if "find-identity" in arguments:
        return completed(IDENTITY_LIST)
    if "default-keychain" in arguments:
        return completed('"/Users/runner/Library/Keychains/login.keychain-db"\n')
    return completed()


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix=".sign-tests-", dir=ROOT)
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.component = self.root / "component/SerialPort"
        self.helper = self.component / "Resources/bin/macOS/serial-port"
        self.library = self.component / "Libraries/lib4d-arm64.dylib"
        self.windows = self.component / "Resources/bin/Windows/serial-port.exe"
        for path in (self.helper, self.library):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xcf\xfa\xed\xfe" + b"synthetic unsigned binary")
        self.windows.parent.mkdir(parents=True)
        self.windows.write_bytes(b"synthetic windows executable")

    def test_preflight_names_only_and_no_values(self):
        with self.assertRaises(signing.SigningError) as error:
            signing.preflight({})
        for name in signing.SECRET_NAMES:
            self.assertIn(name, str(error.exception))
        self.assertEqual(signing.preflight(SECRETS), SECRETS)
        with patch.dict(os.environ, {}, clear=True), patch.object(signing, "ROOT", self.root), patch(
                "sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(signing.main(["preflight"]), 2)
        self.assertIn("missing secrets:", stderr.getvalue())
        report = json.loads((self.root / "build/signing-status.json").read_text())
        self.assertEqual(report["result"], "failed")
        self.assertFalse(report["finished"])

    def test_preflight_requires_six_inputs_and_valid_team_only(self):
        self.assertEqual(len(signing.SECRET_NAMES), 6)
        for team in ("person@example.com", "-", "short"):
            with self.subTest(team=team), self.assertRaisesRegex(signing.SigningError, "NOTARYTOOL_TEAM_ID"):
                signing.preflight(dict(SECRETS, NOTARYTOOL_TEAM_ID=team))
        self.assertEqual(signing.preflight({"NOTARYTOOL_TEAM_ID": "TESTTEAM01"}, verify_only=True),
                         {"NOTARYTOOL_TEAM_ID": "TESTTEAM01"})

    def test_process_failure_never_exposes_arguments_or_output(self):
        sentinel = "PRIVATE-NOTARY-PASSWORD"
        failures = (
            subprocess.CalledProcessError(1, ["security", sentinel], output=sentinel, stderr=sentinel),
            subprocess.TimeoutExpired(["security", sentinel], 1, output=sentinel),
            OSError(sentinel),
        )
        for failure in failures:
            with patch.object(signing.subprocess, "run", side_effect=failure):
                with self.assertRaises(signing.SigningError) as error:
                    signing.run(["/usr/bin/security", sentinel])
                self.assertEqual(str(error.exception), "security execution failed")
                self.assertTrue(error.exception.__suppress_context__)
        with patch.object(signing.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["security", sentinel], 4, sentinel, sentinel)) as mock:
            with patch.dict(os.environ, SECRETS), self.assertRaisesRegex(signing.SigningError, "status 4") as error:
                signing.run(["/usr/bin/security", sentinel])
            self.assertNotIn(sentinel, str(error.exception))
            self.assertFalse(set(mock.call_args.kwargs["env"]) & set(signing.SECRET_NAMES))
            self.assertNotIn("shell", mock.call_args.kwargs)

    def signature_run(self, arguments, **kwargs):
        if "-archs" in arguments:
            return completed("x86_64 arm64\n")
        if "--display" in arguments:
            return completed(stderr=METADATA)
        return completed()

    def test_signature_strict_requirement_and_all_slices(self):
        with patch.object(signing, "run", side_effect=self.signature_run) as mock:
            value = signing.verify_signature(self.helper, "TESTTEAM01", runtime=True)
        self.assertTrue(value["developer_id"])
        commands = [call.args[0] for call in mock.call_args_list]
        self.assertEqual(len([command for command in commands if "--display" in command]), 2)
        self.assertIn("--strict", commands[0])
        self.assertIn("--all-architectures", commands[0])
        self.assertIn("anchor apple generic", commands[0][commands[0].index("-R") + 1])
        self.assertIn("100.6.1.13", commands[0][commands[0].index("-R") + 1])
        self.assertIn("100.6.2.6", commands[0][commands[0].index("-R") + 1])
        self.assertTrue(commands[0][commands[0].index("-R") + 1].startswith("="))

    def test_signature_rejects_bad_metadata(self):
        bad = (
            "", METADATA.replace("TESTTEAM01", "WRONGTEAM0"),
            METADATA.replace("Authority=Developer ID Application:", "Authority=Apple Development:"),
            METADATA.replace("Timestamp=Nov 6, 2026 at 01:00:00", "Timestamp=none"),
            METADATA.replace("Timestamp=Nov 6, 2026 at 01:00:00\n", ""),
            METADATA + "Signature=adhoc\n",
            METADATA.replace("0x10000(runtime)", "0x0(none)"),
        )
        for metadata in bad:
            def response(arguments, **kwargs):
                return completed("arm64\n") if "-archs" in arguments else completed(stderr=metadata)
            with self.subTest(metadata=metadata), patch.object(signing, "run", side_effect=response):
                with self.assertRaises(signing.SigningError):
                    signing.verify_signature(self.helper, "TESTTEAM01", runtime=True)

    def test_signature_rejects_unsigned_and_missing_universal_slice(self):
        with patch.object(signing, "run", side_effect=signing.SigningError("codesign exited with status 1")):
            with self.assertRaisesRegex(signing.SigningError, "codesign"):
                signing.verify_signature(self.helper, "TESTTEAM01", runtime=True)
        with patch.object(signing, "run", return_value=completed("arm64\n")):
            with self.assertRaisesRegex(signing.SigningError, "architectures"):
                signing.verify_signature(self.helper, "TESTTEAM01", runtime=True)

    def test_signing_uses_explicit_keychain_timestamp_runtime_not_deep(self):
        with patch.object(signing, "run", return_value=completed()) as mock, patch.object(
                signing, "verify_signature", return_value={"verified": True}):
            item = signing.sign_file(self.helper, self.component, "owned.keychain", IDENTITY,
                                     "TESTTEAM01", runtime=True)
        command = mock.call_args.args[0]
        self.assertEqual(command[command.index("--sign") + 1], IDENTITY)
        self.assertIn("--keychain", command)
        self.assertIn("--timestamp", command)
        self.assertIn("runtime", command)
        self.assertNotIn("--deep", command)
        self.assertNotIn("--entitlements", command)
        self.assertEqual(item["before_sha256"], signing.sha256(self.helper))

    def test_notary_acceptance_uuid_and_timeout_required(self):
        for value in ({"id": RECEIPT["id"], "status": status} for status in ("Invalid", "Rejected", "In Progress", "accepted", None)):
            with self.assertRaises(signing.SigningError):
                signing.notarization_receipt(value)
        for identifier in ("", "not-a-uuid", 17, None):
            with self.assertRaises(signing.SigningError):
                signing.notarization_receipt(dict(RECEIPT, id=identifier))
        archive = self.root / "SerialPort.zip"
        archive.write_bytes(b"synthetic archive")
        log = {**RECEIPT, "jobId": RECEIPT["id"], "statusCode": 0,
               "sha256": signing.sha256(archive), "issues": None}
        with patch.object(signing, "run", side_effect=[
                completed(json.dumps(RECEIPT)), completed(json.dumps(log))]) as mock:
            receipt = signing.notarize(archive, "chain", "profile")
            self.assertEqual(receipt["id"], RECEIPT["id"])
            self.assertTrue(receipt["log_checked"])
            self.assertEqual(receipt["archive_sha256"], signing.sha256(archive))
        command = mock.call_args_list[0].args[0]
        self.assertIn("--wait", command)
        self.assertEqual(command[command.index("--timeout") + 1], "20m")
        self.assertEqual(mock.call_args_list[0].kwargs["timeout"], 1260)
        self.assertIn("log", mock.call_args_list[1].args[0])
        with patch.object(signing, "run", return_value=completed("not JSON PRIVATE")):
            with self.assertRaisesRegex(signing.SigningError, "^invalid notarization response$"):
                signing.notarize(self.root / "SerialPort.zip", "chain", "profile")

    def test_inventory_exact_matches_and_hashes(self):
        inventory = [{"path": path.relative_to(self.component).as_posix(),
                      "before_sha256": signing.sha256(path), "after_sha256": signing.sha256(path)}
                     for path in (self.helper, self.library)]
        with patch.object(signing, "verify_signature"):
            signing.check_inventory(self.component, "serial-port", inventory, "TESTTEAM01")
            for entries in ([], inventory[:1], inventory + inventory[:1],
                            [dict(inventory[0], after_sha256="0" * 64), inventory[1]]):
                with self.assertRaises(signing.SigningError):
                    signing.check_inventory(self.component, "serial-port", entries, "TESTTEAM01")
            extra = self.component / "Libraries/also-mach-o"
            extra.write_bytes(self.library.read_bytes())
            with self.assertRaisesRegex(signing.SigningError, "inventory"):
                signing.check_inventory(self.component, "serial-port", inventory, "TESTTEAM01")
            extra.unlink()
            self.library.write_bytes(b"not Mach-O")
            with self.assertRaisesRegex(signing.SigningError, "lib4d"):
                signing.expected_files(self.component, "serial-port")

    def test_compiler_intermediates_are_not_distribution_signing_surfaces(self):
        intermediate = self.component / "userPreferences.runner/CompilerIntermediateFiles/arm64/$constants.o"
        intermediate.parent.mkdir(parents=True)
        intermediate.write_bytes(self.library.read_bytes())
        self.assertEqual(set(signing.expected_files(self.component, "serial-port")),
                         {self.helper, self.library})
        unexpected = self.component / "Resources/unregistered-native-code"
        unexpected.write_bytes(self.library.read_bytes())
        with self.assertRaisesRegex(signing.SigningError, "unexpected Mach-O"):
            signing.expected_files(self.component, "serial-port")

    def test_symlink_inputs_and_parents_rejected(self):
        link = self.root / "linked"
        link.symlink_to(self.component, target_is_directory=True)
        with self.assertRaises(signing.SigningError):
            signing.safe_path(link / "Resources/bin/macOS/serial-port")
        (self.component / "Libraries/linked").symlink_to(self.helper)
        with self.assertRaises(signing.SigningError):
            signing.macho_files(self.component)

    def test_private_keychain_cleanup_on_success_and_failure(self):
        for failure_at in (None, "import", "set-key-partition-list", "find-identity", "store-credentials", "create-keychain"):
            commands = []
            def response(arguments, **kwargs):
                commands.append(arguments)
                if "import" in arguments:
                    certificate = Path(arguments[arguments.index("import") + 1])
                    self.assertEqual(certificate.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(certificate.parent.stat().st_mode & 0o777, 0o700)
                if failure_at and failure_at in arguments:
                    raise signing.SigningError("synthetic phase failure")
                return keychain_completed(arguments)
            with patch.object(signing, "run", side_effect=response):
                try:
                    with signing.keychain(self.root, SECRETS) as (chain, profile, identity):
                        self.assertFalse((chain.parent / "identity.p12").exists())
                        self.assertTrue(profile.startswith("serial-port-"))
                        self.assertEqual(identity, IDENTITY)
                except signing.SigningError:
                    self.assertIsNotNone(failure_at)
            self.assertIn("delete-keychain", [command[1] for command in commands])
            self.assertFalse(list((self.root / "build").glob(".signing-*")))
            self.assertFalse(any("default-keychain" in command and "-s" in command for command in commands))

    def test_keychain_cleanup_when_body_fails(self):
        with patch.object(signing, "run", side_effect=keychain_completed) as mock:
            with self.assertRaisesRegex(RuntimeError, "body failed"):
                with signing.keychain(self.root, SECRETS):
                    raise RuntimeError("body failed")
        self.assertIn("delete-keychain", [call.args[0][1] for call in mock.call_args_list])
        self.assertFalse(list((self.root / "build").glob(".signing-*")))

    def test_keychain_search_list_is_restored_only_if_changed(self):
        baseline = '"/Users/runner/Library/Keychains/login.keychain-db"\n'
        queries = 0
        commands = []
        def response(arguments, **kwargs):
            nonlocal queries
            commands.append(arguments)
            if "list-keychains" in arguments and "-s" not in arguments:
                queries += 1
                return completed(baseline if queries == 1 else baseline + '"created.keychain-db"\n')
            return keychain_completed(arguments)
        with patch.object(signing, "run", side_effect=response):
            with signing.keychain(self.root, SECRETS):
                pass
        self.assertIn(["/usr/bin/security", "list-keychains", "-d", "user",
                       "-s", "/Users/runner/Library/Keychains/login.keychain-db"], commands)

    def test_isolated_keychain_is_explicitly_searchable_without_dropping_existing_chains(self):
        baseline = ["/Users/runner/Library/Keychains/login.keychain-db",
                    "/Users/runner/Library/Keychains/existing.keychain-db"]
        search = list(baseline)
        commands = []
        def response(arguments, **kwargs):
            commands.append(arguments)
            if "list-keychains" in arguments:
                if "-s" in arguments:
                    search[:] = [str(path) for path in arguments[arguments.index("-s") + 1:]]
                return completed("".join('"' + path + '"\n' for path in search))
            return keychain_completed(arguments)
        with patch.object(signing, "run", side_effect=response):
            with signing.keychain(self.root, SECRETS) as (chain, _, _):
                self.assertEqual(search, [str(chain), *baseline])
                self.assertIn(["/usr/bin/security", "list-keychains", "-d", "user",
                               "-s", chain, *baseline], commands)
        self.assertEqual(search, baseline)

    def test_keychain_cleanup_error_is_not_success(self):
        def response(arguments, **kwargs):
            if "delete-keychain" in arguments:
                raise signing.SigningError("security exited with status 1")
            return keychain_completed(arguments)
        with patch.object(signing, "run", side_effect=response):
            with self.assertRaisesRegex(signing.SigningError, "security"):
                with signing.keychain(self.root, SECRETS):
                    pass
        self.assertFalse(list((self.root / "build").glob(".signing-*")))

    def orchestration_mocks(self, stack, events):
        config = {"repository": "SerialPort", "cli": "serial-port", "version": "0.1.0"}
        mocks = {
            "load_config": lambda *a, **k: config,
            "commit": lambda *a: "a" * 40,
            "source_digest": lambda *a: "b" * 64,
            "preflight": lambda *a, **k: SECRETS,
            "require_tool4d_host": lambda: None,
            "tool_identity": lambda *a: {"version": "test", "binary_sha256": "c" * 64},
            "assemble": lambda *a: events.append("assemble"),
            "validate_resources": lambda *a: (self.helper, self.windows),
            "smoke_test": lambda *a: events.append("smoke"),
            "verify_signature": lambda *a, **k: {"developer_id": True},
        }
        @contextmanager
        def fake_keychain(*args):
            events.append("keychain")
            try:
                yield "chain", "profile", IDENTITY
            finally:
                events.append("cleanup")
        mocks["keychain"] = fake_keychain
        def sign(path, component, *a, **k):
            events.append("sign_cli" if path == self.helper else "sign_libraries")
            before = signing.sha256(path)
            path.write_bytes(path.read_bytes() + b"signed")
            return {"path": path.relative_to(component).as_posix(), "before_sha256": before,
                    "after_sha256": signing.sha256(path)}
        mocks["sign_file"] = sign
        def compile_outputs(root, tool, *, postprocess_outputs):
            events.append("compile")
            postprocess_outputs(self.component)
            events.append("compile_marker")
            return {"input_sha256": "input", "output_sha256": "output"}
        mocks["compile_component"] = compile_outputs
        def pack(*a, **k):
            events.append("pack")
            path = self.root / "dist/SerialPort.zip"
            path.write_bytes(b"synthetic signed archive")
            return path
        mocks["package"] = pack
        mocks["unpack_for_test"] = lambda *a: events.append("unpack") or self.component
        mocks["notarize"] = lambda archive, *a: events.append(
            "dmg_notary" if archive.suffix == ".dmg" else "notary") or {
                **RECEIPT, "archive_sha256": signing.sha256(archive), "log_checked": True, "log_issue_count": 0}
        def create(root, component, name):
            events.append("dmg_create")
            path = root / "dist" / (name + ".dmg")
            path.write_bytes(b"synthetic disk image")
            return path
        mocks["create_dmg"] = create
        def sign_container(dmg, *args):
            events.append("dmg_sign")
            before = signing.sha256(dmg)
            dmg.write_bytes(dmg.read_bytes() + b"signed")
            return {"before_sha256": before, "signed_sha256": signing.sha256(dmg)}
        mocks["sign_dmg"] = sign_container
        def staple(dmg):
            events.append("dmg_staple")
            dmg.write_bytes(dmg.read_bytes() + b"stapled")
        mocks["staple_dmg"] = staple
        mocks["verify_dmg"] = lambda *args: events.append("dmg_verify") or {"stapled_ticket": True}
        @contextmanager
        def mount(*args):
            events.append("dmg_mount")
            try:
                yield self.component
            finally:
                events.append("dmg_detach")
        mocks["mounted_dmg"] = mount
        def host(root, tool, component=None):
            events.append("compiled_host" if component else "source_host")
            return {"success": True, "finished": True, "compiled": component is not None}
        mocks["test_4d"] = host
        for name, value in mocks.items():
            stack.enter_context(patch.object(signing, name, side_effect=value))

    def test_build_order_and_download_verification_no_repack(self):
        events = []
        with ExitStack() as stack:
            self.orchestration_mocks(stack, events)
            report = signing.build_signed(self.root, "miyako/SerialPort", "tool4d")
            self.assertEqual(events, [
                "assemble", "keychain", "sign_cli", "smoke", "source_host", "compile",
                "sign_libraries", "compile_marker", "pack", "unpack", "notary", "unpack",
                "compiled_host", "dmg_create", "dmg_sign", "dmg_notary", "dmg_staple",
                "dmg_verify", "dmg_mount", "compiled_host", "dmg_detach", "cleanup"])
            self.assertTrue(report["finished"])
            self.assertFalse(report["release_ready"])
            self.assertEqual(report["result"], "partial")
            self.assertEqual(report["steps"]["windows_authenticode"]["status"], "not_run")
            serialized = json.dumps(report)
            self.assertNotIn(IDENTITY, serialized)
            for value in SECRETS.values():
                self.assertNotIn(value, serialized)
            events.clear()
            verified = signing.verify_download(self.root, self.root / "dist/SerialPort.zip",
                                               self.root / "dist/SerialPort.signing.json", "tool4d")
            self.assertEqual(events, ["unpack", "compiled_host", "dmg_verify",
                                      "dmg_mount", "compiled_host", "dmg_detach"])
            self.assertEqual(verified["steps"]["downloaded_package_host"]["status"], "passed")
            self.assertEqual(verified["steps"]["downloaded_package_signatures"]["status"], "passed")
            self.assertEqual(verified["steps"]["downloaded_dmg_host"]["status"], "passed")
            self.assertNotEqual(report["dmg_signing"]["signed_sha256"], report["dmg_artifact"]["sha256"])
            self.assertEqual(json.loads((self.root / "build/signing-status.json").read_text()), verified)

    def test_build_failure_is_sanitized_and_unfinished(self):
        with ExitStack() as stack:
            self.orchestration_mocks(stack, [])
            stack.enter_context(patch.object(signing, "notarize", side_effect=ValueError("PRIVATE-CREDENTIAL")))
            with self.assertRaises(signing.SigningError) as error:
                signing.build_signed(self.root, "miyako/SerialPort", "tool4d")
        report = json.loads((self.root / "dist/SerialPort.signing.json").read_text())
        self.assertFalse(report["finished"])
        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["steps"]["notarization"]["status"], "failed")
        self.assertNotIn("PRIVATE-CREDENTIAL", json.dumps(report) + str(error.exception))
        self.assertEqual(report, json.loads((self.root / "build/signing-status.json").read_text()))

    def test_archive_cannot_change_during_notarization(self):
        with ExitStack() as stack:
            self.orchestration_mocks(stack, [])
            def mutate(archive, *args):
                archive.write_bytes(b"changed after submission")
                return RECEIPT
            stack.enter_context(patch.object(signing, "notarize", side_effect=mutate))
            with self.assertRaises(signing.SigningError):
                signing.build_signed(self.root, "miyako/SerialPort", "tool4d")
        report = json.loads((self.root / "dist/SerialPort.signing.json").read_text())
        self.assertFalse(report["finished"])
        self.assertEqual(report["phase"], "post_notarization_integrity")
        self.assertEqual(report["steps"]["compiled_host"]["status"], "not_run")

    def test_download_rejects_commit_hash_receipt_inventory_and_false_host(self):
        with ExitStack() as stack:
            self.orchestration_mocks(stack, [])
            original = signing.build_signed(self.root, "miyako/SerialPort", "tool4d")
            evidence = self.root / "dist/SerialPort.signing.json"
            archive = self.root / "dist/SerialPort.zip"
            changes = (
                {"commit": "wrong"},
                {"artifact": dict(original["artifact"], sha256="0" * 64)},
                {"notarization": dict(RECEIPT, status="In Progress")},
                {"signed_files": original["signed_files"][:1]},
                {"source_host": {"success": True, "finished": True, "compiled": True}},
                {"steps": dict(original["steps"], windows_authenticode={"status": "passed"})},
                {"dmg_artifact": dict(original["dmg_artifact"], sha256="0" * 64)},
                {"dmg_notarization": dict(original["dmg_notarization"], log_checked=False)},
            )
            for change in changes:
                evidence.write_text(json.dumps(dict(original, **change)))
                with self.assertRaises(signing.SigningError):
                    signing.verify_download(self.root, archive, evidence, "tool4d")
            evidence.write_text(json.dumps(original))
            with patch.object(signing, "test_4d", return_value={
                    "success": True, "finished": True, "compiled": False}):
                with self.assertRaises(signing.SigningError):
                    signing.verify_download(self.root, archive, evidence, "tool4d")
            failed = json.loads(evidence.read_text())
            self.assertFalse(failed["finished"])
            self.assertEqual(failed["steps"]["downloaded_package_host"]["status"], "failed")

    def test_host_check_rejects_unfinished_or_wrong_mode(self):
        for value in ({}, {"success": True, "finished": False, "compiled": True},
                      {"success": True, "finished": True, "compiled": False}):
            with patch.object(signing, "test_4d", return_value=value):
                with self.assertRaises(signing.SigningError):
                    signing.host_check(self.root, "tool", self.component)

    def test_notary_log_inspected_even_after_accepted_submission(self):
        digest = "d" * 64
        valid = {"jobId": RECEIPT["id"], "status": "Accepted", "statusCode": 0,
                 "sha256": digest, "issues": None}
        signing.inspect_notary_log(valid, RECEIPT["id"], digest)
        for change in (
                {"jobId": "wrong"}, {"status": "Invalid"}, {"statusCode": 1},
                {"sha256": "wrong"}, {"issues": [{"severity": "error", "message": "PRIVATE"}]},
                {"issues": "PRIVATE"}, {"issues": [{"severity": "unknown"}]}):
            with self.assertRaises(signing.SigningError) as error:
                signing.inspect_notary_log(dict(valid, **change), RECEIPT["id"], digest)
            self.assertNotIn("PRIVATE", str(error.exception))

    def test_dmg_tree_requires_exact_bytes_modes_and_regular_entries(self):
        expected = signing.tree_manifest(self.component)
        self.assertRegex(signing.equivalent_tree(self.component, expected), r"^[a-f0-9]{64}$")
        self.helper.chmod(0o711)
        with self.assertRaisesRegex(signing.SigningError, "modes"):
            signing.equivalent_tree(self.component, expected)
        self.helper.chmod(expected["Resources/bin/macOS/serial-port"]["mode"])
        self.helper.write_bytes(self.helper.read_bytes() + b"changed")
        with self.assertRaises(signing.SigningError):
            signing.equivalent_tree(self.component, expected)
        (self.component / "linked").symlink_to(self.helper)
        with self.assertRaises(signing.SigningError):
            signing.tree_manifest(self.component)

    def test_dmg_creation_stages_only_exact_component(self):
        (self.root / "dist").mkdir()
        def create(arguments, **kwargs):
            self.assertEqual(arguments[:3], ["/usr/bin/hdiutil", "create", "-format"])
            staging = Path(arguments[arguments.index("-srcfolder") + 1])
            self.assertEqual([path.name for path in staging.iterdir()], ["SerialPort.4dbase"])
            self.assertEqual(signing.tree_manifest(staging / "SerialPort.4dbase"),
                             signing.tree_manifest(self.component))
            Path(arguments[-1]).write_bytes(b"synthetic dmg")
            return completed()
        with patch.object(signing, "run", side_effect=create):
            dmg = signing.create_dmg(self.root, self.component, "SerialPort")
        self.assertEqual(dmg, self.root / "dist/SerialPort.dmg")
        self.assertFalse(list((self.root / "build").glob(".dmg-stage-*")))

    def test_dmg_sign_verify_and_stapling_do_not_sign_component_data(self):
        dmg = self.root / "SerialPort.dmg"
        dmg.write_bytes(b"synthetic dmg")
        def response(arguments, **kwargs):
            return completed(stderr=METADATA) if "--display" in arguments else completed()
        with patch.object(signing, "run", side_effect=response) as mock:
            signing.sign_dmg(dmg, "owned.keychain", IDENTITY, "TESTTEAM01")
            signing.staple_dmg(dmg)
            result = signing.verify_dmg(dmg, "TESTTEAM01")
        commands = [call.args[0] for call in mock.call_args_list]
        self.assertTrue(result["stapled_ticket"])
        self.assertTrue(result["gatekeeper_assessment"])
        self.assertIn("--timestamp", commands[0])
        self.assertFalse(any("--entitlements" in command or "--options" in command for command in commands))
        self.assertTrue(any("staple" in command for command in commands))
        self.assertTrue(any("validate" in command for command in commands))
        self.assertTrue(any("context:primary-signature" in command for command in commands))
        self.assertTrue(all(command[-1] == dmg for command in commands))

    def test_dmg_mount_is_readonly_detaches_and_checks_top_level(self):
        commands = []
        def response(arguments, **kwargs):
            commands.append(arguments)
            if "attach" in arguments:
                self.assertIn("-readonly", arguments)
                mountpoint = Path(arguments[arguments.index("-mountpoint") + 1])
                (mountpoint / "SerialPort.4dbase").mkdir()
                (mountpoint / "SerialPort.4dbase/test").write_bytes(b"test")
            if "detach" in arguments:
                mountpoint = Path(arguments[-1])
                (mountpoint / "SerialPort.4dbase/test").unlink()
                (mountpoint / "SerialPort.4dbase").rmdir()
            return completed()
        with patch.object(signing, "run", side_effect=response), patch.object(
                Path, "is_mount", lambda path: path.name == "volume" and path.exists()):
            with self.assertRaisesRegex(RuntimeError, "host failure"):
                with signing.mounted_dmg(self.root, self.root / "SerialPort.dmg", "SerialPort"):
                    raise RuntimeError("host failure")
        self.assertIn("detach", commands[-1])
        self.assertFalse(list((self.root / "build").glob(".dmg-mount-*")))

    def test_dmg_mount_detaches_after_attach_failure_without_recursive_cleanup(self):
        commands = []
        def response(arguments, **kwargs):
            commands.append(arguments)
            if "attach" in arguments:
                raise signing.SigningError("hdiutil exited with status 1")
            return completed()
        with patch.object(signing, "run", side_effect=response):
            with self.assertRaises(signing.SigningError):
                with signing.mounted_dmg(self.root, self.root / "SerialPort.dmg", "SerialPort"):
                    self.fail("attach failure cannot reach host")
        self.assertIn("detach", commands[-1])
        self.assertFalse(list((self.root / "build").glob(".dmg-mount-*")))

    def test_dmg_failure_prevents_signed_package_success(self):
        with ExitStack() as stack:
            self.orchestration_mocks(stack, [])
            stack.enter_context(patch.object(signing, "verify_dmg", side_effect=signing.SigningError("ticket failed")))
            with self.assertRaises(signing.SigningError):
                signing.build_signed(self.root, "miyako/SerialPort", "tool4d")
        report = json.loads((self.root / "dist/SerialPort.signing.json").read_text())
        self.assertFalse(report["finished"])
        self.assertEqual(report["steps"]["compiled_host"]["status"], "passed")
        self.assertEqual(report["steps"]["dmg_verify"]["status"], "failed")

    def test_cli_writes_diagnostic_for_early_untrusted_evidence_failure(self):
        with patch.object(signing, "verify_download", side_effect=ValueError("PRIVATE")), patch(
                "sys.stderr", new_callable=io.StringIO) as stderr:
            result = signing.main(["verify", "--root", str(self.root), "--tool4d", "fake",
                                   "--archive", "missing.zip", "--evidence", "untrusted.json"])
        self.assertEqual(result, 2)
        report = json.loads((self.root / "build/signing-status.json").read_text())
        self.assertEqual(report["phase"], "verify_setup")
        self.assertEqual(report["steps"]["verify_setup"]["status"], "failed")
        self.assertNotIn("PRIVATE", json.dumps(report) + stderr.getvalue())

    def test_report_write_is_atomic_and_removes_owned_pending_file(self):
        destination = self.root / "signing-status.json"
        destination.write_text('{"previous":"intact"}\n')
        with patch.object(signing.os, "replace", side_effect=OSError("cannot replace")):
            with self.assertRaises(OSError):
                signing.save_report(destination, {"result": "failed"})
        self.assertEqual(destination.read_text(), '{"previous":"intact"}\n')
        self.assertFalse(list(self.root.glob(".signing-evidence-*")))

    def stale_resource(self, kind="keychain"):
        prefixes = {"keychain": ".signing-", "stage": ".dmg-stage-", "mount": ".dmg-mount-"}
        directory = self.root / "build" / (prefixes[kind] + "abcdefgh")
        directory.mkdir(parents=True, mode=0o700)
        token = "a" * 32
        marker = directory / ".signing-owned"
        marker.write_text(token)
        marker.chmod(0o600)
        entry = {"kind": kind, "directory": str(directory), "token": token}
        signing.save_report(signing.cleanup_state(self.root), {"schema": 1, "entries": [entry]})
        return directory, entry

    def test_wrapped_certificate_base64_is_accepted_without_logging(self):
        values = dict(SECRETS, APPLE_DEVELOPER_ID_CERTIFICATE=" \tc3lu\ndGhl\r\ndGlj \n")
        def response(arguments, **kwargs):
            if "import" in arguments:
                self.assertEqual(Path(arguments[2]).read_bytes(), b"synthetic")
            return keychain_completed(arguments)
        with patch.object(signing, "run", side_effect=response):
            with signing.keychain(self.root, values):
                pass

    def test_always_cleanup_is_idempotent_credential_free_and_preserves_diagnostics(self):
        directory, entry = self.stale_resource()
        chain = directory / "signing.keychain-db"
        chain.write_bytes(b"synthetic leftover keychain")
        (directory / "identity.p12").write_bytes(b"synthetic leftover certificate")
        diagnostic = self.root / "build/signing-status.json"
        signing.save_report(diagnostic, {"result": "failed", "finished": False,
                                        "phase": "notarization", "steps": {"notarization": {"status": "failed"}}})
        with patch.dict(os.environ, {}, clear=True), patch.object(
                signing, "run", return_value=completed()) as mock, patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(signing.main(["cleanup", "--root", str(self.root)]), 0)
            self.assertEqual(signing.main(["cleanup", "--root", str(self.root)]), 0)
        self.assertEqual([call.args[0] for call in mock.call_args_list],
                         [["/usr/bin/security", "delete-keychain", chain]])
        self.assertFalse(directory.exists())
        self.assertFalse((self.root / "build/.serialport-signing-state").exists())
        report = json.loads(diagnostic.read_text())
        self.assertEqual(report["phase"], "notarization")
        self.assertEqual(report["result"], "failed")
        self.assertEqual(report["steps"]["cleanup"]["status"], "passed")

    def test_always_cleanup_rejects_traversal_outside_paths_duplicates_and_symlinks(self):
        directory, entry = self.stale_resource()
        sentinel = self.root / "user-keychain"
        sentinel.write_bytes(b"preserve")
        bad_entries = (
            [dict(entry, directory=str(self.root / "build/../user-keychain"))],
            [dict(entry, directory=str(sentinel))],
            [dict(entry, token="b" * 32)],
            [entry, entry],
        )
        with patch.object(signing, "run") as mock:
            for entries in bad_entries:
                signing.save_report(signing.cleanup_state(self.root), {"schema": 1, "entries": entries})
                with self.assertRaises(signing.SigningError):
                    signing.cleanup(self.root)
                self.assertEqual(sentinel.read_bytes(), b"preserve")
                self.assertTrue(directory.is_dir())
            signing.save_report(signing.cleanup_state(self.root), {"schema": 1, "entries": [entry]})
            (directory / "linked-keychain").symlink_to(sentinel)
            with self.assertRaises(signing.SigningError):
                signing.cleanup(self.root)
            mock.assert_not_called()

    def test_always_cleanup_detaches_only_registered_mount(self):
        directory, entry = self.stale_resource("mount")
        mountpoint = directory / "volume"
        mountpoint.mkdir()
        active = True
        def response(arguments, **kwargs):
            nonlocal active
            self.assertEqual(arguments, ["/usr/bin/hdiutil", "detach", mountpoint])
            active = False
            return completed()
        with patch.object(Path, "is_mount", lambda path: path == mountpoint and active), patch.object(
                signing, "run", side_effect=response) as mock:
            signing.cleanup(self.root)
        mock.assert_called_once()
        self.assertFalse(directory.exists())

    def test_cleanup_failure_keeps_state_for_retry_and_marks_diagnostic(self):
        directory, entry = self.stale_resource()
        (directory / "signing.keychain-db").write_bytes(b"synthetic keychain")
        with patch.object(signing, "run", side_effect=signing.SigningError("security exited with status 1")), patch(
                "sys.stderr", new_callable=io.StringIO):
            self.assertEqual(signing.main(["cleanup", "--root", str(self.root)]), 2)
        self.assertTrue(directory.exists())
        self.assertEqual(signing.load_cleanup_state(self.root), [entry])
        report = json.loads((self.root / "build/signing-status.json").read_text())
        self.assertEqual(report["steps"]["cleanup"]["status"], "failed")

    def test_cleanup_recovers_owned_interrupted_state_write(self):
        directory, entry = self.stale_resource("stage")
        state = signing.cleanup_state(self.root)
        pending = state.parent / ".signing-evidence-abcdefgh"
        pending.write_text('{"schema":1,"entries":[]}')
        pending.chmod(0o600)
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state.parent.stat().st_mode & 0o777, 0o700)
        for secret in SECRETS.values():
            self.assertNotIn(secret, state.read_text())
        signing.cleanup(self.root)
        self.assertFalse(directory.exists())
        self.assertFalse(state.parent.exists())

    def test_identity_is_derived_only_from_explicit_isolated_keychain_and_team(self):
        chain = self.root / "isolated.keychain-db"
        unrelated = '  2) ' + "B" * 40 + ' "Apple Development: Other (TESTTEAM01)"\n'
        other_team = '  3) ' + "C" * 40 + ' "Developer ID Application: Other (OTHERTEAM0)"\n'
        with patch.object(signing, "run", return_value=completed(
                unrelated + other_team + IDENTITY_LIST)) as mock:
            identity = signing.find_signing_identity(chain, "TESTTEAM01")
        self.assertEqual(identity, IDENTITY)
        self.assertEqual(mock.call_args.args[0],
                         ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning", chain])

    def test_identity_derivation_rejects_ambiguous_wrong_team_and_incompatible_certificates(self):
        wrong_team = IDENTITY_LIST.replace("TESTTEAM01", "OTHERTEAM0")
        wrong_class = IDENTITY_LIST.replace("Developer ID Application:", "Apple Development:")
        for text in ("0 valid identities found\n", wrong_team, wrong_class,
                     IDENTITY_LIST + IDENTITY_LIST.replace(IDENTITY, "B" * 40),
                     IDENTITY_LIST + IDENTITY_LIST, IDENTITY_LIST.replace(IDENTITY, "not-a-sha1")):
            with self.subTest(text=text), patch.object(signing, "run", return_value=completed(text)):
                with self.assertRaises(signing.SigningError) as error:
                    signing.find_signing_identity(self.root / "isolated.keychain-db", "TESTTEAM01")
                self.assertNotIn("Test Only", str(error.exception))
                self.assertNotIn("TESTTEAM01", str(error.exception))

    def test_derived_identity_validation_occurs_after_import_and_before_notary_credentials(self):
        commands = []
        def response(arguments, **kwargs):
            commands.append(arguments)
            return keychain_completed(arguments)
        with patch.object(signing, "run", side_effect=response):
            with signing.keychain(self.root, SECRETS) as (chain, profile, identity):
                self.assertEqual(identity, IDENTITY)
        positions = {name: next(index for index, command in enumerate(commands) if name in command)
                     for name in ("import", "find-identity", "store-credentials")}
        self.assertLess(positions["import"], positions["find-identity"])
        self.assertLess(positions["find-identity"], positions["store-credentials"])

    def test_keychain_default_is_restored_if_changed(self):
        queries = 0
        commands = []
        def response(arguments, **kwargs):
            nonlocal queries
            commands.append(arguments)
            if "default-keychain" in arguments and "-s" not in arguments:
                queries += 1
                return completed('"/Users/runner/Library/Keychains/' +
                                 ("login.keychain-db" if queries == 1 else "changed.keychain-db") + '"\n')
            return keychain_completed(arguments)
        with patch.object(signing, "run", side_effect=response):
            with signing.keychain(self.root, SECRETS):
                pass
        self.assertIn(["/usr/bin/security", "default-keychain", "-d", "user", "-s",
                       "/Users/runner/Library/Keychains/login.keychain-db"], commands)

    def test_absent_default_keychain_fails_before_creating_or_importing(self):
        def response(arguments, **kwargs):
            return completed()
        with patch.object(signing, "run", side_effect=response) as mock:
            with self.assertRaisesRegex(signing.SigningError, "existing default keychain"):
                with signing.keychain(self.root, SECRETS):
                    self.fail("no default must fail before importing")
        commands = [call.args[0] for call in mock.call_args_list]
        self.assertFalse(any("create-keychain" in command or "import" in command for command in commands))

    def test_codesign_failure_reports_only_fixed_operation_category_and_exit_code(self):
        examples = (
            ("errSecInternalComponent", "keychain_internal_component"),
            ("unable to build chain to self-signed root; errSecInternalComponent", "certificate_chain_unavailable"),
            ("code failed to satisfy specified code requirement(s)", "requirement_failed"),
            ("The timestamp service is not available", "timestamp_service_unavailable"),
            ("no identity found", "signing_identity_unavailable"),
            ("User interaction is not allowed", "keychain_interaction_denied"),
            ("code or signature have been modified", "invalid_signature_or_format"),
            ("Usage: codesign ...", "usage_syntax"),
        )
        private = "PRIVATE-IDENTITY-TEAM-PASSWORD-STDERR"
        for flag, operation in (("--sign", "sign"), ("--verify", "verify"), ("--display", "display")):
            for stderr, category in examples:
                result = subprocess.CompletedProcess([], 1, private, stderr + "\n" + private)
                with self.subTest(operation=operation, category=category), patch.object(
                        signing.subprocess, "run", return_value=result):
                    with self.assertRaises(signing.SigningError) as error:
                        signing.run(["/usr/bin/codesign", flag, private])
                diagnostic = error.exception.diagnostic
                self.assertEqual(diagnostic, {"program": "codesign", "operation": operation,
                                              "category": category, "exit_code": 1})
                self.assertNotIn(private, str(error.exception) + json.dumps(diagnostic))
                self.assertTrue(error.exception.__suppress_context__)

    def test_unknown_codesign_failure_records_stderr_digest_never_text(self):
        private = "PRIVATE-UNKNOWN-STDERR"
        result = subprocess.CompletedProcess([], 7, "PRIVATE-STDOUT", private)
        with patch.object(signing.subprocess, "run", return_value=result):
            with self.assertRaises(signing.SigningError) as error:
                signing.run(["/usr/bin/codesign", "--verify", "PRIVATE-PATH"])
        diagnostic = error.exception.diagnostic
        self.assertEqual(diagnostic["operation"], "verify")
        self.assertEqual(diagnostic["category"], "unknown")
        self.assertEqual(diagnostic["exit_code"], 7)
        self.assertEqual(diagnostic["stderr_sha256"], signing.hashlib.sha256(private.encode()).hexdigest())
        self.assertNotIn(private, str(error.exception) + json.dumps(diagnostic))
        self.assertNotIn("PRIVATE-STDOUT", str(error.exception))
        self.assertNotIn("PRIVATE-PATH", str(error.exception))

    def test_codesign_diagnostic_is_persisted_at_the_actual_failing_phase(self):
        report = {"steps": {}}
        output = self.root / "build/signing-status.json"
        result = subprocess.CompletedProcess([], 1, "", "PRIVATE-KEYCHAIN: errSecInternalComponent")
        with patch.object(signing.subprocess, "run", return_value=result):
            with self.assertRaises(signing.SigningError):
                signing.step(report, output, "sign_cli", lambda: signing.run(
                    ["/usr/bin/codesign", "--sign", "PRIVATE-IDENTITY", "PRIVATE-FILE"]))
        saved = json.loads(output.read_text())
        self.assertEqual(saved["steps"]["sign_cli"]["failure"], {
            "program": "codesign", "operation": "sign", "category": "keychain_internal_component", "exit_code": 1})
        self.assertNotIn("PRIVATE", output.read_text())


if __name__ == "__main__":
    unittest.main()
