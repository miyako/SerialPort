"""Release refs use real local Git tags; no workflows or releases are invoked."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_config
import release_ref


class ReleaseRefTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / (".release-ref-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.git("init", "-q")
        self.git("config", "user.name", "Release fixture")
        self.git("config", "user.email", "release-fixture@example.invalid")
        self.git("commit", "-q", "--allow-empty", "-m", "first")
        self.sha = self.git("rev-parse", "HEAD")
        self.git("tag", "v0.1.0")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout.strip()

    def resolve(self, **args):
        defaults = {"event": "workflow_dispatch", "version_tag": "v0.1.0"}
        defaults.update(args)
        return release_ref.resolve(self.root, **defaults)

    def test_manual_branch_resolves_existing_tag_not_selected_branch(self):
        self.git("commit", "-q", "--allow-empty", "-m", "branch advanced")
        metadata = self.resolve(ref_type="branch", ref_name="main",
                                event_sha=self.git("rev-parse", "HEAD"))
        self.assertEqual(metadata, {"tag": "v0.1.0", "sha": self.sha})

    def test_push_tag_requires_event_commit(self):
        self.assertEqual(self.resolve(event="push", ref_type="tag", ref_name="v0.1.0",
                                      event_sha=self.sha),
                         {"tag": "v0.1.0", "sha": self.sha})

    def test_push_branch_and_unsupported_events_fail(self):
        for args in ({"event": "push", "ref_type": "branch"},
                     {"event": "pull_request"}, {"event": "workflow_call"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.resolve(**args)

    def test_annotated_tag_peels_to_commit(self):
        self.git("tag", "-a", "v0.2.0", "-m", "annotated")
        self.assertNotEqual(self.git("rev-parse", "refs/tags/v0.2.0"), self.sha)
        self.assertEqual(self.resolve(version_tag="v0.2.0")["sha"], self.sha)

    def test_semver_prerelease_and_build_metadata(self):
        tag = "v1.2.3-rc.1+build-4"
        self.git("tag", tag)
        self.assertEqual(self.resolve(version_tag=tag)["sha"], self.sha)

    def test_malformed_and_arbitrary_refs_rejected_before_git(self):
        for tag in ("", "main", "refs/tags/v0.1.0", "v01.2.3", "v1.2",
                    "v1.2.3-01", "v1.2.3\nsha=evil", "v1.2.3^{commit}",
                    "--help", "v1.2.3;echo x", "v١.2.3", None):
            with self.subTest(tag=tag), patch.object(release_ref.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    self.resolve(version_tag=tag)
                run.assert_not_called()

    def test_missing_tag_fails(self):
        with self.assertRaisesRegex(ValueError, "already exist"):
            self.resolve(version_tag="v9.9.9")

    def test_tag_pointing_at_blob_fails(self):
        (self.root / "blob").write_text("not a commit", encoding="utf-8")
        blob = self.git("hash-object", "-w", "blob")
        self.git("tag", "v0.3.0", blob)
        with self.assertRaisesRegex(ValueError, "point to a commit"):
            self.resolve(version_tag="v0.3.0")

    def test_verify_expected_commit_and_invalid_sha(self):
        self.assertEqual(release_ref.verify(self.root, "v0.1.0", self.sha), self.sha)
        for sha in ("", self.sha[:7], "HEAD", "A" * 40, "f" * 41, None):
            with self.subTest(sha=sha), self.assertRaisesRegex(ValueError, "full normalized"):
                release_ref.verify(self.root, "v0.1.0", sha)

    def test_moved_tag_fails_push_and_publish(self):
        self.git("commit", "-q", "--allow-empty", "-m", "second")
        self.git("tag", "-f", "v0.1.0")
        with self.assertRaisesRegex(ValueError, "moved"):
            release_ref.verify(self.root, "v0.1.0", self.sha)
        with self.assertRaisesRegex(ValueError, "moved"):
            self.resolve(event="push", ref_type="tag", ref_name="v0.1.0",
                         event_sha=self.sha)

    def test_deleted_tag_fails_publish(self):
        self.git("tag", "-d", "v0.1.0")
        with self.assertRaisesRegex(ValueError, "already exist"):
            release_ref.verify(self.root, "v0.1.0", self.sha)

    def test_git_uses_literal_tag_ref_argument_and_rejects_bad_output(self):
        result = subprocess.CompletedProcess([], 0, stdout="not-a-sha\n", stderr="")
        with patch.object(release_ref.subprocess, "run", return_value=result) as run:
            with self.assertRaisesRegex(ValueError, "invalid commit SHA"):
                release_ref.tag_commit(self.root, "v0.1.0")
            self.assertEqual(run.call_args.args[0],
                             ["git", "rev-parse", "--verify", "refs/tags/v0.1.0^{commit}"])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_cli_outputs_only_validated_single_line_metadata(self):
        output = self.root / "github-output"
        output.write_text("previous=step\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/release_ref.py"), "--root", str(self.root),
             "resolve", "--event", "workflow_dispatch", "--version-tag", "v0.1.0"],
            env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True,
            text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = f"tag=v0.1.0\nsha={self.sha}\n"
        self.assertEqual(result.stdout, expected)
        self.assertEqual(result.stderr, "")
        self.assertEqual(output.read_text(encoding="utf-8"), "previous=step\n" + expected)

    def test_cli_failure_leaves_metadata_unchanged(self):
        output = self.root / "github-output"
        output.write_text("previous=step\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/release_ref.py"), "--root", str(self.root),
             "resolve", "--event", "workflow_dispatch", "--version-tag", "v9.9.9"],
            env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True,
            text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("already exist", result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous=step\n")

    def test_existing_configuration_validator_checks_version(self):
        config = ci_config.load_config(ROOT)
        self.assertEqual(ci_config.load_config(ROOT, tag="v" + config["version"]), config)
        with self.assertRaisesRegex(ValueError, "template.json version"):
            ci_config.load_config(ROOT, tag="v999999.0.0")

    def test_workflows_pin_every_post_gate_checkout_and_package_tag(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertEqual(ci.count("uses: actions/checkout@v4"), 3)
        self.assertEqual(ci.count("ref: ${{ inputs.source_sha || github.sha }}"), 3)
        self.assertEqual(ci.count("&& !inputs.release_call"), 4)
        self.assertIn("workflow_dispatch:", ci)
        self.assertIn("workflow_dispatch:", release)
        self.assertIn("fetch-depth: 0", release)
        self.assertIn("source_sha: ${{ needs.gate.outputs.source_sha }}", release)
        self.assertIn("release_call: true", release)
        self.assertEqual(release.count("ref: ${{ needs.gate.outputs.source_sha }}"), 2)
        self.assertEqual(release.count('validate --release --tag "$RELEASE_TAG"'), 2)
        self.assertIn('package_component.py --tag "$RELEASE_TAG" --unpack-test', release)
        self.assertLess(release.index("release_ref.py verify"), release.index("gh release create"))
        self.assertIn('git fetch --force --no-tags origin "+refs/tags/$RELEASE_TAG:refs/tags/$RELEASE_TAG"', release)
        self.assertIn("--verify-tag", release)


if __name__ == "__main__":
    unittest.main()
