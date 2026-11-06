"""Version planning and bounded writes use disposable repositories, never real tags."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bump_release


SURFACES = (
    "template.json", "package.json", "cli/main.cpp", "README.md", "cli/PROTOCOL.md",
    "tests/test_cli.py", "tests/host/Project/Sources/Methods/Smoke.4dm", "tests/host/contract.json",
)
INPUTS = (*SURFACES, "tests/cli-contract.json", "cli/CMakeLists.txt",
          "component/SerialPort/Project/SerialPort.4DProject",
          "component/SerialPort/Project/Sources/settings.4DSettings",
          "component/SerialPort/Project/Sources/catalog.4DCatalog",
          "component/SerialPort/Project/Sources/Classes/SerialPort.4dm")


class BumpReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix=".bump-test-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in INPUTS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, path)
        # Keep test baselines independent of future automated repository bumps.
        current = json.loads((ROOT / "template.json").read_text(encoding="utf-8"))["version"]
        for relative in SURFACES:
            path = self.root / relative
            path.write_text(path.read_text(encoding="utf-8").replace(current, "0.1.0"), encoding="utf-8")
        self.git("init", "-q")
        self.git("config", "user.name", "Version fixture")
        self.git("config", "user.email", "version-fixture@example.invalid")
        self.git("remote", "add", "origin", "https://github.com/miyako/SerialPort.git")
        self.git("add", "--all")
        self.git("commit", "-q", "-m", "fixture")
        self.version = json.loads((self.root / "template.json").read_text())["version"]
        self.environment = patch.dict(os.environ, {"GITHUB_REPOSITORY": "miyako/SerialPort"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    def test_patch_minor_major_default_to_configured_baseline_without_tags(self):
        major, minor, patch_version = bump_release.stable(self.version)
        for mode, value in (("patch", (major, minor, patch_version + 1)),
                            ("minor", (major, minor + 1, 0)), ("major", (major + 1, 0, 0))):
            with self.subTest(mode=mode):
                expected = ".".join(map(str, value))
                self.assertEqual(bump_release.next_version(self.root, mode, tags=[]),
                                 {"version": expected, "tag": expected})
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_numeric_tag_order_legacy_tags_and_nonrelease_names(self):
        tags = ["1.9.9", "v1.10.0", "1.11.0-rc.1", "nightly", "v01.99.0"]
        self.assertEqual(bump_release.next_version(self.root, "patch", tags=tags),
                         {"version": "1.10.1", "tag": "1.10.1"})

    def test_real_local_tags_are_read_but_never_changed(self):
        self.git("tag", "v2.0.0")
        self.git("tag", "2.0.9")
        before = self.git("show-ref", "--tags")
        self.assertEqual(bump_release.next_version(self.root, "minor"),
                         {"version": "2.1.0", "tag": "2.1.0"})
        self.assertEqual(self.git("show-ref", "--tags"), before)

    def test_configured_version_cannot_be_downgraded_from_tag_history(self):
        bump_release.apply(self.root, "9.0.0")
        with self.assertRaisesRegex(ValueError, "downgrade"):
            bump_release.next_version(self.root, "patch", tags=["1.0.0"])

    def test_failed_release_can_retry_an_already_bumped_version(self):
        bump_release.apply(self.root, "1.0.1")
        self.assertEqual(bump_release.next_version(self.root, "patch", tags=["1.0.0"]),
                         {"version": "1.0.1", "tag": "1.0.1"})
        self.assertEqual(bump_release.apply(self.root, "1.0.1")["changed_paths"], [])

    def test_apply_updates_every_surface_and_preserves_dependency_pins(self):
        unchanged = {path: (self.root / path).read_bytes() for path in INPUTS if path not in SURFACES}
        unrelated = self.root / "notes.txt"
        unrelated.write_text("historical version " + self.version, encoding="utf-8")
        source = self.root / "cli/main.cpp"
        source.write_text(source.read_text() + "// historical " + self.version + "\n", encoding="utf-8")
        before_head = self.git("rev-parse", "HEAD")
        result = bump_release.apply(self.root, "3.2.1", stage=True)
        self.assertEqual(result, {"version": "3.2.1", "tag": "3.2.1", "changed_paths": sorted(SURFACES)})
        self.assertEqual(self.git("diff", "--cached", "--name-only").splitlines(), sorted(SURFACES))
        for path, content in unchanged.items():
            self.assertEqual((self.root / path).read_bytes(), content, path)
        self.assertEqual(unrelated.read_text(), "historical version " + self.version)
        self.assertIn("// historical " + self.version, source.read_text())
        self.assertEqual(self.git("rev-parse", "HEAD"), before_head)
        self.assertEqual(self.git("tag", "--list"), "")
        self.assertEqual(bump_release.plan(self.root, "3.2.1")[1], {})

    def test_noop_does_not_reformat_or_stage_files(self):
        before = {path: (self.root / path).read_bytes() for path in INPUTS}
        self.assertEqual(bump_release.apply(self.root, self.version, stage=True)["changed_paths"], [])
        self.assertEqual(self.git("status", "--porcelain"), "")
        for path, content in before.items():
            self.assertEqual((self.root / path).read_bytes(), content)

    def test_late_contract_failure_does_not_write_any_surface(self):
        contract = self.root / "tests/host/contract.json"
        value = json.loads(contract.read_text())
        value["execution"]["assertions"] = [
            item for item in value["execution"]["assertions"] if item.get("path") != "/renamedExecutable"]
        contract.write_text(json.dumps(value), encoding="utf-8")
        before = {path: (self.root / path).read_bytes() for path in SURFACES}
        with self.assertRaisesRegex(ValueError, "missing exact host"):
            bump_release.apply(self.root, "2.0.0", stage=True)
        for path, content in before.items():
            self.assertEqual((self.root / path).read_bytes(), content)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_native_placeholder_contract_is_required(self):
        path = self.root / "tests/cli-contract.json"
        path.write_text(path.read_text().replace("$VERSION", self.version), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "placeholder"):
            bump_release.apply(self.root, "2.0.0")

    def test_native_version_contract_rejects_bad_types_and_ambiguous_streams(self):
        path = self.root / "tests/cli-contract.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        changes = (
            lambda value: value.update(schema=True),
            lambda value: value.update(cases=[None]),
            lambda value: value["cases"].append(dict(
                next(item for item in value["cases"] if item["args"] == ["--version"]))),
        )
        for change in changes:
            value = json.loads(json.dumps(original))
            change(value)
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                bump_release.apply(self.root, "2.0.0")
        for change in ({"exitCode": True}, {"stderr": "warning"}, {"stdoutPattern": ".*"},
                       {"stderrPattern": ".*"}, {"stdout": {"protocol": True, "version": "$VERSION"}}):
            value = json.loads(json.dumps(original))
            next(item for item in value["cases"] if item["args"] == ["--version"]).update(change)
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(change=change), self.assertRaises(ValueError):
                bump_release.apply(self.root, "2.0.0")
        self.assertEqual(json.loads((self.root / "template.json").read_text())["version"], self.version)

    def test_mismatched_literal_fails_before_writes(self):
        path = self.root / "cli/PROTOCOL.md"
        path.write_text(path.read_text().replace(self.version, "99.99.99"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "version surface"):
            bump_release.apply(self.root, "2.0.0")
        self.assertEqual(json.loads((self.root / "template.json").read_text())["version"], self.version)

    def test_duplicate_metadata_key_is_rejected(self):
        path = self.root / "package.json"
        path.write_text(path.read_text().replace('"version":', '"version": "1.0.0", "version":', 1),
                        encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            bump_release.next_version(self.root, "patch")

    def test_invalid_versions_modes_and_tag_inputs_fail(self):
        for version in ("v1.2.3", "1.2.3-rc.1", "01.2.3", "١.2.3", "1.2.3\nsha=x", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                bump_release.apply(self.root, version)
        for tags in ("1.2.3", [None]):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                bump_release.next_version(self.root, "patch", tags=tags)
        with self.assertRaisesRegex(ValueError, "mode"):
            bump_release.next_version(self.root, "nightly", tags=[])

    def test_exact_repository_identity_and_original_template_guard(self):
        with self.assertRaisesRegex(ValueError, "exactly match"):
            bump_release.next_version(self.root, "patch", repository="miyako/serialport")
        self.git("remote", "set-url", "origin", "https://github.com/miyako/4d-component-template.git")
        with self.assertRaisesRegex(ValueError, "original template"):
            bump_release.next_version(self.root, "patch", repository="miyako/4d-component-template")

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges vary")
    def test_symlink_version_input_is_rejected(self):
        path = self.root / "cli/PROTOCOL.md"
        source = self.root / "original-protocol.md"
        path.rename(source)
        path.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            bump_release.apply(self.root, "2.0.0")
        self.assertEqual(json.loads((self.root / "template.json").read_text())["version"], self.version)

    def test_cli_outputs_a_single_line_plan_without_mutation(self):
        output = self.root / "github-output"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/bump_release.py"), "--root", str(self.root),
             "next", "--mode", "patch"], env={**os.environ, "GITHUB_OUTPUT": str(output)},
            capture_output=True, text=True, encoding="utf-8", check=True)
        value = json.loads(result.stdout)
        self.assertEqual(output.read_text(), f"version={value['version']}\ntag={value['tag']}\n")
        self.assertEqual(self.git("diff", "--name-only"), "")


if __name__ == "__main__":
    unittest.main()
