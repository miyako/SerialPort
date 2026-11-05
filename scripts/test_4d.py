#!/usr/bin/env python3
"""Exercise the namespaced API from an actual host project using free tool4d."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import shutil
import uuid


def _pointer(value, path):
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise ValueError("contract paths must be JSON pointers")
    for token in path.split("/")[1:] if path else []:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ValueError("missing host contract path: " + path)
    return value


def _equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _assertions(value, assertions):
    if not isinstance(assertions, list):
        raise ValueError("contract assertions must be an array")
    for assertion in assertions:
        if not isinstance(assertion, dict) or "path" not in assertion or not (
                set(assertion) & {"equals", "length", "each", "absent", "matches"}):
            raise ValueError("invalid host contract assertion")
        if set(assertion) - {"path", "equals", "length", "each", "absent", "matches"}:
            raise ValueError("unknown host contract assertion field")
        path = assertion["path"]
        if "absent" in assertion:
            if assertion["absent"] is not True or set(assertion) != {"path", "absent"}:
                raise ValueError("absent assertions require only path and absent:true")
            try:
                _pointer(value, path)
            except ValueError as error:
                if str(error).startswith("missing host contract path:"):
                    continue
                raise
            raise ValueError("host contract path must be absent: " + path)
        actual = _pointer(value, path)
        if "equals" in assertion and not _equal(actual, assertion["equals"]):
            raise ValueError("host contract value mismatch: " + path)
        if "matches" in assertion:
            pattern = assertion["matches"]
            if not isinstance(actual, str) or not isinstance(pattern, str) or not pattern:
                raise ValueError("host matches assertion requires Text and a nonempty pattern")
            try:
                matched = re.fullmatch(pattern, actual)
            except re.error as error:
                raise ValueError("invalid host contract regex: " + str(error)) from error
            if matched is None:
                raise ValueError("host contract pattern mismatch: " + path)
        if "length" in assertion:
            length = assertion["length"]
            if type(length) is not int or length < 0 or not isinstance(actual, list) or len(actual) != length:
                raise ValueError("host contract array length mismatch: " + path)
        if "each" in assertion:
            if not isinstance(actual, list) or not actual or not assertion["each"]:
                raise ValueError("host contract each requires a nonempty array: " + path)
            for entry in actual:
                _assertions(entry, assertion["each"])


def _success_result(result, feature=False):
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("host success result must have ok:true")
    if (not isinstance(result.get("stdout"), str) or not result["stdout"]
            or not isinstance(result.get("stderr"), str)
            or type(result.get("exitCode")) is not int or result["exitCode"] != 0):
        raise ValueError("host success result requires nonempty stdout, stderr and exitCode:0")
    if "error" in result:
        raise ValueError("host success result must not contain an error")
    if feature and ("value" not in result or result["value"] is None):
        raise ValueError("host feature success result requires a value")


def validate_status(status, contract, expect_compiled):
    """Validate reusable completion/results and fixture-owned JSON assertions."""
    if (not isinstance(status, dict) or status.get("success") is not True
            or status.get("finished") is not True
            or status.get("compiled") is not expect_compiled):
        raise ValueError("4D host requires success:true, finished:true and exact compiled mode")
    if not isinstance(contract, dict) or type(contract.get("schema")) is not int or contract["schema"] != 1:
        raise ValueError("unsupported host contract schema")
    feature = contract.get("feature")
    execution = contract.get("execution")
    if not isinstance(feature, dict) or not isinstance(execution, dict):
        raise ValueError("host contract requires feature and execution sections")
    cases = _pointer(status, feature.get("casesPath"))
    expected = feature.get("cases")
    count = feature.get("caseCount")
    if expected is not None and count is not None:
        raise ValueError("choose exact feature cases or semantic caseCount, not both")
    if expected is None:
        if type(count) is not int or count <= 0 or not feature.get("caseAssertions"):
            raise ValueError("semantic feature cases require positive caseCount and nonempty caseAssertions")
    elif not isinstance(expected, list) or not expected:
        raise ValueError("host contract requires all nonempty feature cases")
    if not isinstance(cases, list) or len(cases) != (count if expected is None else len(expected)):
        raise ValueError("host contract requires all nonempty feature cases")
    for index, result in enumerate(cases):
        _success_result(result, feature=True)
        if expected is None:
            _assertions(result, feature["caseAssertions"])
            continue
        fields = expected[index]
        if not isinstance(fields, dict) or not {"stdout", "stderr", "exitCode", "value"} <= fields.keys():
            raise ValueError("feature contract cases require stdout, stderr, exitCode and value")
        for key, value in fields.items():
            if key not in result or not _equal(result[key], value):
                raise ValueError("host feature case mismatch: " + key)
    paths = execution.get("successPaths")
    if not isinstance(paths, list):
        raise ValueError("execution successPaths must be an array")
    for path in paths:
        _success_result(_pointer(status, path))
    for section in (feature, execution):
        assertions = section.get("assertions")
        if not isinstance(assertions, list) or not assertions:
            raise ValueError("feature and execution assertions must be nonempty arrays")
        _assertions(status, assertions)
    return status


def _read_json(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("invalid JSON constant: " + value)

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def _no_symlinks(path, recursive=True):
    path = Path(path).absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("host staging input must not contain symlinks: " + str(path))
    if recursive and path.is_dir():
        for directory, folders, files in os.walk(path, followlinks=False):
            for name in folders + files:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("host staging input must not contain symlinks: " + str(path))


def _host_path_prefix(label, windows=None):
    if windows is None:
        windows = os.name == "nt"
    if windows:
        return label + " 日本語 path"
    return label + " 日本語 ' \" quote"


def test_4d(root, tool4d, component=None):
    _no_symlinks(root, recursive=False)
    root = Path(root).resolve()
    config = _read_json(root / "template.json")
    if not config["initialized"]:
        raise ValueError("initialize the destination before running 4D host tests")
    fixture = root / "tests" / "host"
    source = Path(component).absolute() if component is not None else root / "component" / config["repository"]
    if not source.is_dir():
        raise ValueError("host component must be an existing directory")
    _no_symlinks(fixture)
    _no_symlinks(source)
    contract = _read_json(fixture / "contract.json")
    build = root / "build"
    output = build / "host-status.json"
    _no_symlinks(build, recursive=False)
    _no_symlinks(output, recursive=False)
    build.mkdir(exist_ok=True)
    output.unlink(missing_ok=True)
    staging = build / (_host_path_prefix("4D host") + " " + uuid.uuid4().hex)
    staging.mkdir()
    try:
        host = staging / "Host"
        shutil.copytree(fixture, host,
                        ignore=shutil.ignore_patterns("DerivedData", "userPreferences.*", "Data", "Logs"))
        (host / "environment4d.json").unlink()
        package = host / "Components" / (config["repository"] + ".4dbase")
        ignored = ["userPreferences.*", "Data", "Logs"]
        if component is None:
            ignored.append("DerivedData")
        shutil.copytree(source, package, ignore=shutil.ignore_patterns(*ignored))
        platform = "Windows" if os.name == "nt" else "macOS"
        executable_name = config["cli"] + (".exe" if os.name == "nt" else "")
        native = package / "Resources/bin" / platform / executable_name
        if not native.is_file() or native.is_symlink():
            raise ValueError("host test needs the configured native CLI resource")
        executable = host / (_host_path_prefix("renamed executable") + (".exe" if os.name == "nt" else ""))
        shutil.copy2(native, executable)
        executable.chmod(0o755)
        request = host / (_host_path_prefix("host request") + ".json")
        staged_output = host / (_host_path_prefix("host status") + ".json")
        request.write_text(json.dumps({"status": str(staged_output),
                                      "expectCompiled": component is not None,
                                      "executable": str(executable)}, ensure_ascii=False) + "\n",
                           encoding="utf-8")
        subprocess.run([str(Path(tool4d).resolve()), str(host / "Project" / "Host.4DProject"),
                        "--skip-onstartup", "--startup-method=Smoke", "--dataless",
                        "--user-param=" + str(request)], check=True, timeout=120)
        _no_symlinks(staged_output, recursive=False)
        if not staged_output.is_file() or staged_output.is_symlink():
            raise ValueError("tool4d did not write host-status.json; host execution is unverified")
        output.write_bytes(staged_output.read_bytes())
        status = _read_json(output)
        return validate_status(status, contract, component is not None)
    finally:
        shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tool4d", type=Path, required=True)
    parser.add_argument("--component", type=Path, help="load this unpacked .4dbase instead of the source project")
    args = parser.parse_args()
    try:
        test_4d(args.root, args.tool4d, args.component)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"4D host tests failed: {error}", file=sys.stderr)
        return 1
    print("4D host contract passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
