#!/usr/bin/env python3
"""Verify exact downloaded SerialPort signing evidence; explicitly publish prereleases only.

Ubuntu checks receipts and bytes, not Apple cryptography or 4D runtime execution.
Those gates must already have passed on the same-run macOS downloaded artifacts.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

from build_provenance import commit, sha256, source_digest, verify as verify_native
from ci_config import ROOT, TARGETS, load_config
from initialize import github_repository
from package_component import COMPILE_TARGETS, include_project
from release_ref import create_tag as create_release_tag, require_main, validate_tag, verify as verify_tag
from test_4d import _read_json, validate_status


REQUIRED_STEPS = (
    "preflight", "tool4d", "assembly", "sign_cli", "signed_cli_smoke", "source_host",
    "compile", "sign_libraries", "package", "signatures", "notarization", "compiled_host",
    "final_integrity", "downloaded_package_signatures", "downloaded_package_host",
    "dmg_create", "dmg_sign", "dmg_notarization", "dmg_staple", "dmg_verify",
    "dmg_host", "dmg_integrity", "downloaded_dmg_verify", "downloaded_dmg_host",
)
MACHO = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
         b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
         b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
NOTES = (
    "SerialPort prototype PRERELEASE; not a stable or latest release. "
    "The source-stripped compiled component contains universal macOS and x64 Windows CLI resources. "
    "macOS CLI and native Libraries are Developer ID signed; the exact ZIP and DMG have Accepted "
    "notarization with inspected logs. The signed DMG is stapled and Gatekeeper-assessed; "
    "the ZIP/bare CLI cannot be stapled and use online tickets. "
    "The exact Actions-downloaded ZIP and mounted DMG passed macOS compiled 4D host checks. "
    "Adjacent signing evidence binds hashes, revision, native CLI manifests and host reports. "
    "Physical serial hardware and native Windows/Intel 4D remain unverified; "
    "per-device streaming text decoding is not implemented and callbacks are byte chunks, not messages. "
    "Windows Authenticode is not implemented and the Windows executable is unsigned. "
    "Ubuntu publication validation does not rerun macOS cryptographic or 4D runtime checks."
)


def safe_path(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("release paths must not traverse symlinks or parent directories")
    return path


def digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("invalid SHA256: " + label)
    return value


def member_path(name):
    if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name
            or ":" in name or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))):
        raise ValueError("unsafe archive or inventory path")
    return PurePosixPath(name)


def json_bytes(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate archive JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("invalid archive JSON constant")
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def zip_entries(data):
    entries = {}
    normalized = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or sum(item.file_size for item in infos) > 512 * 1024 * 1024:
            raise ValueError("compiled ZIP exceeds validation bounds")
        for info in infos:
            if info.orig_filename != info.filename:
                raise ValueError("ZIP member filename contains a hidden terminator")
            path = member_path(info.filename)
            if path.as_posix() in normalized:
                raise ValueError("duplicate ZIP member")
            normalized.add(path.as_posix())
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if (info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    or kind != (stat.S_IFDIR if info.is_dir() else stat.S_IFREG)
                    or stat.S_IMODE(mode) not in {0o644, 0o755}
                    or info.is_dir() and stat.S_IMODE(mode) != 0o755
                    or path.suffix.lower() == ".4dm"):
                raise ValueError("ZIP must contain regular, source-stripped component entries")
            entries[info.filename] = (archive.read(info), stat.S_IMODE(mode))
    for name in entries:
        for parent in PurePosixPath(name).parents:
            if str(parent) != "." and parent.as_posix() + "/" not in entries:
                raise ValueError("ZIP requires explicit nested directory records")
    return entries


def signature(value, *, runtime=None, architectures=False):
    if not isinstance(value, dict) or any(
            value.get(field) is not True for field in ("developer_id", "team_matches", "secure_timestamp")):
        raise ValueError("Developer ID/team/timestamp signature metadata is required")
    if runtime is not None and value.get("runtime") is not runtime:
        raise ValueError("CLI/library runtime signature metadata mismatch")
    if architectures:
        arch = value.get("architectures")
        if (not isinstance(arch, list) or not arch or any(not isinstance(item, str) for item in arch)
                or len(set(arch)) != len(arch) or set(arch) - {"arm64", "x86_64"}
                or runtime is True and set(arch) != {"arm64", "x86_64"}):
            raise ValueError("signature architecture metadata mismatch")


def receipt(value, expected_hash):
    if (not isinstance(value, dict) or value.get("status") != "Accepted"
            or value.get("log_checked") is not True or value.get("archive_sha256") != expected_hash
            or type(value.get("log_issue_count")) is not int or value["log_issue_count"] < 0):
        raise ValueError("Accepted notarization and inspected matching log are required")
    identifier = value.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", identifier):
        raise ValueError("invalid notarization submission UUID")
    if uuid.UUID(identifier).int == 0:
        raise ValueError("invalid nil notarization submission UUID")


def artifact(path, value):
    if (not isinstance(value, dict) or value.get("path") != path.name
            or type(value.get("bytes")) is not int or value["bytes"] <= 0):
        raise ValueError("artifact name and positive byte count are required")
    digest(value.get("sha256"), path.name)
    if not safe_path(path).is_file() or path.stat().st_size != value["bytes"] or sha256(path) != value["sha256"]:
        raise ValueError("downloaded artifact bytes differ from signing evidence: " + path.name)


def validate_zip(archive, config, report, windows_hash):
    entries = zip_entries(archive.read_bytes())
    prefix = config["repository"] + ".4dbase/"
    anchor = prefix + config["repository"] + ".4DZ"
    if prefix not in entries or anchor not in entries or not entries[anchor][0]:
        raise ValueError("ZIP is missing its configured component anchor")
    outer = {}
    for name, value in entries.items():
        if not name.startswith(prefix):
            raise ValueError("ZIP has an unexpected component root")
        relative = name[len(prefix):]
        if not relative:
            continue
        if relative != config["repository"] + ".4DZ" and relative.split("/")[0] not in {
                "Resources", "Documentation", "Libraries"}:
            raise ValueError("unexpected outer component payload")
        outer[relative] = value
    inner = zip_entries(entries[anchor][0])
    required = ("Project/" + config["repository"] + ".4DProject",
                "Project/Sources/settings.4DSettings", "Project/Sources/catalog.4DCatalog",
                "Project/DerivedData/CompiledCode/classes.json", "Project/DerivedData/CompiledCode/map.json")
    if any(name not in inner or not inner[name][0] for name in required):
        raise ValueError("packed compiled project metadata is missing")
    for name in inner:
        path = PurePosixPath(name)
        if (path.parts[0] != "Project" or any(part.lower() in {"classes", "methods"} for part in path.parts)
                or not name.endswith("/") and not include_project(path)):
            raise ValueError("unexpected interpreted or uncompiled project payload")
    if not any(re.fullmatch(r"Project/DerivedData/CompiledCode/MX64 [0-9]+", name) and value[0]
               for name, value in inner.items()):
        raise ValueError("packed project has no x86_64 compiled code")
    for name in required[1:3]:
        if b"<!ENTITY" in inner[name][0].upper():
            raise ValueError("XML entities are prohibited in component metadata")
    settings = ET.fromstring(inner[required[1]][0])
    general = settings.find(".//general")
    if general is None or general.get("component_classStore_name") != config["namespace"]:
        raise ValueError("packed component namespace differs from configuration")
    if ET.fromstring(inner[required[2]][0]).get("name") != config["repository"]:
        raise ValueError("packed component catalog name differs from configuration")
    syntax_name = "Resources/en.lproj/syntaxEN.json"
    if syntax_name not in outer:
        raise ValueError("compiler-generated completion metadata is missing")
    syntax = json_bytes(outer[syntax_name][0])
    namespace = syntax.get("cs." + config["namespace"]) if isinstance(syntax, dict) else None
    feature = config.get("feature_class", "Greeting")
    if (not isinstance(namespace, dict) or not isinstance(namespace.get(feature), dict)
            or not namespace[feature] or not isinstance(syntax.get(feature), dict) or not syntax[feature]):
        raise ValueError("completion metadata does not export the configured feature class")
    helper = "Resources/bin/macOS/" + config["cli"]
    windows = "Resources/bin/Windows/" + config["cli"] + ".exe"
    if (helper not in outer or outer[helper][0][:4] not in {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}
            or not outer[helper][1] & 0o111 or windows not in outer or not outer[windows][1] & 0o111
            or hashlib.sha256(outer[windows][0]).hexdigest() != windows_hash
            or report.get("windows_sha256") != windows_hash):
        raise ValueError("packaged native helpers differ from accepted provenance")
    actual = {name: data for name, (data, _) in outer.items() if not name.endswith("/") and data[:4] in MACHO}
    inventory = report.get("signed_files")
    if not isinstance(inventory, list) or not inventory:
        raise ValueError("signed native file inventory is required")
    declared = set()
    for item in inventory:
        if not isinstance(item, dict):
            raise ValueError("invalid signed file entry")
        name = item.get("path")
        member_path(name)
        if (name in declared or name.endswith("/") or name != helper and not name.startswith("Libraries/")):
            raise ValueError("signed file paths must uniquely identify the helper and native Libraries")
        declared.add(name)
        digest(item.get("before_sha256"), "native file before signing")
        digest(item.get("after_sha256"), "native file after signing")
        if name not in actual or hashlib.sha256(actual[name]).hexdigest() != item["after_sha256"]:
            raise ValueError("signed native file inventory hash mismatch")
        signature(item.get("signature"), runtime=name == helper, architectures=True)
        if name == "Libraries/lib4d-arm64.dylib" and "arm64" not in item["signature"]["architectures"]:
            raise ValueError("required ARM64 library signature has no ARM64 architecture")
    if declared != set(actual) or not {helper, "Libraries/lib4d-arm64.dylib"} <= declared:
        raise ValueError("signed inventory does not match all packaged native Mach-O files")


def verify_assets(root, repository, tag, revision):
    root = safe_path(root)
    if not isinstance(repository, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_-]{0,79}", repository, re.ASCII):
        raise ValueError("invalid GitHub repository identity")
    validate_tag(tag)
    if github_repository(root) != repository:
        raise ValueError("release repository must match the checkout's exact GitHub origin")
    config = load_config(root, initialized=True, repository=repository, tag=tag)
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision) or commit(root) != revision:
        raise ValueError("publication checkout must match the full normalized accepted revision")
    name = config["repository"]
    paths = [root / "dist" / (name + suffix) for suffix in (".zip", ".dmg", ".signing.json")]
    for path in paths:
        if not safe_path(path).is_file():
            raise ValueError("all three regular downloaded release assets are required")
    if {path.name for path in paths[0].parent.iterdir()} != {path.name for path in paths}:
        raise ValueError("release asset directory contains unvalidated assets")
    report = _read_json(paths[2])
    if (not isinstance(report, dict) or type(report.get("schema")) is not int or report["schema"] != 1
            or report.get("commit") != revision or report.get("cli_source_sha256") != source_digest(root)
            or report.get("result") != "partial" or report.get("finished") is not True
            or report.get("release_ready") is not False):
        raise ValueError("signing evidence does not identify a finished partial acceptance of this revision")
    steps = report.get("steps")
    if not isinstance(steps, dict):
        raise ValueError("signing phase evidence is missing")
    for phase in REQUIRED_STEPS:
        if not isinstance(steps.get(phase), dict) or steps[phase].get("status") != "passed":
            raise ValueError("required signing/download phase did not pass: " + phase)
    windows = steps.get("windows_authenticode")
    if (not isinstance(windows, dict) or windows.get("status") != "not_run"
            or not isinstance(windows.get("reason"), str) or not windows["reason"].strip()):
        raise ValueError("Windows Authenticode must be explicitly disclosed as not_run")
    if any(not isinstance(value, dict) or value.get("status") not in {"passed", "not_run"}
           for value in steps.values()):
        raise ValueError("signing evidence contains a failed or invalid phase")
    contract = _read_json(safe_path(root / "tests/host/contract.json"))
    for field in ("source_host", "compiled_host", "dmg_host", "downloaded_package_host", "downloaded_dmg_host"):
        validate_status(report.get(field), contract, field != "source_host")
    compiled = report.get("compile")
    if (not isinstance(compiled, dict) or compiled.get("success") is not True
            or not isinstance(compiled.get("targets"), list) or len(compiled["targets"]) != 2
            or set(compiled["targets"]) != COMPILE_TARGETS
            or not isinstance(compiled.get("project"), str)
            or not compiled["project"].endswith("/component/" + name + "/Project/" + name + ".4DProject")):
        raise ValueError("compiler evidence must identify the configured project and both targets")
    for field in ("input_sha256", "output_sha256"):
        digest(compiled.get(field), "compiler " + field)
    tool = report.get("tool4d")
    if not isinstance(tool, dict) or not isinstance(tool.get("version"), str) or not tool["version"].strip():
        raise ValueError("actual tool4d identity is required")
    digest(tool.get("binary_sha256"), "tool4d")
    native = {}
    for target in TARGETS:
        executable = safe_path(root / "build" / target / (config["cli"] + (".exe" if target == "windows-x64" else "")))
        manifest_path = safe_path(executable.with_name("build-manifest.json"))
        if not executable.is_file():
            raise ValueError("same-run native executable is missing: " + target)
        recorded = _read_json(manifest_path)
        if not isinstance(recorded, dict) or type(recorded.get("schema")) is not int or recorded["schema"] != 1:
            raise ValueError("unsupported native CLI build manifest")
        manifest = verify_native(root, target, executable)
        if manifest != recorded or manifest.get("execution") != "passed":
            raise ValueError("native CLI execution must have passed: " + target)
        native[target] = manifest
    artifact(paths[0], report.get("artifact"))
    artifact(paths[1], report.get("dmg_artifact"))
    receipt(report.get("notarization"), report["artifact"]["sha256"])
    signing = report.get("dmg_signing")
    if not isinstance(signing, dict):
        raise ValueError("DMG signing provenance is required")
    digest(signing.get("before_sha256"), "DMG before signing")
    digest(signing.get("signed_sha256"), "DMG before stapling")
    signature(signing.get("signature"))
    receipt(report.get("dmg_notarization"), signing["signed_sha256"])
    signature(report.get("dmg_signature"))
    if any(report["dmg_signature"].get(field) is not True for field in ("stapled_ticket", "gatekeeper_assessment")):
        raise ValueError("DMG stapling and Gatekeeper verification are required")
    digest(report.get("dmg_tree_sha256"), "accepted equivalent DMG tree")
    validate_zip(paths[0], config, report, native["windows-x64"]["binary_sha256"])
    return paths


def publish(root, repository, tag, revision, *, create_tag=False):
    assets = verify_assets(root, repository, tag, revision)
    require_main()
    if tag.startswith("v"):
        raise ValueError("publication requires prefix-free SemVer; legacy tags are verification-only")
    if create_tag:
        create_release_tag(root, repository, tag, revision)
    else:
        verify_tag(root, tag, revision)
    subprocess.run(["gh", "release", "create", tag, *(str(path) for path in assets),
                    "--repo", repository, "--verify-tag", "--title", tag,
                    "--prerelease", "--latest=false", "--notes", NOTES],
                   cwd=root, check=True, capture_output=True, text=True, timeout=180)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--create-tag", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.create_tag and not args.publish:
            raise ValueError("--create-tag requires --publish")
        if args.publish:
            publish(args.root, args.repository, args.tag, args.sha, create_tag=args.create_tag)
        else:
            for path in verify_assets(args.root, args.repository, args.tag, args.sha):
                print(path)
    except (ValueError, OSError, TypeError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        parser.exit(2, "release asset verification failed: " + str(error) + "\n")
    except subprocess.SubprocessError:
        parser.exit(2, "release publication command failed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
