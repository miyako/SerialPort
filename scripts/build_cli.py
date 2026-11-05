#!/usr/bin/env python3
"""Build the selected CLI; test native vectors, or explicitly cross-build Go on macOS."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

from ci_config import ROOT, TARGETS, load_config
from build_provenance import record, source_digest


def native_target():
    system = platform.system()
    machine = platform.machine().lower()
    architecture = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if system == "Darwin" and architecture in ("x64", "arm64"):
        # A translated x86 process on ARM is not a native Intel runner.
        translated = subprocess.run(
            ["sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True, check=False)
        if translated.stdout.strip() == "1":
            raise ValueError("Rosetta translation is not a native build host")
        return "macos-" + architecture
    if system == "Windows" and architecture == "x64":
        if os.environ.get("PROCESSOR_ARCHITEW6432", "").lower() in ("arm64", "aarch64"):
            raise ValueError("Windows ARM emulation is not a native x64 build host")
        return "windows-x64"
    raise ValueError(f"unsupported native host: {system}/{machine}")


def smoke_test(executable, version, contract, prefix=()):
    data = json.loads(Path(contract).read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(cases, list) or not cases:
        raise ValueError("CLI contract must contain schema:1 and nonempty cases")
    if (not any(isinstance(case, dict) and case.get("exitCode") == 0
                and case.get("args") != ["--version"] for case in cases)
            or not any(isinstance(case, dict) and type(case.get("exitCode")) is int
                       and case["exitCode"] > 0 for case in cases)):
        raise ValueError("CLI contract needs feature success and failure vectors, not only --version")
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("args"), list)
                or any(not isinstance(arg, str) for arg in case["args"])
                or type(case.get("exitCode")) is not int):
            raise ValueError("invalid CLI contract arguments/status")
        def expected(field):
            value = case[field]
            if isinstance(value, dict):
                value = json.loads(json.dumps(value).replace("$VERSION", version))
                return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            if not isinstance(value, str):
                raise ValueError("CLI contract streams must be Text or JSON objects")
            return value.encode("utf-8")
        def stream_matches(field, actual):
            pattern_key = field + "Pattern"
            if pattern_key in case:
                if field in case or not isinstance(case[pattern_key], str) or not case[pattern_key]:
                    raise ValueError("choose an exact stream or a nonempty stream Pattern, not both")
                try:
                    return re.fullmatch(case[pattern_key], actual.decode("utf-8")) is not None
                except (UnicodeDecodeError, re.error) as error:
                    raise ValueError("invalid UTF-8 stream or contract regex: " + str(error)) from error
            return actual == expected(field)
        result = subprocess.run([*prefix, str(executable), *case["args"]], capture_output=True, timeout=20)
        if (result.returncode != case["exitCode"] or not stream_matches("stdout", result.stdout)
                or not stream_matches("stderr", result.stderr)):
            raise ValueError(f"CLI contract failed: {case['args']!r}: {result!r}")


def require_toolchain(language):
    if language == "go":
        command, pattern, minimum = ["go", "version"], r"\bgo(\d+)\.(\d+)\.(\d+)\b", (1, 23, 0)
    else:
        command, pattern, minimum = ["rustc", "--version"], r"\brustc (\d+)\.(\d+)\.(\d+)\b", (1, 82, 0)
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    match = re.search(pattern, output)
    if not match or tuple(map(int, match.groups())) < minimum:
        raise ValueError(f"{language} {'.'.join(map(str, minimum))} or newer is required")


def build(root, target, language=None, cross=False):
    root = Path(root).resolve()
    config = load_config(root, initialized=True, repository=os.environ.get("GITHUB_REPOSITORY"))
    native = native_target()
    if target not in TARGETS or (native != target and not cross):
        raise ValueError("requested target does not match the native host architecture")
    selected = config["language"]
    if cross and (selected != "go" or not native.startswith("macos-")):
        raise ValueError("--cross is supported only for Go on a native macOS host; use native CI for Rust/C++")
    if language is not None and language != selected:
        raise ValueError("requested language does not match template.json")
    source = root / "cli"
    if not source.is_dir():
        raise ValueError("initialized CLI source is missing")
    output = root / "build" / target
    if any(path.is_symlink() for path in [output, *output.parents]):
        raise ValueError("CLI build output must not traverse symlinks")
    output.mkdir(parents=True, exist_ok=True)
    executable = output / (config["cli"] + (".exe" if target == "windows-x64" else ""))
    executable.unlink(missing_ok=True)
    env = os.environ.copy()
    original_digest = source_digest(root)
    toolchain = selected
    if target.startswith("macos-"):
        env["MACOSX_DEPLOYMENT_TARGET"] = "11.0"
    if selected == "go":
        require_toolchain("go")
        toolchain = subprocess.run(["go", "version"], check=True, capture_output=True, text=True).stdout.strip()
        env.update(CGO_ENABLED="0", GOTOOLCHAIN="local",
                   GOOS="windows" if target == "windows-x64" else "darwin",
                   GOARCH="arm64" if target == "macos-arm64" else "amd64")
        commands = [["go", "build", "-trimpath", "-o", str(executable), "."]]
    elif selected == "rust":
        require_toolchain("rust")
        toolchain = subprocess.run(["rustc", "--version"], check=True, capture_output=True, text=True).stdout.strip()
        env.pop("CARGO_BUILD_TARGET", None)
        env["CARGO_TARGET_DIR"] = str(output / "cargo")
        commands = [["cargo", "build", "--release", "--locked"]]
    else:
        architecture_options = (
            ["-A", "x64"] if target == "windows-x64" else
            ["-DCMAKE_OSX_ARCHITECTURES=" + ("arm64" if target == "macos-arm64" else "x86_64")])
        commands = [
            ["cmake", "-S", str(source), "-B", str(output / "cmake"),
             "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_STANDARD=17", "-DCMAKE_CXX_EXTENSIONS=OFF",
             *architecture_options],
            ["cmake", "--build", str(output / "cmake"), "--config", "Release"],
        ]
    for command in commands:
        subprocess.run(command, cwd=source, env=env, check=True, timeout=600)
    if target == native:
        if selected == "go":
            subprocess.run(["go", "test", "./..."], cwd=source, env=env, check=True, timeout=600)
        elif selected == "rust":
            subprocess.run(["cargo", "test", "--release", "--locked"], cwd=source, env=env, check=True, timeout=600)
        elif (output / "cmake/CTestTestfile.cmake").is_file():
            subprocess.run(["ctest", "--test-dir", str(output / "cmake"), "-C", "Release",
                            "--output-on-failure", "--no-tests=error"], env=env, check=True, timeout=600)
        else:
            print("C++ native unit tests not registered; Python CLI contract tests still required")
    if selected == "rust":
        shutil.copyfile(output / "cargo" / "release" / executable.name, executable)
    elif selected == "cpp":
        candidates = [output / "cmake" / executable.name, output / "cmake" / "Release" / executable.name]
        built = next((path for path in candidates if path.is_file()), None)
        if built is None:
            raise ValueError("CMake did not produce the configured executable")
        shutil.copyfile(built, executable)
    executable.chmod(0o755)
    if source_digest(root) != original_digest:
        raise ValueError("CLI inputs changed during build")
    if target == native:
        contract = root / "tests/cli-contract.json"
        if not contract.is_file():
            raise ValueError("tests/cli-contract.json is required; define feature smoke vectors")
        smoke_test(executable, config["version"], contract)
    record(root, target, executable, target == native, toolchain)
    return executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=TARGETS)
    parser.add_argument("--language", choices=("go", "rust", "cpp"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cross", action="store_true", help="Go on macOS only; foreign binaries are build-only")
    args = parser.parse_args()
    try:
        print(build(args.root, args.target, args.language, args.cross))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(2, f"CLI build failed: {error}\n")


if __name__ == "__main__":
    main()
