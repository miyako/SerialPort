#!/usr/bin/env python3
"""Plan an Overlay-style version bump without committing, tagging or publishing.

New tags are prefix-free X.Y.Z. Legacy vX.Y.Z tags remain read-only baselines.
apply validates every version surface before writing and can stage only those
paths. It never stages unrelated changes or changes dependency/tool versions.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from ci_config import ROOT, load_config
from initialize import github_repository


STABLE = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def parse_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON metadata key: " + key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique)


def stable(version):
    if not isinstance(version, str) or not re.fullmatch(STABLE, version, re.ASCII):
        raise ValueError("version must be X.Y.Z without a prefix or leading zeros")
    return tuple(map(int, version.split(".")))


def regular(root, relative):
    path = root / relative
    if not path.is_file() or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("version input must be a regular non-symlink file: " + str(relative))
    return path


def configuration(root, repository=None):
    for relative in ("template.json", "package.json"):
        parse_json(regular(root, relative).read_text(encoding="utf-8"))
    actual = github_repository(root)
    expected = repository or os.environ.get("GITHUB_REPOSITORY") or actual
    if not actual or expected != actual:
        raise ValueError("destination repository must exactly match the GitHub origin")
    if actual.lower() == "miyako/4d-component-template":
        raise ValueError("cannot bump the original template repository")
    config = load_config(root, initialized=True, repository=expected)
    if config["language"] != "cpp" or config["feature_class"] != "SerialPort":
        raise ValueError("version automation requires the adopted C++ SerialPort feature")
    stable(config["version"])
    return config


def plan(root, version, repository=None):
    root = Path(root).resolve()
    stable(version)
    config = configuration(root, repository)
    old = config["version"]
    updates = {}

    def save(relative, before, after):
        if before != after:
            updates[relative] = after

    def literal(relative, pattern, count):
        before = regular(root, relative).read_text(encoding="utf-8")
        matches = list(re.finditer(pattern, before, re.MULTILINE))
        if len(matches) != count or any(match["version"] != old for match in matches):
            raise ValueError("missing or inconsistent version surface: " + relative)
        after = re.sub(
            pattern, lambda match: match[0][:match.start("version") - match.start()] + version
            + match[0][match.end("version") - match.start():], before, flags=re.MULTILINE)
        save(relative, before, after)

    for relative in ("template.json", "package.json"):
        before = regular(root, relative).read_text(encoding="utf-8")
        value = parse_json(before)
        if not isinstance(value, dict) or value.get("version") != old:
            raise ValueError("inconsistent component metadata: " + relative)
        value["version"] = version
        if version != old:
            save(relative, before, json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    literal("cli/main.cpp", r'\{\s*"version"\s*,\s*"(?P<version>[^"\r\n]+)"\s*\}', 1)
    literal("README.md", r"^\*\*serial-port\*\*, version \*\*(?P<version>[^*\r\n]+)\*\*", 1)
    literal("cli/PROTOCOL.md", r"^# serial-port (?P<version>[^\r\n]+)", 1)
    protocol = r'"protocol"\s*:\s*1\s*,\s*"version"\s*:\s*"(?P<version>[^"\r\n]+)"'
    # Apply both protocol document surfaces to the same original content.
    before = regular(root, "cli/PROTOCOL.md").read_text(encoding="utf-8")
    matches = list(re.finditer(protocol, before))
    if len(matches) != 1 or matches[0]["version"] != old:
        raise ValueError("missing or inconsistent version surface: cli/PROTOCOL.md")
    after = re.sub(protocol, lambda match: match[0].replace('"' + old + '"', '"' + version + '"'),
                   updates.get("cli/PROTOCOL.md", before))
    save("cli/PROTOCOL.md", before, after)
    literal("tests/test_cli.py", protocol, 1)
    literal("tests/host/Project/Sources/Methods/Smoke.4dm",
            r'\.value\.version\s*[#=]\s*"(?P<version>[^"\r\n]+)"', 1)

    relative = "tests/host/contract.json"
    before = regular(root, relative).read_text(encoding="utf-8")
    contract = parse_json(before)
    if not isinstance(contract, dict) or type(contract.get("schema")) is not int or contract["schema"] != 1:
        raise ValueError("unsupported host version contract")

    def payload(value):
        serialized = isinstance(value, str)
        decoded = parse_json(value) if serialized else value
        if (not isinstance(decoded, dict) or type(decoded.get("protocol")) is not int
                or decoded["protocol"] != 1 or decoded.get("version") != old):
            raise ValueError("inconsistent host version response")
        result = dict(decoded, version=version)
        if serialized:
            return json.dumps(result, separators=(",", ":")) + ("\n" if value.endswith("\n") else "")
        return result

    expected = {
        ("feature", "/version/stdout"), ("feature", "/version/value"),
        ("execution", "/renamedExecutable"),
    }
    seen = set()
    for section in ("feature", "execution"):
        section_value = contract.get(section)
        assertions = section_value.get("assertions") if isinstance(section_value, dict) else None
        if not isinstance(assertions, list) or not all(isinstance(item, dict) for item in assertions):
            raise ValueError("invalid host version assertions")
        for assertion in assertions:
            key = (section, assertion.get("path"))
            if key not in expected:
                continue
            if key in seen or "equals" not in assertion:
                raise ValueError("host version assertions must be unique exact equals")
            value = assertion["equals"]
            assertion["equals"] = (dict(value, stdout=payload(value["stdout"]))
                                   if key[1] == "/renamedExecutable" else payload(value))
            seen.add(key)
    if seen != expected:
        raise ValueError("missing exact host version assertions")
    if contract != parse_json(before):
        save(relative, before, json.dumps(contract, indent=2, ensure_ascii=False) + "\n")

    native = parse_json(regular(root, "tests/cli-contract.json").read_text(encoding="utf-8"))
    if (not isinstance(native, dict) or type(native.get("schema")) is not int or native["schema"] != 1
            or not isinstance(native.get("cases"), list)
            or not all(isinstance(case, dict) for case in native["cases"])):
        raise ValueError("unsupported native version contract")
    cases = [case for case in native["cases"] if case.get("args") == ["--version"]]
    if (len(cases) != 1 or cases[0].get("stdout") != {"protocol": 1, "version": "$VERSION"}
            or type(cases[0]["stdout"]["protocol"]) is not int or "stdoutPattern" in cases[0]
            or type(cases[0].get("exitCode")) is not int or cases[0]["exitCode"] != 0
            or cases[0].get("stderr") != "" or "stderrPattern" in cases[0]):
        raise ValueError("native version contract must retain its exact $VERSION placeholder")
    return config, updates


def next_version(root, mode, repository=None, *, tags=None):
    config, _ = plan(root, configuration(Path(root).resolve(), repository)["version"], repository)
    if mode not in ("patch", "minor", "major"):
        raise ValueError("mode must be patch, minor, or major")
    if tags is None:
        result = subprocess.run(["git", "tag", "--list"], cwd=root, check=True,
                                capture_output=True, text=True, encoding="utf-8")
        tags = result.stdout.splitlines()
    if not isinstance(tags, (tuple, list)) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be a list of tag names")
    versions = [stable(tag.removeprefix("v")) for tag in tags if re.fullmatch("v?" + STABLE, tag, re.ASCII)]
    major, minor, patch = max(versions) if versions else stable(config["version"])
    value = {"patch": (major, minor, patch + 1), "minor": (major, minor + 1, 0),
             "major": (major + 1, 0, 0)}[mode]
    if stable(config["version"]) > value:
        raise ValueError("configured version is ahead of the next release; refusing a downgrade")
    version = ".".join(map(str, value))
    return {"version": version, "tag": version}


def apply(root, version, repository=None, *, stage=False):
    root = Path(root).resolve()
    _, updates = plan(root, version, repository)
    for relative, content in updates.items():
        (root / relative).write_text(content, encoding="utf-8")
    if stage and updates:
        subprocess.run(["git", "add", "--", *sorted(updates)], cwd=root, check=True,
                       capture_output=True, text=True, encoding="utf-8")
    return {"version": version, "tag": version, "changed_paths": sorted(updates)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repository", help="exact owner/name; defaults to GITHUB_REPOSITORY or origin")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("next").add_argument("--mode", choices=("patch", "minor", "major"), required=True)
    applier = commands.add_parser("apply")
    applier.add_argument("--version", required=True)
    applier.add_argument("--stage", action="store_true")
    args = parser.parse_args()
    try:
        result = (next_version(args.root, args.mode, args.repository) if args.command == "next"
                  else apply(args.root, args.version, args.repository, stage=args.stage))
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                for key, value in result.items():
                    stream.write(f"{key}={json.dumps(value) if isinstance(value, list) else value}\n")
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        parser.exit(2, f"bump_release: {error}\n")


if __name__ == "__main__":
    main()
