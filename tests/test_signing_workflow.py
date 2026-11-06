"""Signing stays manual, credential-isolated and gated by downloaded-package checks."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
JOBS = dict(re.findall(r"^  (\w+):\n(.*?)(?=^  \w+:\n|\Z)",
                       WORKFLOW.split("jobs:\n", 1)[1], re.MULTILINE | re.DOTALL))
SECRETS = (
    "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
    "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID",
    "NOTARYTOOL_PASSWORD",
)


class SigningWorkflowTests(unittest.TestCase):
    def test_only_manual_signing_jobs_receive_secrets(self):
        manual_gate = "if: github.event_name == 'workflow_dispatch'"
        for name, body in JOBS.items():
            if name in ("signing_preflight", "signed_macos"):
                self.assertIn(manual_gate, body)
                self.assertIn("environment: macos-signing", body)
                for secret in SECRETS:
                    self.assertIn("${{ secrets." + secret + " }}", body)
            else:
                self.assertNotIn("${{ secrets.", body)
        self.assertNotIn("pull_request_target:", WORKFLOW)
        self.assertNotIn("contents: write", WORKFLOW)
        self.assertNotIn("secrets.CODESIGN_APPLE_ID", WORKFLOW)

    def test_missing_credentials_block_signing_but_normal_ci_can_run(self):
        self.assertIn("sign_component.py preflight", JOBS["signing_preflight"])
        self.assertIn("needs: [signing_preflight,", JOBS["signed_macos"])
        self.assertNotIn("signing_preflight", JOBS["configuration"])
        self.assertNotIn("continue-on-error", WORKFLOW)
        dispatch = WORKFLOW.split("workflow_dispatch:", 1)[1].split("workflow_call:", 1)[0]
        self.assertNotIn("inputs:", dispatch)
        self.assertNotIn("inputs.sign", WORKFLOW)

    def test_signed_job_requires_all_native_and_baseline_gates(self):
        signed = JOBS["signed_macos"]
        self.assertIn("needs: [signing_preflight, configuration, native, assembly]", signed)
        for target in ("macos-x64", "macos-arm64", "windows-x64"):
            self.assertIn("name: cli-" + target, signed)
            self.assertIn("path: build/" + target, signed)
        self.assertIn("python scripts/ci_config.py download-tool4d", signed)
        self.assertNotIn("FOURD_ENABLED", signed)

    def test_final_upload_follows_actual_download_and_host_acceptance(self):
        signed = JOBS["signed_macos"]
        build = signed.index("sign_component.py build")
        candidate = signed.index("name: SerialPort-macOS-signing-candidate")
        download = signed.index("name: Download the exact candidate bytes")
        verify = signed.index("sign_component.py verify")
        final = signed.index("name: SerialPort-macOS-signed-notarized")
        self.assertLess(build, candidate)
        self.assertLess(candidate, download)
        self.assertLess(download, verify)
        self.assertLess(verify, final)
        for name in ("SerialPort.zip", "SerialPort.dmg", "SerialPort.signing.json"):
            self.assertIn("build/downloaded-signed/" + name, signed[final:])
        final_upload = signed[final:].split("name: Preserve credential-free", 1)[0]
        self.assertNotIn("if: always()", final_upload)

    def test_unconditional_cleanup_precedes_final_artifact(self):
        signed = JOBS["signed_macos"]
        cleanup = signed.index("name: Delete isolated signing credentials")
        final = signed.index("name: Upload the accepted macOS")
        self.assertLess(cleanup, final)
        self.assertIn("if: always()", signed[cleanup:final])
        self.assertIn("sign_component.py cleanup --root .", signed[cleanup:final])
        self.assertNotIn("${{ secrets.", signed[cleanup:final])

    def test_diagnostics_cannot_include_private_keychain_files(self):
        diagnostics = JOBS["signed_macos"].split("name: Preserve credential-free signing diagnostics", 1)[1]
        self.assertNotIn("RUNNER_TEMP", diagnostics)
        self.assertNotIn(".p12", diagnostics)
        self.assertNotIn("keychain", diagnostics)
        self.assertNotIn("**", diagnostics)
        self.assertIn("build/signing-status.json", diagnostics)


if __name__ == "__main__":
    unittest.main()
