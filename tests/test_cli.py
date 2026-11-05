"""Native build plus CLI contract checks; all scratch stays in the repository."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
USAGE = "expected serve, list, echo --text <UTF-8 text> or --version"


class CppCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("TEST_CLI_LANGUAGE")
        if selected == "none":
            raise unittest.SkipTest("CLI toolchains explicitly disabled")
        if selected not in (None, "cpp"):
            raise RuntimeError("SerialPort requires C++")
        cls.build = ROOT / "build" / "cli-test"
        subprocess.run(["cmake", "-S", str(ROOT / "cli"), "-B", str(cls.build),
                        "-DCMAKE_BUILD_TYPE=Release",
                        *(["-A", "x64"] if os.name == "nt" else [])], check=True, timeout=180)
        subprocess.run(["cmake", "--build", str(cls.build), "--config", "Release"],
                       check=True, timeout=180)
        cls.executable = cls.build / ("serial-port.exe" if os.name == "nt" else "serial-port")
        if not cls.executable.exists():
            cls.executable = cls.build / "Release" / "serial-port.exe"

    def invoke(self, args):
        return subprocess.run([os.fspath(self.executable), *args], capture_output=True, timeout=10)

    def test_version_and_list(self):
        result = self.invoke(["--version"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'{"protocol":1,"version":"0.1.0"}\n')
        result = self.invoke(["list"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["protocol"], 1)
        self.assertIsInstance(json.loads(result.stdout)["ports"], list)
        self.assertEqual(result.stderr, b"")

    def test_echo_literals(self):
        for value in ["", "😀 世界 café", 'quotes" slash\\ \'$();`\'', "\n\t\r\b\f",
                      "".join(chr(n) for n in range(1, 32)), "\U0010ffff"]:
            with self.subTest(value=value):
                result = self.invoke(["echo", "--text", value])
                self.assertEqual(result.returncode, 0)
                self.assertTrue(result.stdout.isascii())
                self.assertEqual(json.loads(result.stdout), {"protocol": 1, "text": value})
                self.assertEqual(result.stderr, b"")

    def test_invalid_arguments(self):
        for args in [[], ["unknown"], ["greet"], ["echo"], ["echo", "--text"],
                     ["echo", "--name", "x"], ["--version", "extra"], ["serve", "extra"]]:
            result = self.invoke(args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(json.loads(result.stderr), {
                "protocol": 1, "error": {"code": "invalid_arguments", "message": USAGE}})

    @unittest.skipUnless(os.name == "posix", "raw UTF-8 argv requires POSIX")
    def test_invalid_utf8(self):
        for raw in [b"\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80"]:
            result = self.invoke([b"echo", b"--text", raw])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
