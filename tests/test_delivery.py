"""Static delivery tests: synthetic binary headers never execute or use toolchains."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_cli
import ci_config
import package_component
from compile_component import input_digest, output_digest
from build_provenance import record


def synthetic_universal():
    result = bytearray(112)
    struct.pack_into(">II", result, 0, 0xCAFEBABE, 2)
    for index, cpu in enumerate((0x01000007, 0x0100000C)):
        offset = 48 + index * 32
        struct.pack_into(">IIIII", result, 8 + index * 20, cpu, 0, offset, 32, 0)
        struct.pack_into("<II", result, offset, 0xFEEDFACF, cpu)
    return bytes(result)


def synthetic_pe():
    result = bytearray(128)
    result[:2] = b"MZ"
    struct.pack_into("<I", result, 0x3C, 64)
    result[64:68] = b"PE\0\0"
    struct.pack_into("<H", result, 68, 0x8664)
    struct.pack_into("<H", result, 88, 0x20B)
    return bytes(result)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix=".delivery-test-", dir=ROOT)
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.config = {
            "schema": 1, "initialized": True, "repository": "example-component",
            "namespace": "Example", "cli": "example-cli", "language": "go",
            "feature": "greeting", "version": "0.1.0",
        }
        self.write_config()
        self.component = self.root / "component" / self.config["repository"]
        self.project = self.component / "Project" / "example-component.4DProject"
        self.project.parent.mkdir(parents=True)
        self.project.write_text("{}", encoding="utf-8")
        sources = self.project.parent / "Sources"
        sources.mkdir()
        (sources / "source.4dm").write_text("// excluded interpreted source\n", encoding="utf-8")
        for directory in ("Classes", "Methods"):
            (sources / directory).mkdir()
            (sources / directory / "excluded.4dm").write_text("// excluded\n", encoding="utf-8")
            (sources / directory / "metadata.json").write_text("{}", encoding="utf-8")
        (sources / "Classes/Greeting.4dm").write_text("// synthetic feature class\n")
        (self.root / "cli").mkdir()
        (self.root / "cli/main.go").write_text("// synthetic CLI source\n")
        for name in ("settings.4DSettings", "catalog.4DCatalog", "dependencies.json", "folders.json"):
            (sources / name).write_text("{}", encoding="utf-8")
        (sources / "settings.4DSettings").write_text('<preferences><com.4d><general component_classStore_name="Example"/></com.4d></preferences>')
        (sources / "catalog.4DCatalog").write_text('<base name="example-component"/>')
        compiled = self.project.parent / "DerivedData" / "CompiledCode"
        compiled.mkdir(parents=True)
        for name in ("classes.json", "map.json"):
            (compiled / name).write_text("{}", encoding="utf-8")
        for architecture in ("MX64", "IX64"):
            (compiled / (architecture + " 123")).write_bytes(b"synthetic compiled code")
        for name in ("methodAttributes.json", "formAttributes.json", "webservices.json", "excluded-cache.json"):
            (compiled.parent / name).write_text("{}", encoding="utf-8")
        libraries = self.component / "Libraries"
        libraries.mkdir()
        (libraries / "lib4d-arm64.dylib").write_bytes(b"static test compiled placeholder")
        (libraries / "lib4d-arm64.dylib").chmod(0o755)
        documentation = self.component / "Documentation" / "Classes"
        documentation.mkdir(parents=True)
        (documentation / "Example.md").write_text("Documentation\n", encoding="utf-8")
        self.mac = self.component / "Resources" / "bin" / "macOS" / "example-cli"
        self.windows = self.component / "Resources" / "bin" / "Windows" / "example-cli.exe"
        for path, content in ((self.mac, synthetic_universal()), (self.windows, synthetic_pe())):
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
        resources = self.component / "Resources"
        (resources / "licenses").mkdir()
        for language, name in package_component.RUNTIME_NOTICES.items():
            source = ROOT / ("cli" if json.loads((ROOT / "template.json").read_text())["initialized"] else "cli-starters/" + language) / "licenses" / name
            # Synthetic delivery tests may select an unrelated language after initialization.
            (resources / "licenses" / name).write_bytes(source.read_bytes() if source.is_file() else b"synthetic runtime notice")
        (resources / "logo.png").write_bytes(b"synthetic PNG fixture")
        (resources / "logo.svg").write_text("<svg/>", encoding="utf-8")
        (resources / "en.lproj").mkdir()
        (resources / "en.lproj" / "syntaxEN.json").write_text("{}", encoding="utf-8")
        (self.root / "LICENSE").write_text("test license\n", encoding="utf-8")
        self.marker = self.root / "build" / "compile-status.json"
        self.marker.parent.mkdir()
        self.status = {
            "success": True, "targets": ["x86_64_generic", "arm64_macOS_lib"],
            "project": self.project.relative_to(self.root).as_posix(),
        }
        self.write_status()

    def write_config(self):
        (self.root / "template.json").write_text(json.dumps(self.config), encoding="utf-8")
        (self.root / "package.json").write_text(
            json.dumps({"name": self.config["repository"], "version": self.config["version"]}), encoding="utf-8")

    def write_status(self):
        self.status["input_sha256"] = input_digest(self.root)
        self.status["output_sha256"] = output_digest(self.component)
        self.marker.write_text(json.dumps(self.status), encoding="utf-8")

    def package(self, **options):
        return package_component.package(
            self.root, repository="owner/example-component", tag="v0.1.0", **options)

    def test_uninitialized_fails_and_has_no_native_jobs(self):
        self.config.update(initialized=False, repository="4d-component-template", language=None)
        self.write_config()
        self.assertEqual(ci_config.matrix(ci_config.load_config(self.root)), {"include": []})
        with self.assertRaisesRegex(ValueError, "initialize"):
            package_component.package(self.root, repository="owner/4d-component-template", tag="v0.1.0")

    def test_matrix_only_selected_language(self):
        for language in ("go", "rust", "cpp"):
            self.config["language"] = language
            self.write_config()
            entries = ci_config.matrix(ci_config.load_config(self.root))["include"]
            self.assertEqual([entry["target"] for entry in entries], list(ci_config.TARGETS))
            self.assertEqual([entry["runner"] for entry in entries],
                             ["macos-15-intel", "macos-15", "windows-2022"])
            self.assertEqual({entry["language"] for entry in entries}, {language})

    def test_assembly_places_file_before_variadic_verify_arch_option(self):
        inputs = []
        for target in ci_config.TARGETS:
            filename = self.config["cli"] + (".exe" if target == "windows-x64" else "")
            path = self.root / "build" / target / filename
            path.parent.mkdir()
            path.write_bytes(synthetic_pe() if target == "windows-x64" else b"mock native slice")
            record(self.root, target, path, True, "synthetic test")
            inputs.append(path)
        with patch.dict(os.environ, {}, clear=True), patch("build_provenance.commit", return_value=None), patch.object(ci_config.subprocess, "run") as run:
            destination = ci_config.assemble(self.root)
        self.assertEqual(run.call_args_list, [
            unittest.mock.call(["lipo", "-create", str(inputs[0]), str(inputs[1]),
                                "-output", str(self.mac)], check=True),
            unittest.mock.call(["lipo", str(self.mac), "-verify_arch", "x86_64", "arm64"], check=True),
        ])
        self.assertEqual(destination, self.mac.parent.parent)
        self.assertEqual(self.mac.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.windows.read_bytes(), inputs[2].read_bytes())

    def test_all_languages_preserve_repository_release_asset_identity(self):
        for language in ("go", "rust", "cpp"):
            self.config["language"] = language
            self.write_config()
            self.write_status()
            archive = self.package(language=language)
            self.assertEqual(archive.name, self.config["repository"] + ".zip")
            with zipfile.ZipFile(archive) as packed:
                prefix = self.config["repository"] + ".4dbase/"
                self.assertTrue(all(name.startswith(prefix) for name in packed.namelist()))
                project_name = prefix + self.config["repository"] + ".4DZ"
                with zipfile.ZipFile(io.BytesIO(packed.read(project_name))) as project:
                    self.assertIn("Project/" + self.config["repository"] + ".4DProject",
                                  project.namelist())

    def test_wrong_repository(self):
        with self.assertRaisesRegex(ValueError, "actual repository"):
            package_component.package(self.root, repository="owner/wrong", tag="v0.1.0")

    def test_wrong_package_name(self):
        (self.root / "package.json").write_text('{"name":"wrong"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "package.json name"):
            self.package()

    def test_wrong_package_version(self):
        (self.root / "package.json").write_text('{"name":"example-component","version":"9.0.0"}')
        with self.assertRaisesRegex(ValueError, "package.json version"):
            self.package()

    def test_wrong_4d_project_namespace_and_catalog(self):
        settings = self.project.parent / "Sources/settings.4DSettings"
        original = settings.read_text()
        settings.write_text(original.replace('"Example"', '"Wrong"'))
        with self.assertRaisesRegex(ValueError, "namespace"):
            self.package()
        settings.write_text(original)
        catalog = self.project.parent / "Sources/catalog.4DCatalog"
        catalog.write_text('<base name="wrong"/>')
        with self.assertRaisesRegex(ValueError, "catalog name"):
            self.package()

    def test_wrong_language(self):
        with self.assertRaisesRegex(ValueError, "language"):
            self.package(language="rust")
        self.config["language"] = "python"
        self.write_config()
        with self.assertRaisesRegex(ValueError, "language"):
            self.package()

    def test_initialized_configuration_uses_initializer_reserved_name_rules(self):
        for field in ("repository", "cli"):
            original = self.config[field]
            for name in ("con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"):
                self.config[field] = name
                self.write_config()
                with self.subTest(field=field, name=name), self.assertRaisesRegex(ValueError, "reserved"):
                    ci_config.load_config(self.root)
            self.config[field] = original
            self.write_config()

    def test_bad_tag_or_semver(self):
        for tag in ("0.2.0", "v0.2.0", "0.1.0/../../escape", "v0.1.0/../../escape"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "tag"):
                package_component.package(self.root, repository="owner/example-component", tag=tag)
        for version in ("01.1.0", "0.1", "0.1.0-01", "0.1.0\n", "0.1.0/escape"):
            self.config["version"] = version
            self.write_config()
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "SemVer"):
                ci_config.load_config(self.root)

    def test_prefix_free_and_legacy_matching_tags_pack_the_same_component(self):
        prefix_free = package_component.package(
            self.root, repository="owner/example-component", tag="0.1.0").read_bytes()
        legacy = package_component.package(
            self.root, repository="owner/example-component", tag="v0.1.0").read_bytes()
        self.assertEqual(prefix_free, legacy)

    def test_missing_resources(self):
        for resource in (self.mac, self.windows):
            content = resource.read_bytes()
            resource.unlink()
            with self.assertRaisesRegex(ValueError, "missing regular native resource"):
                self.package()
            resource.write_bytes(content)

    def test_selected_runtime_notice_missing_or_empty_fails(self):
        notice = self.component / "Resources/licenses/Go-LICENSE.txt"
        original = notice.read_bytes()
        for empty in (False, True):
            if empty:
                notice.write_bytes(b"")
            else:
                notice.unlink()
            with self.assertRaisesRegex(ValueError, "redistribution notice"):
                self.package()
            notice.write_bytes(original)

    def test_fake_headers_rejected(self):
        self.mac.write_bytes(b"\xca\xfe\xba\xbe" + b"\0" * 64)
        with self.assertRaisesRegex(ValueError, "exactly Intel and ARM"):
            self.package()
        self.mac.write_bytes(synthetic_universal())
        self.windows.write_bytes(b"MZ" + b"\0" * 128)
        with self.assertRaisesRegex(ValueError, "PE32"):
            self.package()

    def test_architecture_and_slice_bounds(self):
        data = bytearray(synthetic_universal())
        struct.pack_into(">I", data, 20, 1000)
        self.mac.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "bounds"):
            self.package()
        self.mac.write_bytes(synthetic_universal())
        data = bytearray(synthetic_pe())
        struct.pack_into("<H", data, 68, 0xAA64)
        self.windows.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "x64"):
            self.package()

    def test_missing_failed_or_wrong_compile_marker(self):
        self.marker.unlink()
        with self.assertRaisesRegex(ValueError, "compile-status"):
            self.package()
        for updates in (
            {"success": False}, {"success": "true"}, {"targets": ["x86_64_generic"]},
            {"project": "component/other/Project/other.4DProject"},
        ):
            original = self.status.copy()
            self.status.update(updates)
            self.write_status()
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.package()
            self.status = original

    def test_empty_libraries_rejected(self):
        (self.component / "Libraries" / "lib4d-arm64.dylib").unlink()
        with self.assertRaisesRegex(ValueError, "Libraries"):
            self.package()

    def test_stale_compile_provenance_rejected(self):
        paths = [
            self.root / "template.json", self.root / "package.json", self.root / "LICENSE",
            self.project, self.project.parent / "Sources" / "settings.4DSettings",
            self.component / "Documentation" / "Classes" / "Example.md",
            self.mac, self.windows, self.component / "Resources" / "logo.svg",
        ]
        for path in paths:
            original = path.read_bytes()
            updated = original + (b" " if path.suffix in {".json", ".4DProject", ".4DSettings"} else b"changed")
            path.write_bytes(updated)
            with self.subTest(path=path.name), self.assertRaisesRegex(ValueError, "input_sha256"):
                self.package()
            path.write_bytes(original)
        self.status.pop("input_sha256")
        self.marker.write_text(json.dumps(self.status), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "input_sha256"):
            self.package()

    def test_generated_syntax_does_not_invalidate_but_compiled_tampering_does(self):
        (self.component / "Resources/en.lproj/syntaxEN.json").write_text(
            '{"generated":true}', encoding="utf-8")
        self.package()
        (self.project.parent / "DerivedData/CompiledCode/map.json").write_text(
            '{"generated":true}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "output_sha256"):
            self.package()

    def test_required_compiled_code_rejected_if_missing_or_empty(self):
        compiled = self.project.parent / "DerivedData" / "CompiledCode"
        for path in (compiled / "classes.json", compiled / "map.json", compiled / "MX64 123"):
            content = path.read_bytes()
            for absent in (False, True):
                if absent:
                    path.unlink()
                else:
                    path.write_bytes(b"")
                with self.subTest(path=path.name, absent=absent), self.assertRaisesRegex(
                        ValueError, "compiled|MX64"):
                    self.package()
                path.write_bytes(content)

    def test_deterministic_zip_root_modes_and_exclusions(self):
        for directory in ("Data", "Logs", "DerivedData", ".git"):
            path = self.component / directory / "excluded"
            path.parent.mkdir()
            path.write_text("exclude", encoding="utf-8")
        first = self.package().read_bytes()
        self.mac.chmod(0o600)
        self.assertEqual(first, self.package().read_bytes())
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            names = archive.namelist()
            prefix = "example-component.4dbase/"
            self.assertTrue(all(name.startswith(prefix) for name in names))
            self.assertIn(prefix + "example-component.4DZ", names)
            self.assertFalse(any(name.startswith(prefix + "Project/") for name in names))
            with zipfile.ZipFile(io.BytesIO(archive.read(prefix + "example-component.4DZ"))) as packed:
                inner = packed.namelist()
                self.assertIn("Project/example-component.4DProject", inner)
                for directory in ("Project/", "Project/Sources/", "Project/DerivedData/",
                                  "Project/DerivedData/CompiledCode/"):
                    entry = packed.getinfo(directory)
                    self.assertTrue(entry.is_dir())
                    self.assertTrue(stat.S_ISDIR(entry.external_attr >> 16))
                    self.assertEqual((entry.external_attr >> 16) & 0o777, 0o755)
                for metadata in ("settings.4DSettings", "catalog.4DCatalog", "dependencies.json", "folders.json"):
                    self.assertIn("Project/Sources/" + metadata, inner)
                for name in ("classes.json", "map.json", "MX64 123", "IX64 123"):
                    self.assertIn("Project/DerivedData/CompiledCode/" + name, inner)
                for name in ("methodAttributes.json", "formAttributes.json", "webservices.json"):
                    self.assertIn("Project/DerivedData/" + name, inner)
                self.assertNotIn("Project/DerivedData/excluded-cache.json", inner)
                self.assertFalse(any(name.endswith(".4dm") or "/Classes/" in name
                                     or "/Methods/" in name or "/Libraries/" in name for name in inner))
                self.assertTrue(all(entry.date_time == (1980, 1, 1, 0, 0, 0)
                                    for entry in packed.infolist()))
            self.assertIn(prefix + "Libraries/lib4d-arm64.dylib", names)
            self.assertIn(prefix + "Documentation/Classes/Example.md", names)
            for name in ("logo.png", "logo.svg", "en.lproj/syntaxEN.json"):
                self.assertIn(prefix + "Resources/" + name, names)
            self.assertEqual(archive.read(prefix + "Resources/licenses/LICENSE"), b"test license\n")
            self.assertNotIn(prefix + "LICENSE", names)
            self.assertFalse(any("excluded" in name for name in names))
            for entry in archive.infolist():
                self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                mode = entry.external_attr >> 16
                self.assertTrue(stat.S_ISDIR(mode) if entry.is_dir() else stat.S_ISREG(mode))
                executable = "/bin/" in entry.filename or entry.filename.endswith("lib4d-arm64.dylib")
                self.assertEqual(mode & 0o777, 0o755 if entry.is_dir() or executable else 0o644)

    def test_unpack_actual_archive_preserves_resources_and_compiled_project(self):
        archive = self.package()
        component = package_component.unpack_for_test(self.root, archive)
        self.assertEqual(component, self.root / "build" / "release-test" / "example-component.4dbase")
        self.assertEqual((component / "Resources/bin/macOS/example-cli").read_bytes(), synthetic_universal())
        self.assertEqual((component / "Resources/bin/macOS/example-cli").stat().st_mode & 0o777, 0o755)
        self.assertTrue((component / "example-component.4DZ").is_file())
        self.assertEqual((component / "Libraries/lib4d-arm64.dylib").stat().st_mode & 0o777, 0o755)
        self.assertFalse((component / "Project").exists())
        stale = component / "stale"
        stale.write_text("previous run", encoding="utf-8")
        package_component.unpack_for_test(self.root, archive)
        self.assertFalse(stale.exists())

    def test_unpack_rejects_unsafe_archive_and_missing_packed_project(self):
        cases = [
            "../escape", "/escape", "other.4dbase/file",
            "example-component.4dbase/../escape", "example-component.4dbase/readme",
        ]
        for name in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                package_component.unpack_for_test(self.root, io.BytesIO(self.archive_bytes(name)))
        with self.assertRaisesRegex(ValueError, "unsafe"):
            package_component.unpack_for_test(self.root, io.BytesIO(
                self.archive_bytes("example-component.4dbase/link", mode=stat.S_IFLNK | 0o777)))

    @unittest.skipUnless(os.name == "posix", "symlink creation requires POSIX")
    def test_symlinks_rejected(self):
        (self.component / "escape").symlink_to(self.root / "LICENSE")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.package()

    def test_native_mismatch_and_wrong_build_language(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(build_cli, "native_target", return_value="macos-arm64"):
            with self.assertRaisesRegex(ValueError, "native host"):
                build_cli.build(self.root, "macos-x64")
            with self.assertRaisesRegex(ValueError, "language"):
                build_cli.build(self.root, "macos-arm64", "rust")

    def test_toolchain_minimums_allow_supported_ci_pins(self):
        versions = [
            ("go", "go version go1.23.0 darwin/arm64", True),
            ("go", "go version go1.25.1 darwin/arm64", True),
            ("go", "go version go1.26.10 darwin/arm64", True),
            ("go", "go version go1.22.12 darwin/arm64", False),
            ("rust", "rustc 1.82.0 (test)", True),
            ("rust", "rustc 1.91.0 (test)", True),
            ("rust", "rustc 1.99.1 (test)", True),
            ("rust", "rustc 1.81.0 (test)", False),
        ]
        for language, version, valid in versions:
            with self.subTest(language=language, version=version), patch.object(
                    build_cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, version)):
                if valid:
                    build_cli.require_toolchain(language)
                else:
                    with self.assertRaisesRegex(ValueError, "or newer"):
                        build_cli.require_toolchain(language)

    def test_release_requires_opt_in(self):
        environment = {
            "GITHUB_REPOSITORY": "owner/example-component", "GITHUB_REF_NAME": "v0.1.0",
            "FOURD_ENABLED": "false",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
                sys, "argv", ["ci_config.py", "validate", "--release", "--root", str(self.root)]):
            with self.assertRaises(SystemExit) as caught:
                ci_config.main()
            self.assertEqual(caught.exception.code, 2)

    def test_manual_release_validates_explicit_tag_not_selected_branch_name(self):
        environment = {"GITHUB_REPOSITORY": "owner/example-component",
                       "GITHUB_REF_NAME": "main", "FOURD_ENABLED": "true"}
        with patch.dict(os.environ, environment, clear=True), patch.object(
                sys, "argv", ["ci_config.py", "validate", "--release", "--tag", "v0.1.0",
                              "--root", str(self.root)]):
            ci_config.main()

    def archive_bytes(self, name, data=b"tool4d", mode=stat.S_IFREG | 0o755):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, data)
        return output.getvalue()

    def download(self, data, digest=None):
        response = io.BytesIO(data)
        response.geturl = lambda: "https://resources-download.4d.com/fixed/tool4d.zip"
        environment = {
            "TOOL4D_URL": "https://resources-download.4d.com/fixed/tool4d.zip",
            "TOOL4D_SHA256": digest or hashlib.sha256(data).hexdigest(),
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
                ci_config, "open_tool4d", return_value=response):
            return ci_config.download_tool4d(self.root)

    def test_verified_tool4d_download_and_permissions(self):
        executable = self.download(self.archive_bytes("tool4d.app/Contents/MacOS/tool4d"))
        self.assertEqual(executable.read_bytes(), b"tool4d")
        self.assertEqual(executable.stat().st_mode & 0o777, 0o755)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.download(self.archive_bytes("tool4d.app/Contents/MacOS/tool4d"))
        self.assertEqual(executable.read_bytes(), b"tool4d")

    def test_tool4d_digest_and_unsafe_zip_rejected(self):
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.download(self.archive_bytes("tool4d.app/Contents/MacOS/tool4d"), "0" * 64)
        for name in ("../escape", "/escape", "C:/escape", r"..\escape"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unsafe"):
                self.download(self.archive_bytes(name))
        with self.assertRaisesRegex(ValueError, "non-regular"):
            self.download(self.archive_bytes("link", mode=stat.S_IFLNK | 0o777))

    def tar_bytes(self, entries):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:xz") as archive:
            for name, content, link in entries:
                entry = tarfile.TarInfo(name)
                if link is not None:
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = link
                else:
                    entry.size = len(content)
                    entry.mode = 0o755
                archive.addfile(entry, io.BytesIO(content) if link is None else None)
        return output.getvalue()

    @unittest.skipUnless(os.name == "posix", "app framework symlinks require POSIX")
    def test_tar_xz_tool4d_and_internal_framework_links(self):
        data = self.tar_bytes([
            ("tool4d.app/Contents/MacOS/tool4d", b"static binary", None),
            ("tool4d.app/Contents/Frameworks/F.framework/Versions/A/F", b"framework", None),
            ("tool4d.app/Contents/Frameworks/F.framework/Versions/Current", b"", "A"),
            ("tool4d.app/Contents/Frameworks/F.framework/F", b"", "Versions/Current/F"),
        ])
        executable = self.download(data)
        self.assertEqual(executable.read_bytes(), b"static binary")
        framework = executable.parents[1] / "Frameworks/F.framework/F"
        self.assertTrue(framework.is_symlink())
        self.assertEqual(framework.read_bytes(), b"framework")

    def test_tar_traversal_and_external_links_rejected(self):
        entries = [
            [("../escape", b"bad", None)],
            [("tool4d.app/link", b"", "../../escape")],
            [("tool4d.app/link", b"", "/escape")],
            [("tool4d.app/link", b"", "other"), ("tool4d.app/link/file", b"bad", None)],
        ]
        for case in entries:
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, "unsafe|symlink"):
                self.download(self.tar_bytes(case))

    def test_duplicate_archive_paths_rejected(self):
        data = self.tar_bytes([("tool4d.app/file", b"one", None),
                               ("tool4d.app/./file", b"two", None)])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.download(data)

    def test_download_requires_concrete_pinned_url(self):
        with patch.dict(os.environ, {"TOOL4D_URL": "https://resources-download.4d.com/latest/tool4d.tar.xz",
                                     "TOOL4D_SHA256": "0" * 64}, clear=True):
            with self.assertRaisesRegex(ValueError, "concrete build"):
                ci_config.download_tool4d(self.root)


if __name__ == "__main__":
    unittest.main()
