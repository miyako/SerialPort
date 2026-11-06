"""Synthetic evidence and mocked publication; never sign, create real tags or publish."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release_assets as assets
from build_provenance import record
from package_component import write_zip
from test_host_runner import valid_status


MANUAL = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main"}
FIXTURE_VERSION = "1.2.3"


def hashed(data):
    return hashlib.sha256(data).hexdigest()


def fixture_version(value):
    if isinstance(value, dict):
        result = {key: fixture_version(item) for key, item in value.items()}
        if type(result.get("protocol")) is int and result["protocol"] == 1 and "version" in result:
            result["version"] = FIXTURE_VERSION
        return result
    if isinstance(value, list):
        return [fixture_version(item) for item in value]
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except ValueError:
            return value
        if (isinstance(payload, dict) and type(payload.get("protocol")) is int
                and payload["protocol"] == 1 and "version" in payload):
            payload["version"] = FIXTURE_VERSION
            return json.dumps(payload, separators=(",", ":")) + ("\n" if value.endswith("\n") else "")
    return value


class ReleaseAssetsTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix=".release-assets-tests-", dir=ROOT)
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.config = json.loads((ROOT / "template.json").read_text())
        self.config["version"] = FIXTURE_VERSION
        self.name = self.config["repository"]
        self.repository = "miyako/" + self.name
        self.tag = self.config["version"]
        for name in ("template.json", "package.json"):
            metadata = json.loads((ROOT / name).read_text())
            metadata["version"] = FIXTURE_VERSION
            (self.root / name).write_text(json.dumps(metadata) + "\n")
        project = Path("component") / self.name / "Project"
        feature = self.config["feature_class"]
        for relative in (project / (self.name + ".4DProject"),
                         project / "Sources/settings.4DSettings", project / "Sources/catalog.4DCatalog",
                         project / "Sources/Classes" / (feature + ".4dm"),
                         Path("tests/host/contract.json")):
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        contract = self.root / "tests/host/contract.json"
        contract.write_text(json.dumps(fixture_version(json.loads(contract.read_text()))) + "\n")
        (self.root / "cli").mkdir()
        (self.root / "cli/main.cpp").write_text("// synthetic test input\n")
        self.git("init", "-q")
        self.git("remote", "add", "origin", "https://github.com/" + self.repository + ".git")
        self.git("config", "user.name", "Release assets fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "fixture")
        self.revision = self.git("rev-parse", "HEAD")
        self.native = {}
        for target in assets.TARGETS:
            executable = self.root / "build" / target / (
                self.config["cli"] + (".exe" if target == "windows-x64" else ""))
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"synthetic native " + target.encode())
            self.native[target] = record(self.root, target, executable, True, {"fixture": True})
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.archive = self.dist / (self.name + ".zip")
        self.dmg = self.dist / (self.name + ".dmg")
        self.evidence = self.dist / (self.name + ".signing.json")
        self.inner = {}
        for relative in ((self.name + ".4DProject"), "Sources/settings.4DSettings", "Sources/catalog.4DCatalog"):
            self.inner["Project/" + relative] = ((self.root / project / relative).read_bytes(), 0o644)
        for relative in ("classes.json", "map.json", "MX64 123"):
            self.inner["Project/DerivedData/CompiledCode/" + relative] = (b"synthetic compiled data", 0o644)
        helper = "Resources/bin/macOS/" + self.config["cli"]
        self.outer = {
            helper: (b"\xca\xfe\xba\xbe" + b"synthetic signed universal", 0o755),
            "Libraries/lib4d-arm64.dylib": (b"\xcf\xfa\xed\xfe" + b"synthetic signed library", 0o755),
            "Resources/bin/Windows/" + self.config["cli"] + ".exe": (
                (self.root / "build/windows-x64" / (self.config["cli"] + ".exe")).read_bytes(), 0o755),
            "Resources/en.lproj/syntaxEN.json": (json.dumps({
                "cs." + self.config["namespace"]: {feature: {"new()": {}}},
                feature: {"version()": {}}}).encode(), 0o644),
        }
        self.pack()
        self.dmg.write_bytes(b"synthetic signed and stapled DMG")
        signed = []
        for name in (helper, "Libraries/lib4d-arm64.dylib"):
            signed.append({"path": name, "before_sha256": hashed(b"unsigned " + name.encode()),
                           "after_sha256": hashed(self.outer[name][0]),
                           "signature": {**self.signature(), "runtime": name == helper,
                                         "architectures": ["x86_64", "arm64"] if name == helper else ["arm64"]}})
        self.report = {
            "schema": 1, "commit": self.revision, "cli_source_sha256": assets.source_digest(self.root),
            "result": "partial", "finished": True, "release_ready": False,
            "steps": {name: {"status": "passed"} for name in assets.REQUIRED_STEPS},
            "signed_files": signed, "source_host": fixture_version(valid_status(False)),
            "compiled_host": fixture_version(valid_status(True)), "dmg_host": fixture_version(valid_status(True)),
            "downloaded_package_host": fixture_version(valid_status(True)),
            "downloaded_dmg_host": fixture_version(valid_status(True)),
            "compile": {"success": True, "targets": ["x86_64_generic", "arm64_macOS_lib"],
                        "input_sha256": "a" * 64, "output_sha256": "b" * 64,
                        "project": "/runner/component/" + self.name + "/Project/" + self.name + ".4DProject"},
            "tool4d": {"version": "fixture 20.7", "binary_sha256": "c" * 64},
            "artifact": self.artifact(self.archive), "dmg_artifact": self.artifact(self.dmg),
            "windows_sha256": self.native["windows-x64"]["binary_sha256"],
            "notarization": self.receipt(hashed(self.archive.read_bytes())),
            "dmg_signing": {"before_sha256": "d" * 64, "signed_sha256": "e" * 64,
                            "signature": self.signature()},
            "dmg_notarization": self.receipt("e" * 64),
            "dmg_signature": {**self.signature(), "stapled_ticket": True, "gatekeeper_assessment": True},
            "dmg_tree_sha256": "f" * 64,
        }
        self.report["steps"]["windows_authenticode"] = {
            "status": "not_run", "reason": "Windows Authenticode not implemented or provisioned"}
        self.save()

    def git(self, *arguments):
        return subprocess.run(["git", *arguments], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout.strip()

    @staticmethod
    def signature():
        return {"developer_id": True, "team_matches": True, "secure_timestamp": True}

    @staticmethod
    def receipt(digest):
        return {"id": str(uuid.uuid4()), "status": "Accepted", "log_checked": True,
                "archive_sha256": digest, "log_issue_count": 0}

    @staticmethod
    def artifact(path):
        return {"path": path.name, "sha256": assets.sha256(path), "bytes": path.stat().st_size}

    def pack(self):
        packed = io.BytesIO()
        write_zip(packed, [(name, data, mode) for name, (data, mode) in self.inner.items()])
        entries = [(self.name + ".4dbase/" + self.name + ".4DZ", packed.getvalue(), 0o644)]
        entries += [(self.name + ".4dbase/" + name, data, mode)
                    for name, (data, mode) in self.outer.items()]
        write_zip(self.archive, entries)

    def save(self):
        self.evidence.write_text(json.dumps(self.report) + "\n")

    def rebind_archive(self):
        self.pack()
        self.report["artifact"] = self.artifact(self.archive)
        self.report["notarization"]["archive_sha256"] = self.report["artifact"]["sha256"]
        self.save()

    def verify(self, tag=None):
        return assets.verify_assets(self.root, self.repository, tag or self.tag, self.revision)

    def test_exact_three_assets_verify_without_network_or_tags(self):
        with patch.object(assets, "create_release_tag") as create, patch.object(assets, "verify_tag") as verify:
            self.assertEqual(self.verify(), [self.archive, self.dmg, self.evidence])
            self.assertEqual(self.verify("v" + self.tag), [self.archive, self.dmg, self.evidence])
            create.assert_not_called()
            verify.assert_not_called()
        self.assertNotEqual(self.report["dmg_signing"]["signed_sha256"], self.report["dmg_artifact"]["sha256"])

    def test_temporary_metadata_and_host_protocol_use_fixed_fixture_version(self):
        self.assertEqual(self.tag, FIXTURE_VERSION)
        for name in ("template.json", "package.json"):
            self.assertEqual(json.loads((self.root / name).read_text())["version"], FIXTURE_VERSION)
        for name in ("source_host", "compiled_host", "dmg_host", "downloaded_package_host", "downloaded_dmg_host"):
            self.assertEqual(self.report[name]["version"]["value"]["version"], FIXTURE_VERSION)
        self.assertEqual(fixture_version({"protocol": 1, "version": "99.88.77"})["version"], FIXTURE_VERSION)
        self.assertEqual(json.loads(fixture_version('{"protocol":1,"version":"99.88.77"}\n'))["version"],
                         FIXTURE_VERSION)
        self.verify()

    def test_every_required_build_and_download_phase_is_mandatory(self):
        original = copy.deepcopy(self.report)
        for phase in assets.REQUIRED_STEPS:
            for value in (None, {"status": "not_run"}, {"status": "failed"}):
                with self.subTest(phase=phase, value=value):
                    self.report = copy.deepcopy(original)
                    self.report["steps"][phase] = value
                    self.save()
                    with self.assertRaises(ValueError):
                        self.verify()

    def test_revision_config_source_and_completion_must_match(self):
        original = copy.deepcopy(self.report)
        for field, value in (("commit", "0" * 40), ("cli_source_sha256", "0" * 64),
                             ("result", "passed"), ("finished", False), ("finished", 1),
                             ("release_ready", True), ("schema", True)):
            self.report = dict(original, **{field: value})
            self.save()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()
        self.report = original
        self.save()
        for repository, tag, revision in (("miyako/serialport", self.tag, self.revision),
                                          ("another-owner/" + self.name, self.tag, self.revision),
                                          (self.repository, "9.9.9", self.revision),
                                          (self.repository, self.tag, self.revision.upper()),
                                          (self.repository, self.tag, self.revision[:8])):
            with self.assertRaises(ValueError):
                assets.verify_assets(self.root, repository, tag, revision)

    def test_host_reports_use_actual_contract_not_boolean_summary(self):
        original = copy.deepcopy(self.report)
        for name in ("source_host", "compiled_host", "dmg_host", "downloaded_package_host", "downloaded_dmg_host"):
            for change in ({"compiled": name == "source_host"}, {"finished": False}, {"cases": []},
                           {"async": {"responseCount": 0}}):
                self.report = copy.deepcopy(original)
                self.report[name].update(change)
                self.save()
                with self.subTest(name=name, change=change), self.assertRaises(ValueError):
                    self.verify()

    def test_compiler_and_tool_identity_are_mandatory(self):
        original = copy.deepcopy(self.report)
        for field, value in (("success", False), ("targets", ["x86_64_generic"]),
                             ("targets", ["x86_64_generic", "x86_64_generic"]),
                             ("input_sha256", "invalid"), ("output_sha256", None),
                             ("project", "/wrong/component.4DProject")):
            self.report = copy.deepcopy(original)
            self.report["compile"][field] = value
            self.save()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()
        self.report = copy.deepcopy(original)
        self.report["tool4d"]["binary_sha256"] = ""
        self.save()
        with self.assertRaises(ValueError):
            self.verify()

    def test_native_provenance_execution_and_actual_bytes_independently_verified(self):
        for target in assets.TARGETS:
            path = self.root / "build" / target / "build-manifest.json"
            original = path.read_text()
            for field, value in (("schema", True), ("execution", "not_run"), ("commit", "0" * 40),
                                 ("source_sha256", "0" * 64), ("binary_sha256", "0" * 64)):
                manifest = json.loads(original)
                manifest[field] = value
                path.write_text(json.dumps(manifest))
                with self.subTest(target=target, field=field), self.assertRaises(ValueError):
                    self.verify()
            path.write_text(original)
        executable = self.root / "build/macos-arm64" / self.config["cli"]
        executable.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.verify()

    def test_artifact_names_hashes_positive_sizes_and_no_extra_dist_assets(self):
        original = copy.deepcopy(self.report)
        for name in ("artifact", "dmg_artifact"):
            for field, value in (("path", "../unexpected.zip"), ("bytes", 0),
                                 ("bytes", True), ("sha256", "0" * 64)):
                self.report = copy.deepcopy(original)
                self.report[name][field] = value
                self.save()
                with self.assertRaises(ValueError):
                    self.verify()
        self.report = original
        self.save()
        (self.dist / "unvalidated.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "unvalidated"):
            self.verify()

    def test_windows_unsigned_disclosure_cannot_be_removed_or_upgraded(self):
        original = copy.deepcopy(self.report["steps"]["windows_authenticode"])
        for value in (None, {"status": "passed"}, {"status": "not_run"}, {"status": "not_run", "reason": ""}):
            self.report["steps"]["windows_authenticode"] = value
            self.save()
            with self.assertRaisesRegex(ValueError, "Windows Authenticode"):
                self.verify()
        self.report["steps"]["windows_authenticode"] = original

    def test_notary_receipts_logs_and_dmg_ticket_are_mandatory(self):
        original = copy.deepcopy(self.report)
        for name in ("notarization", "dmg_notarization"):
            for field, value in (("status", "In Progress"), ("id", "invalid"), ("id", str(uuid.UUID(int=0))),
                                 ("log_checked", False), ("log_issue_count", -1),
                                 ("log_issue_count", True), ("archive_sha256", "0" * 64)):
                self.report = copy.deepcopy(original)
                self.report[name][field] = value
                self.save()
                with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                    self.verify()
        for field in ("stapled_ticket", "gatekeeper_assessment", "team_matches", "secure_timestamp"):
            self.report = copy.deepcopy(original)
            self.report["dmg_signature"][field] = False
            self.save()
            with self.assertRaises(ValueError):
                self.verify()

    def test_signed_inventory_paths_hashes_and_signature_metadata_are_strict(self):
        original = copy.deepcopy(self.report)
        entries = original["signed_files"]
        for inventory in ([], entries[:1], entries + entries[:1],
                          [dict(entries[0], path="Libraries/../outside"), entries[1]],
                          [dict(entries[0], after_sha256="0" * 64), entries[1]],
                          [dict(entries[0], before_sha256="invalid"), entries[1]]):
            self.report = dict(original, signed_files=copy.deepcopy(inventory))
            self.save()
            with self.assertRaises(ValueError):
                self.verify()
        for field, value in (("developer_id", False), ("team_matches", False), ("secure_timestamp", False),
                             ("runtime", False), ("architectures", ["arm64"])):
            self.report = copy.deepcopy(original)
            self.report["signed_files"][0]["signature"][field] = value
            self.save()
            with self.assertRaises(ValueError):
                self.verify()

    def test_zip_source_stripping_namespace_completion_and_native_inventory(self):
        original_inner, original_outer = copy.deepcopy(self.inner), copy.deepcopy(self.outer)
        mutations = (
            ("inner", "Project/Sources/Classes/Hidden.4dm", b"interpreted source"),
            ("inner", "Project/Sources/settings.4DSettings", b'<preferences><general component_classStore_name="Wrong"/></preferences>'),
            ("inner", "Project/Sources/catalog.4DCatalog", b'<base name="Wrong"/>'),
            ("outer", "Resources/en.lproj/syntaxEN.json", b'{"cs.Wrong":{"Wrong":{}}}'),
            ("outer", "Libraries/unlisted.dylib", b"\xcf\xfa\xed\xfemore native code"),
            ("outer", "Resources/bin/Windows/" + self.config["cli"] + ".exe", b"changed Windows binary"),
        )
        for location, name, data in mutations:
            self.inner, self.outer = copy.deepcopy(original_inner), copy.deepcopy(original_outer)
            (self.inner if location == "inner" else self.outer)[name] = (data, 0o644)
            self.rebind_archive()
            with self.subTest(location=location, name=name), self.assertRaises(ValueError):
                self.verify()

    @unittest.skipIf(os.name == "nt", "Windows symlink privileges vary")
    def test_symlink_assets_manifest_and_parents_are_rejected(self):
        source = self.root / "outside.dmg"
        self.dmg.rename(source)
        self.dmg.symlink_to(source)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.verify()
        self.dmg.unlink()
        source.rename(self.dmg)
        manifest = self.root / "build/macos-arm64/build-manifest.json"
        original = manifest.with_name("original-manifest.json")
        manifest.rename(original)
        manifest.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.verify()

    def test_archive_path_traversal_symlinks_duplicates_and_missing_directories(self):
        for name, mode in (("../outside", stat.S_IFREG | 0o644), ("/absolute", stat.S_IFREG | 0o644),
                           ("safe/../outside", stat.S_IFREG | 0o644), ("link", stat.S_IFLNK | 0o777),
                           ("without/parent", stat.S_IFREG | 0o644)):
            stream = io.BytesIO()
            with assets.zipfile.ZipFile(stream, "w") as archive:
                info = assets.zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                archive.writestr(info, b"data")
            with self.assertRaises(ValueError):
                assets.zip_entries(stream.getvalue())
        stream = io.BytesIO()
        with assets.zipfile.ZipFile(stream, "w") as archive:
            for name, mode in (("same", stat.S_IFREG | 0o644), ("same/", stat.S_IFDIR | 0o755)):
                info = assets.zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                archive.writestr(info, b"")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assets.zip_entries(stream.getvalue())

    def test_publish_verifies_everything_before_tag_or_network_and_is_prerelease_only(self):
        events = []
        with patch.dict(os.environ, MANUAL, clear=True), patch.object(
                assets, "verify_assets", side_effect=lambda *a: events.append("verify") or [
                    self.archive, self.dmg, self.evidence]), patch.object(
                assets, "create_release_tag", side_effect=lambda *a: events.append("tag")) as tag, patch.object(
                assets.subprocess, "run", side_effect=lambda *a, **k: events.append("release")) as run:
            assets.publish(self.root, self.repository, self.tag, self.revision, create_tag=True)
        self.assertEqual(events, ["verify", "tag", "release"])
        tag.assert_called_once_with(self.root, self.repository, self.tag, self.revision)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["gh", "release", "create", self.tag])
        self.assertIn("--verify-tag", command)
        self.assertIn("--prerelease", command)
        self.assertIn("--latest=false", command)
        self.assertEqual(command[4:7], [str(self.archive), str(self.dmg), str(self.evidence)])
        self.assertIn("hardware", command[-1])
        self.assertIn("unsigned", command[-1])

    def test_bad_assets_or_wrong_manual_branch_never_create_tags_or_releases(self):
        with patch.object(assets, "create_release_tag") as tag, patch.object(
                assets, "verify_tag") as existing, patch.object(assets.subprocess, "run") as run:
            with patch.object(assets, "verify_assets", side_effect=ValueError("asset gate")):
                with self.assertRaisesRegex(ValueError, "asset gate"):
                    assets.publish(self.root, self.repository, self.tag, self.revision, create_tag=True)
            for environment in ({}, {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"},
                                {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/feature"}):
                with patch.object(assets, "verify_assets", return_value=[]), patch.dict(
                        os.environ, environment, clear=True), self.assertRaises(ValueError):
                    assets.publish(self.root, self.repository, self.tag, self.revision, create_tag=True)
            tag.assert_not_called()
            existing.assert_not_called()
            run.assert_not_called()

    def test_existing_tag_path_is_verified_and_legacy_publication_is_rejected(self):
        with patch.dict(os.environ, MANUAL, clear=True), patch.object(
                assets, "verify_assets", return_value=[]), patch.object(
                assets, "verify_tag") as verify, patch.object(assets.subprocess, "run"):
            assets.publish(self.root, self.repository, self.tag, self.revision)
            verify.assert_called_once_with(self.root, self.tag, self.revision)
            with self.assertRaisesRegex(ValueError, "prefix-free"):
                assets.publish(self.root, self.repository, "v" + self.tag, self.revision)

    def test_cli_defaults_to_verify_and_create_tag_requires_explicit_publish(self):
        arguments = ["--root", str(self.root), "--repository", self.repository,
                     "--tag", self.tag, "--sha", self.revision]
        with patch.object(assets, "verify_assets", return_value=[]) as verify, patch.object(assets, "publish") as publish:
            self.assertEqual(assets.main(arguments), 0)
            verify.assert_called_once()
            publish.assert_not_called()
            with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
                assets.main([*arguments, "--create-tag"])
            self.assertEqual(error.exception.code, 2)
            publish.assert_not_called()
            self.assertEqual(assets.main([*arguments, "--publish", "--create-tag"]), 0)
            publish.assert_called_once_with(self.root, self.repository, self.tag, self.revision, create_tag=True)


if __name__ == "__main__":
    unittest.main()
