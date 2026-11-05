#!/usr/bin/env python3
"""Run local acceptance via existing helpers; always label non-hosted coverage partial."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from build_cli import build, native_target, smoke_test
from build_provenance import commit, sha256
from ci_config import ROOT, TARGETS, assemble, load_config, require_tool4d_host
from compile_component import compile_component
from doctor import doctor
from package_component import package, unpack_for_test, validate_resources
from test_4d import test_4d


def accept(root, repository, tool4d, cross=False):
    root = Path(root).resolve()
    output = root / "build/acceptance.json"
    if any(path.is_symlink() for path in [output, *output.parents]):
        raise ValueError("acceptance output must not traverse symlinks")
    output.parent.mkdir(parents=True, exist_ok=True)
    names = ["unit", "icons", *("cli_build_" + target for target in TARGETS),
             *("cli_test_" + target for target in TARGETS), "cli_rosetta",
             "dependencies", "assembly", "source_host", "compile", "package", "compiled_host",
             "hosted_cli", "native_intel_4d", "windows_4d", "release_download", "signing"]
    report = {"schema": 1, "scope": "local", "release_ready": False, "result": "partial",
              "started_at": datetime.now(timezone.utc).isoformat(),
              "finished": False, "commit": commit(root), "steps": {name: {"status": "not_run", "reason": "not exercised"}
                                               for name in names}}
    def save():
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    def step(name, action):
        report["steps"][name] = {"status": "failed", "reason": "step did not complete"}
        save()
        value = action()
        report["steps"][name] = {"status": "passed"}
        if isinstance(value, dict):
            report["steps"][name]["details"] = value
        save()
        return value
    save()
    try:
        config = load_config(root, initialized=True, repository=repository)
        report["configuration"] = config
        report["doctor"] = doctor(root, tool4d)
        require_tool4d_host()
        if not Path(tool4d).is_file():
            raise ValueError("tool4d executable is missing")
        version = subprocess.run([str(Path(tool4d).resolve()), "--version"],
                                 check=True, capture_output=True, text=True, timeout=30)
        if not version.stdout.strip():
            raise ValueError("tool4d --version produced no version text; compiler identity is unverified")
        report["tool4d"] = {"version": version.stdout.strip(), "binary_sha256": sha256(tool4d)}
        native = native_target()
        def run(arguments):
            environment = os.environ.copy()
            environment["TEST_CLI_LANGUAGE"] = config["language"]
            subprocess.run(arguments, cwd=root, env=environment, check=True, timeout=900)
        step("unit", lambda: run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]))
        step("icons", lambda: run([sys.executable, "scripts/generate_icons.py", "--check"]))
        targets = TARGETS if cross else (native,)
        for target in targets:
            step("cli_build_" + target, lambda target=target: build(root, target, cross=cross))
            report["steps"]["cli_test_" + target] = (
                {"status": "passed"} if target == native else
                {"status": "not_run", "reason": "cross-built only; not executed on its native OS"})
            save()
        if native == "macos-arm64" and "macos-x64" in targets:
            probe = subprocess.run(["arch", "-x86_64", "/usr/bin/true"], capture_output=True)
            if probe.returncode == 0:
                step("cli_rosetta", lambda: smoke_test(root / "build/macos-x64" / config["cli"],
                     config["version"], root / "tests/cli-contract.json", prefix=("arch", "-x86_64")))
            else:
                report["steps"]["cli_rosetta"]["reason"] = "Rosetta unavailable: " + probe.stderr.decode("utf-8", errors="replace")
        # Existing manifests are accepted only when bound to current source and commit.
        step("assembly", lambda: assemble(root))
        component = root / "component" / config["repository"]
        validate_resources(component, config["cli"])
        def dependencies():
            result = subprocess.run(["otool", "-L", str(component / "Resources/bin/macOS" / config["cli"])],
                                    capture_output=True, text=True, check=True)
            dependencies = [line.strip().split(" (", 1)[0] for line in result.stdout.splitlines()
                            if " (compatibility version " in line]
            extra = [item for item in dependencies if not item.startswith(("/usr/lib/", "/System/Library/"))]
            if extra:
                raise ValueError("non-system macOS dependencies need explicit packaging/review: " + repr(extra))
            return {"macOS": dependencies, "windows": "not inspected locally; verify native CI runtime/DLL dependencies"}
        step("dependencies", dependencies)
        source = step("source_host", lambda: test_4d(root, tool4d))
        (output.parent / "source-host-status.json").write_text(json.dumps(source, indent=2) + "\n")
        step("compile", lambda: compile_component(root, tool4d))
        archive = step("package", lambda: package(root, repository=repository, tag="v" + config["version"]))
        report["artifact"] = {"path": str(archive), "sha256": sha256(archive), "bytes": archive.stat().st_size}
        unpacked = unpack_for_test(root, archive)
        compiled = step("compiled_host", lambda: test_4d(root, tool4d, unpacked))
        (output.parent / "compiled-host-status.json").write_text(json.dumps(compiled, indent=2) + "\n")
        if sha256(archive) != report["artifact"]["sha256"]:
            raise ValueError("final archive changed during host acceptance")
        report["finished"] = True
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["reason"] = "Local package verified; hosted/native Intel4D/Windows4D/release/signing remain separate observations"
        save()
        evidence = archive.with_suffix(".acceptance.json")
        if evidence.is_symlink():
            raise ValueError("stable acceptance evidence must not be a symlink")
        evidence.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return report
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report.update(result="failed", finished=False, error=str(error))
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tool4d", type=Path, required=True)
    parser.add_argument("--cross", action="store_true", help="Go/macOS cross-build all targets, not foreign runtime proof")
    args = parser.parse_args()
    try:
        report = accept(args.root, args.repository, args.tool4d, args.cross)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(2, f"local acceptance failed (see build/acceptance.json): {error}\n")
    print(json.dumps({"result": report["result"], "release_ready": False,
                      "artifact": report["artifact"],
                      "evidence": str(Path(report["artifact"]["path"]).with_suffix(".acceptance.json"))}, indent=2))


if __name__ == "__main__":
    main()
