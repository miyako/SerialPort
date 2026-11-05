import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STARTERS = ROOT / "cli-starters"
ERROR = (
    b'{"protocol":1,"error":{"code":"invalid_arguments","message":'
    b'"expected greet --name <non-empty UTF-8 name> or --version"}}\n'
)


def json_line(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


class ContractTests:
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("TEST_CLI_LANGUAGE")
        if selected not in (None, "none", "go", "rust", "cpp"):
            raise RuntimeError("TEST_CLI_LANGUAGE must be none, go, rust, or cpp")
        if selected == "none":
            raise unittest.SkipTest("CLI toolchains disabled by TEST_CLI_LANGUAGE=none")
        if selected is not None and selected != cls.language:
            raise unittest.SkipTest("TEST_CLI_LANGUAGE selects " + selected)

        config_path = ROOT / "template.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        initialized = config.get("initialized", False) or (ROOT / "cli").is_dir()
        if initialized:
            configured = config.get("language")
            if configured not in ("go", "rust", "cpp"):
                raise RuntimeError("initialized template.json must configure a CLI language")
            if configured != cls.language:
                if selected is not None:
                    raise RuntimeError(
                        "TEST_CLI_LANGUAGE selects " + selected
                        + " but template.json configures " + configured
                    )
                raise unittest.SkipTest("initialized template selects " + configured)
            original = ROOT / "cli"
            executable_name = config["cli"]
        else:
            original = STARTERS / cls.language
            executable_name = "component-cli"
        if not original.is_dir():
            raise RuntimeError("missing CLI source directory: " + str(original))

        def unavailable(message):
            if selected is not None:
                raise RuntimeError("selected " + cls.language + " toolchain unavailable: " + message)
            raise unittest.SkipTest("unavailable toolchain: " + message)

        missing = [tool for tool in cls.tools if shutil.which(tool) is None]
        if missing:
            unavailable(", ".join(missing))
        compiler = None
        if cls.language == "cpp" and os.name != "nt":
            compiler = next(
                (shutil.which(tool) for tool in ("c++", "g++", "clang++") if shutil.which(tool)),
                None,
            )
            if compiler is None:
                unavailable("C++17 compiler")
        scratch = tempfile.TemporaryDirectory(prefix=".cli-test path ", dir=Path(__file__).parent)
        cls.addClassCleanup(scratch.cleanup)
        root = Path(scratch.name)
        source = root / "source"
        shutil.copytree(original, source)
        environment = os.environ.copy()
        executable_name += ".exe" if os.name == "nt" else ""
        execution_directory = root / "executables with spaces"
        execution_directory.mkdir()
        cls.executable = execution_directory / executable_name
        if cls.language == "go":
            environment.pop("GOOS", None)
            environment.pop("GOARCH", None)
            environment["CGO_ENABLED"] = "0"
            environment.update(
                GOCACHE=str(root / "go-cache"),
                GOPATH=str(root / "go-path"),
                GOTOOLCHAIN="local",
                GOPROXY="off",
                GOSUMDB="off",
            )
            commands = [["go", "build", "-o", str(cls.executable), "."]]
        elif cls.language == "rust":
            environment.pop("CARGO_BUILD_TARGET", None)
            environment["CARGO_TARGET_DIR"] = str(root / "target")
            built_executable = root / "target" / "release" / executable_name
            commands = [["cargo", "build", "--release", "--offline", "--locked"]]
        else:
            build = root / "build"
            built_executable = build / executable_name
            commands = [
                ["cmake", "-S", str(source), "-B", str(build),
                 "-DCMAKE_BUILD_TYPE=Release",
                 *([] if os.name == "nt" else ["-DCMAKE_CXX_COMPILER=" + compiler]),
                 *(["-A", "x64"] if os.name == "nt" else [])],
                ["cmake", "--build", str(build), "--config", "Release"],
            ]
        for command in commands:
            result = subprocess.run(
                command, cwd=source, env=environment, capture_output=True, timeout=180
            )
            if result.returncode:
                raise RuntimeError(
                    "build failed: " + repr(command) + "\n"
                    + result.stdout.decode("utf-8", errors="replace")
                    + result.stderr.decode("utf-8", errors="replace")
                )
        if cls.language == "cpp" and not built_executable.exists():
            built_executable = build / "Release" / executable_name
        if cls.language != "go":
            shutil.copy2(built_executable, cls.executable)

    def assert_result(self, arguments, status, stdout, stderr):
        result = subprocess.run(
            [os.fspath(self.executable), *arguments], capture_output=True, timeout=10
        )
        self.assertEqual(result.returncode, status)
        self.assertEqual(result.stdout, stdout)
        self.assertEqual(result.stderr, stderr)

    def test_version(self):
        self.assert_result(["--version"], 0, b'{"protocol":1,"version":"0.1.0"}\n', b"")

    def test_name(self):
        self.assertIn(" ", str(self.executable))
        name = '😀 "quoted" \\backslash; \'$()\''
        self.assert_result(
            ["greet", "--name", name], 0,
            json_line({"protocol": 1, "message": "Hello, " + name + "!"}), b"",
        )

    def test_names(self):
        names = [
            "World", "two words", " ", " leading and trailing ",
            "世界 café 😀", 'a"b\\c', "line\nbreak", "\t\r\b\f",
            "".join(chr(value) for value in range(1, 32)),
            "<>&/\u2028\u2029", "\u007f\u0080\u07ff\u0800\uffff\U0010ffff",
            "--version", "$(echo not-executed); `echo nope`",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assert_result(
                    ["greet", "--name", name], 0,
                    json_line({"protocol": 1, "message": "Hello, " + name + "!"}), b"",
                )

    def test_invalid_arguments(self):
        cases = [
            [], [""], ["unknown"], ["greet"], ["greet", "--name"],
            ["greet", "--name", ""], ["greet", "World"],
            ["greet", "--name=World"], ["greet", "--unknown", "World"],
            ["greet", "--name", "World", "--name", "Again"],
            ["greet", "--name", "World", "extra"],
            ["--name", "World", "greet"], ["greet", "--version"],
            ["--version", "extra"], ["--version", "--version"],
            ["greet", "--name", "World", "--version"],
            ["GREET", "--name", "World"], ["greet", "--Name", "World"],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assert_result(arguments, 2, b"", ERROR)

    @unittest.skipUnless(os.name == "posix", "raw byte arguments require POSIX")
    def test_malformed_utf8(self):
        malformed = [
            b"\x80", b"\xff", b"\xc0\xaf", b"\xc1\xbf", b"\xc2",
            b"\xe2\x82", b"\xe2(\xa1", b"\xe0\x80\x80", b"\xed\xa0\x80",
            b"\xf0\x80\x80\x80", b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80",
            b"\xf8\x88\x80\x80\x80", b"valid\xfe",
        ]
        for value in malformed:
            for arguments in (
                [b"greet", b"--name", value],
                [value, b"--name", b"World"],
                [b"greet", value, b"World"],
                [b"--version", value],
            ):
                with self.subTest(arguments=arguments):
                    self.assert_result(arguments, 2, b"", ERROR)


class GoCLI(ContractTests, unittest.TestCase):
    language = "go"
    tools = ("go",)


class RustCLI(ContractTests, unittest.TestCase):
    language = "rust"
    tools = ("cargo", "rustc")


class CppCLI(ContractTests, unittest.TestCase):
    language = "cpp"
    tools = ("cmake",)


if __name__ == "__main__":
    unittest.main()
