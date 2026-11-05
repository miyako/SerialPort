import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compile_component", ROOT / "scripts/compile_component.py")
compiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compiler)


class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".compile-tests-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        config = json.loads((ROOT / "template.json").read_text())
        config["initialized"] = True
        (self.root / "template.json").write_text(json.dumps(config))
        for name in ("package.json", "LICENSE"):
            shutil.copy2(ROOT / name, self.root / name)
        self.name = config["repository"]
        self.feature_class = config.get("feature_class", "Greeting")
        shutil.copytree(ROOT / "component" / self.name, self.root / "component" / self.name,
                        ignore=shutil.ignore_patterns("DerivedData"))
        self.project = self.root / "component" / self.name / "Project" / (self.name + ".4DProject")
        self.status = self.root / "build/compile-status.json"

    def result(self, args, **kwargs):
        self.status.write_text(json.dumps({"success": True, "project": self.project.as_posix(),
                                         "targets": ["x86_64_generic", "arm64_macOS_lib"]}))
        self.assertNotIn("shell", kwargs)
        self.assertIn("--startup-method=Compile", args)
        self.assertTrue(any(a.startswith("--user-param=") for a in args))

    def test_real_result_required_and_stale_status_removed(self):
        self.status.parent.mkdir()
        self.status.write_text('{"success":true}')
        with patch.object(compiler.subprocess, "run"), self.assertRaisesRegex(ValueError, "did not write"):
            compiler.compile_component(self.root, "/missing/tool4d")
        self.assertFalse(self.status.exists())

    def test_failed_compilation_not_success_shaped(self):
        def failure(*args, **kwargs):
            self.status.write_text('{"success":false,"errors":[{"message":"bad class"}]}')
        with patch.object(compiler.subprocess, "run", side_effect=failure), self.assertRaisesRegex(ValueError, "bad class"):
            compiler.compile_component(self.root, "/fake/tool4d")

    def test_failed_process_propagates(self):
        with patch.object(compiler.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["tool4d"])):
            with self.assertRaises(subprocess.CalledProcessError):
                compiler.compile_component(self.root, "/fake/tool4d")

    def test_success_binds_input_digest_and_identity(self):
        with patch.object(compiler.subprocess, "run", side_effect=self.result):
            status = compiler.compile_component(self.root, "/fake/tool4d")
        self.assertEqual(status["input_sha256"], compiler.input_digest(self.root))
        path = self.project.parent / "Sources/Classes" / (self.feature_class + ".4dm")
        path.write_text(path.read_text() + "\n")
        self.assertNotEqual(status["input_sha256"], compiler.input_digest(self.root))

    def test_changed_sources_during_compile_rejected(self):
        def changed(*args, **kwargs):
            self.result(*args, **kwargs)
            path = self.project.parent / "Sources/Classes" / (self.feature_class + ".4dm")
            path.write_text(path.read_text() + "\n")
        with patch.object(compiler.subprocess, "run", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed during compilation"):
                compiler.compile_component(self.root, "/fake/tool4d")

    def test_wrong_target_and_project_rejected(self):
        def wrong(*args, **kwargs):
            self.status.write_text('{"success":true,"project":"wrong","targets":["x86_64_generic"]}')
        with patch.object(compiler.subprocess, "run", side_effect=wrong):
            with self.assertRaisesRegex(ValueError, "required project and targets"):
                compiler.compile_component(self.root, "/fake/tool4d")

    def test_output_processor_runs_before_output_digest_is_recorded(self):
        library = self.project.parent.parent / "Libraries/lib4d-arm64.dylib"
        def processor(component):
            self.assertEqual(component, self.project.parent.parent)
            self.assertNotIn("output_sha256", json.loads(self.status.read_text()))
            library.parent.mkdir(exist_ok=True)
            library.write_bytes(b"processed compiler output")
        with patch.object(compiler.subprocess, "run", side_effect=self.result):
            status = compiler.compile_component(self.root, "/fake/tool4d",
                                                postprocess_outputs=processor)
        self.assertEqual(status["output_sha256"], compiler.output_digest(self.project.parent.parent))
        library.write_bytes(b"post-marker mutation")
        self.assertNotEqual(status["output_sha256"], compiler.output_digest(self.project.parent.parent))

    def test_output_processor_cannot_mutate_inputs(self):
        def processor(component):
            (component / "Resources/changed").write_bytes(b"changed input")
        with patch.object(compiler.subprocess, "run", side_effect=self.result):
            with self.assertRaisesRegex(ValueError, "changed during output processing"):
                compiler.compile_component(self.root, "/fake/tool4d", postprocess_outputs=processor)
        self.assertNotIn("input_sha256", json.loads(self.status.read_text()))

    def test_output_processor_failure_cannot_create_valid_marker(self):
        def processor(component):
            raise RuntimeError("processing failed")
        with patch.object(compiler.subprocess, "run", side_effect=self.result):
            with self.assertRaisesRegex(RuntimeError, "processing failed"):
                compiler.compile_component(self.root, "/fake/tool4d", postprocess_outputs=processor)
        self.assertNotIn("output_sha256", json.loads(self.status.read_text()))

    @unittest.skipUnless(__import__("os").name == "posix", "symlink requires POSIX")
    def test_cleanup_does_not_follow_derived_data_symlink(self):
        outside = self.root / "sentinel-directory"
        outside.mkdir()
        sentinel = outside / "keep"
        sentinel.write_text("not compiler output")
        (self.project.parent / "DerivedData").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            compiler.compile_component(self.root, "/fake/tool4d")
        self.assertEqual(sentinel.read_text(), "not compiler output")
