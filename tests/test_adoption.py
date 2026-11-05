"""Deterministic adoption safety checks; actual tool4d is exercised separately."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from initialize import initialize
from initialize import github_repository
from rename_feature import rename_feature
from build_provenance import record, verify
from build_cli import smoke_test
from doctor import doctor
import accept_local
import ci_config


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        if json.loads((ROOT / "template.json").read_text())["initialized"]:
            self.skipTest("adoption fixtures need original starters")
        self.temporary = tempfile.TemporaryDirectory(prefix="adoption 日本語 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("component", "cli-starters", "tests/host"):
            shutil.copytree(ROOT / name, self.root / name)
        for name in ("template.json", "package.json", "README.md", "LICENSE", "AGENTS.md", ".gitattributes"):
            shutil.copy2(ROOT / name, self.root / name)
        initialize(self.root, "UuidTest", "UuidTest", "uuid-test", "go", "Feature decisions remain required")

    def test_case_identity_rename_idempotence_and_no_token_rewrite(self):
        classes = self.root / "component/UuidTest/Project/Sources/Classes"
        original = (classes / "Greeting.4dm").read_text()
        (classes / "Greeting.4dm").write_text(original + '\nvar $other : cs:C1710.Greeting\n'
                                             '// cs.Greeting remains a comment\n'
                                             '$literal:="cs.Greeting"\n')
        unrelated = classes / "_Process.4dm"
        previous = unrelated.read_bytes()
        self.assertTrue(rename_feature(self.root, "Identifier"))
        self.assertFalse(rename_feature(self.root, "Identifier"))
        self.assertIn("cs:C1710.Identifier", (classes / "Identifier.4dm").read_text())
        self.assertIn('$literal:="cs.Greeting"', (classes / "Identifier.4dm").read_text())
        self.assertIn("// cs.Greeting remains a comment", (classes / "Identifier.4dm").read_text())
        self.assertEqual(unrelated.read_bytes(), previous)
        self.assertNotIn("cs.UuidTest.Greeting", (self.root / "tests/host/Project/Sources/Methods/Smoke.4dm").read_text())
        self.assertEqual(ci_config.load_config(self.root)["feature_class"], "Identifier")
        self.assertTrue((self.root / "component/UuidTest/Documentation/Classes/Identifier.md").is_file())
        self.assertFalse((classes / "Greeting.4dm").exists())

    def test_rename_rejects_invalid_collision_symlink_without_changes(self):
        for name in ("../escape", "_Internal", "4D", "cs", "x" * 32):
            with self.subTest(name=name), self.assertRaises(ValueError):
                rename_feature(self.root, name)
        classes = self.root / "component/UuidTest/Project/Sources/Classes"
        (classes / "Taken.4dm").write_text("")
        with self.assertRaisesRegex(ValueError, "collides"):
            rename_feature(self.root, "taken")
        if os.name == "posix":
            doc = self.root / "component/UuidTest/Documentation/Classes/Greeting.md"
            doc.unlink()
            doc.symlink_to(self.root / "README.md")
            original = (classes / "Greeting.4dm").read_bytes()
            with self.assertRaisesRegex(ValueError, "symlinks"):
                rename_feature(self.root, "Identifier")
            self.assertEqual(original, (classes / "Greeting.4dm").read_bytes())

    def test_same_source_manifest_rejects_tampering(self):
        executable = self.root / "binary"
        executable.write_bytes(b"build result")
        record(self.root, "macos-arm64", executable, True, "test")
        verify(self.root, "macos-arm64", executable)
        executable.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "stale"):
            verify(self.root, "macos-arm64", executable)
        record(self.root, "macos-arm64", executable, False, "test")
        source = self.root / "cli/main.go"
        source.write_text(source.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            verify(self.root, "macos-arm64", executable)

    def test_doctor_uninitialized_and_wrong_host_not_ready(self):
        with patch("doctor.native_target", side_effect=ValueError("unsupported")), patch(
                "doctor.require_tool4d_host", side_effect=ValueError("ARM64 only")):
            report = doctor(self.root)
        self.assertFalse(report["ready_for_local_acceptance"])
        self.assertFalse(report["release_ready"])
        self.assertEqual(next(item for item in report["checks"] if item["name"] == "native_cli_host")["status"], "not_run")

    def test_acceptance_blocker_writes_explicit_failure_evidence(self):
        with patch.object(accept_local, "require_tool4d_host", side_effect=ValueError("wrong host")):
            with self.assertRaisesRegex(ValueError, "wrong host"):
                accept_local.accept(self.root, "owner/UuidTest", "/missing/tool4d")
        report = json.loads((self.root / "build/acceptance.json").read_text())
        self.assertEqual(report["result"], "failed")
        self.assertFalse(report["finished"])
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["steps"]["compiled_host"]["status"], "not_run")

    def test_cli_contract_vectors_explicit_streams_and_status(self):
        contract = self.root / "contract.json"
        contract.write_text(json.dumps({"schema": 1, "cases": [
            {"args": ["success"], "exitCode": 0, "stdout": "value\n", "stderr": ""},
            {"args": ["feature"], "exitCode": 1, "stdout": "", "stderr": "failed\n"}]}))
        with patch("build_cli.subprocess.run", side_effect=[
                subprocess.CompletedProcess([], 0, b"value\n", b""),
                subprocess.CompletedProcess([], 1, b"", b"failed\n")]):
            smoke_test(self.root / "binary", "0.1.0", contract)
        with patch("build_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            with self.assertRaisesRegex(ValueError, "contract failed"):
                smoke_test(self.root / "binary", "0.1.0", contract)

    def test_downloader_failure_and_nonvendor_url(self):
        with self.assertRaises(ValueError):
            ci_config.download_tool4d(self.root, url="https://untrusted.invalid/tool4d.zip", digest="0" * 64)
        with patch.object(ci_config.urllib.request, "urlopen", side_effect=urllib.error.URLError("network failure")):
            with self.assertRaises(urllib.error.URLError):
                ci_config.download_tool4d(self.root, url=ci_config.tool_pin(self.root)[0],
                                         digest=ci_config.tool_pin(self.root)[1])
        self.assertFalse((self.root / ".tools/tool4d.download").exists())
        with patch.object(ci_config.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(ValueError, "macOS ARM64"):
                ci_config.require_tool4d_host()

    def test_package_tool_defaults_and_complete_override_precedence(self):
        with patch.dict(os.environ, {}, clear=True):
            url, digest = ci_config.resolve_tool_pin(self.root)
        self.assertIn("/20.x/20.7/101905/mac/tool4d_arm64.tar.xz", url)
        self.assertEqual(digest, "40a5d177a4f73165f52abf6ec8000249d81411c41353ccf258cece8e74639261")
        with patch.dict(os.environ, {"TOOL4D_URL": "", "TOOL4D_SHA256": ""}, clear=True):
            self.assertEqual(ci_config.resolve_tool_pin(self.root), (url, digest))
        with patch.dict(os.environ, {"TOOL4D_URL": url, "TOOL4D_SHA256": "1" * 64}, clear=True):
            self.assertEqual(ci_config.resolve_tool_pin(self.root)[1], "1" * 64)
            self.assertEqual(ci_config.resolve_tool_pin(self.root, url=url, digest=digest), (url, digest))
        for environment in ({"TOOL4D_URL": url}, {"TOOL4D_SHA256": digest}):
            with patch.dict(os.environ, environment, clear=True), self.assertRaisesRegex(ValueError, "together"):
                ci_config.resolve_tool_pin(self.root)
        with self.assertRaisesRegex(ValueError, "together"):
            ci_config.resolve_tool_pin(self.root, url="")
        manifest = self.root / "package.json"
        data = json.loads(manifest.read_text())
        for field, value in (("build", "latest"), ("sha256", "bad"), ("arch", "x64")):
            altered = dict(data, tool=dict(data["tool"], **{field: value}))
            manifest.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, "fixed numeric"):
                ci_config.tool_pin(self.root)
        manifest.write_text(json.dumps(data))

    def test_git_remote_identity_ignores_worktree_basename_and_does_not_emit_tokens(self):
        results = [subprocess.CompletedProcess([], 0, str(self.root) + "\n", ""),
                   subprocess.CompletedProcess([], 0, "https://secret-token@github.com/Owner/UuidTest.git\n", "")]
        with patch("initialize.subprocess.run", side_effect=results):
            self.assertEqual(github_repository(self.root), "Owner/UuidTest")
        results[1] = subprocess.CompletedProcess([], 0, "git@github.com:Owner/UuidTest.git\n", "")
        with patch("initialize.subprocess.run", side_effect=results):
            self.assertEqual(github_repository(self.root), "Owner/UuidTest")

    def test_version_only_cli_contract_cannot_pass_feature_acceptance(self):
        contract = self.root / "version-only.json"
        contract.write_text(json.dumps({"schema": 1, "cases": [
            {"args": ["--version"], "exitCode": 0, "stdout": "", "stderr": ""}]}))
        with self.assertRaisesRegex(ValueError, "feature success and failure"):
            smoke_test(self.root / "binary", "0.1.0", contract)

    def test_nondeterministic_cli_stream_patterns_are_full_utf8_matches(self):
        contract = self.root / "patterns.json"
        contract.write_text(json.dumps({"schema": 1, "cases": [
            {"args": ["generate"], "exitCode": 0, "stdoutPattern": "[a-z]{3}-[0-9]{3}\\n", "stderr": ""},
            {"args": ["bad"], "exitCode": 2, "stdout": "", "stderr": "invalid\n"}]}))
        with patch("build_cli.subprocess.run", side_effect=[
                subprocess.CompletedProcess([], 0, b"abc-123\n", b""),
                subprocess.CompletedProcess([], 2, b"", b"invalid\n")]):
            smoke_test(self.root / "binary", "0.1.0", contract)
        with patch("build_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"prefix abc-123\n", b"")):
            with self.assertRaisesRegex(ValueError, "contract failed"):
                smoke_test(self.root / "binary", "0.1.0", contract)

    def test_local_pipeline_is_partial_and_preserves_final_evidence(self):
        executable = self.root / "tool4d"
        executable.write_bytes(b"unit test placeholder")
        archive = self.root / "dist/UuidTest.zip"
        archive.parent.mkdir()
        archive.write_bytes(b"unit test archive placeholder")
        host_calls = []
        def host(root, tool, component=None):
            host_calls.append("compiled" if component else "source")
            return {"success": True, "finished": True, "compiled": component is not None}
        with patch.object(accept_local, "require_tool4d_host"), patch.object(
                accept_local, "native_target", return_value="macos-arm64"), patch.object(
                accept_local, "build"), patch.object(accept_local, "assemble"), patch.object(
                accept_local, "validate_resources"), patch.object(accept_local, "test_4d", side_effect=host), patch.object(
                accept_local, "compile_component", return_value={"success": True}), patch.object(
                accept_local, "package", return_value=archive), patch.object(
                accept_local, "unpack_for_test", return_value=self.root / "release.4dbase"), patch.object(
                accept_local.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "20.7 build 20.101905\n", "")):
            report = accept_local.accept(self.root, "owner/UuidTest", executable)
        self.assertEqual(host_calls, ["source", "compiled"])
        self.assertTrue(report["finished"])
        self.assertEqual(report["result"], "partial")
        self.assertFalse(report["release_ready"])
        self.assertEqual(report["steps"]["cli_test_windows-x64"]["status"], "not_run")
        saved = json.loads(archive.with_suffix(".acceptance.json").read_text())
        self.assertEqual(saved["artifact"], report["artifact"])

    @unittest.skipUnless(os.environ.get("TOOL4D_INTEGRATION"), "set TOOL4D_INTEGRATION to run real macOS ARM64 acceptance")
    def test_real_initialized_renamed_source_and_compiled_host(self):
        tool = Path(os.environ["TOOL4D_INTEGRATION"]).resolve()
        for name in ("scripts", "tooling", "assets", "tests", ".github"):
            shutil.copytree(ROOT / name, self.root / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", ".cli-test*",
                                                          *(("host",) if name == "tests" else ())))
        rename_feature(self.root, "Identifier")
        destination = ROOT / "build/adoption-evidence"
        if any(path.is_symlink() for path in [destination, *destination.parents]):
            self.fail("adoption evidence destination must not traverse symlinks")
        destination.mkdir(parents=True, exist_ok=True)
        try:
            report = accept_local.accept(self.root, "fixture/UuidTest", tool, cross=True)
        finally:
            for name in ("acceptance.json", "host-status.json", "compile-status.json",
                         "source-host-status.json", "compiled-host-status.json"):
                source = self.root / "build" / name
                if source.is_file():
                    shutil.copy2(source, destination / name)
            shutil.copy2(self.root / "template.json", destination / "fixture-template.json")
            shutil.copytree(self.root / "component", destination / "fixture-component", dirs_exist_ok=True)
            shutil.copytree(self.root / "tests/host", destination / "fixture-host", dirs_exist_ok=True)
        self.assertTrue(report["finished"])
        self.assertFalse(report["release_ready"])
        self.assertIs(report["steps"]["source_host"]["details"]["compiled"], False)
        self.assertIs(report["steps"]["compiled_host"]["details"]["compiled"], True)
        archive = Path(report["artifact"]["path"])
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), report["artifact"]["sha256"])
        with zipfile.ZipFile(archive) as packed:
            self.assertIn(b"Copyright 2009 The Go Authors.", packed.read("UuidTest.4dbase/Resources/licenses/Go-LICENSE.txt"))
            syntax = packed.read("UuidTest.4dbase/Resources/en.lproj/syntaxEN.json")
            self.assertIn(b"Identifier", syntax)
            self.assertNotIn(b"Greeting", syntax)
            self.assertEqual(packed.getinfo("UuidTest.4dbase/Resources/bin/macOS/uuid-test").external_attr >> 16 & 0o777, 0o755)
            self.assertIn("UuidTest.4dbase/Libraries/lib4d-arm64.dylib", packed.namelist())
            with zipfile.ZipFile(io.BytesIO(packed.read("UuidTest.4dbase/UuidTest.4DZ"))) as project:
                self.assertIn("Project/", project.namelist())
                self.assertFalse(any(name.endswith(".4dm") for name in project.namelist()))
        shutil.copy2(archive, destination / archive.name)
        report["artifact"]["path"] = str(destination / archive.name)
        report["fixture_disposed"] = True
        report["scope"] = "local starter identity/wiring regression, not UUID feature acceptance"
        (destination / "UuidTest.acceptance.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
