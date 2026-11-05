#!/usr/bin/env python3
"""Explicit, fail-closed Developer ID build and downloaded-package verification.

Credentials are inherited, never serialized. This signs individual Mach-O files,
not the component folder or its data-only .4DZ. The ZIP has an online ticket;
a separately signed, notarized and stapled DMG seals the legacy component tree.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

from build_cli import smoke_test
from build_provenance import commit, sha256, source_digest
from ci_config import ROOT, assemble, load_config, require_tool4d_host
from compile_component import compile_component
from package_component import package, unpack_for_test, validate_resources
from test_4d import test_4d


SECRET_NAMES = (
    "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
    "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID",
    "NOTARYTOOL_PASSWORD",
)
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
STEPS = (
    "preflight", "tool4d", "assembly", "sign_cli", "signed_cli_smoke",
    "source_host", "compile", "sign_libraries", "package", "signatures",
    "notarization", "compiled_host", "final_integrity", "downloaded_package_signatures",
    "downloaded_package_host", "dmg_create", "dmg_sign", "dmg_notarization",
    "dmg_staple", "dmg_verify", "dmg_host", "dmg_integrity",
    "downloaded_dmg_verify", "downloaded_dmg_host", "windows_authenticode",
)


class SigningError(ValueError):
    """Only fixed, credential-free diagnostics may cross the command boundary."""

    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic


def codesign_failure(arguments, result):
    operation = next((name for flag, name in (
        ("--verify", "verify"), ("--display", "display"), ("-d", "display"),
        ("--sign", "sign"), ("-s", "sign")) if flag in arguments), "other")
    stderr = result.stderr if isinstance(result.stderr, str) else ""
    lowered = stderr.lower()
    categories = (
        ("certificate_chain_unavailable", ("unable to build chain", "unable to build certificate chain")),
        ("keychain_interaction_denied", ("user interaction is not allowed", "errsecinteractionnotallowed",
                                          "interaction with the security server is not allowed", "authorization denied")),
        ("keychain_internal_component", ("errsecinternalcomponent",)),
        ("requirement_failed", ("does not satisfy specified requirement", "failed to satisfy specified requirement",
                                "requirement failed", "code failed to satisfy specified code requirement")),
        ("timestamp_service_unavailable", ("timestamp service is not available", "timestamp server",
                                           "timestamp service unavailable", "a timestamp was expected but was not found")),
        ("signing_identity_unavailable", ("no identity found", "no signing identity", "identity not found",
                                          "the specified item could not be found in the keychain")),
        ("invalid_signature_or_format", ("invalid signature", "code or signature have been modified",
                                         "invalid or unsupported format", "bundle format unrecognized",
                                         "code object is not signed at all", "unsealed contents present")),
        ("usage_syntax", ("usage: codesign", "unrecognized option", "invalid option",
                          "option requires an argument", "ambiguous option")),
        ("forbidden_metadata", ("resource fork, finder information, or similar detritus not allowed",)),
        ("file_access_denied", ("permission denied", "operation not permitted")),
    )
    category = next((name for name, patterns in categories
                     if any(pattern in lowered for pattern in patterns)), "unknown")
    diagnostic = {"program": "codesign", "operation": operation, "category": category,
                  "exit_code": result.returncode}
    if category == "unknown":
        diagnostic["stderr_sha256"] = hashlib.sha256(stderr.encode("utf-8")).hexdigest()
    message = ("codesign exited with status " + str(result.returncode) +
               " (operation=" + operation + "; category=" + category)
    if "stderr_sha256" in diagnostic:
        message += "; stderr_sha256=" + diagnostic["stderr_sha256"]
    return SigningError(message + ")", diagnostic=diagnostic)


def safe_path(path):
    path = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise SigningError("path must not traverse symlinks")
    return path


def run(arguments, *, timeout=120):
    """Never expose argv, captured output or an original subprocess exception."""
    label = Path(arguments[0]).name
    if label not in {"security", "codesign", "xcrun", "lipo", "hdiutil", "spctl"}:
        label = "process"
    environment = {key: value for key, value in os.environ.items() if key not in SECRET_NAMES}
    try:
        result = subprocess.run([str(arg) for arg in arguments], capture_output=True,
                                text=True, timeout=timeout, env=environment, check=False)
    except Exception:
        raise SigningError(label + " execution failed") from None
    if result.returncode:
        if label == "codesign":
            raise codesign_failure(arguments, result) from None
        raise SigningError(label + " exited with status " + str(result.returncode)) from None
    return result


def preflight(environment=None, *, verify_only=False):
    environment = os.environ if environment is None else environment
    names = ("NOTARYTOOL_TEAM_ID",) if verify_only else SECRET_NAMES
    missing = [name for name in names if not environment.get(name, "").strip()]
    if missing:
        raise SigningError("missing secrets: " + ", ".join(missing))
    if not re.fullmatch(r"[A-Z0-9]{10}", environment["NOTARYTOOL_TEAM_ID"]):
        raise SigningError("invalid NOTARYTOOL_TEAM_ID")
    return {name: environment[name] for name in names}


def find_signing_identity(chain, team):
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise SigningError("invalid NOTARYTOOL_TEAM_ID")
    result = run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning", chain])
    matches = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(
            r'\s*\d+\)\s+([A-Fa-f0-9]{40})\s+"Developer ID Application: .+ \(([A-Z0-9]{10})\)"\s*',
            line)
        if match and match.group(2) == team:
            matches.append(match.group(1).upper())
    if len(matches) != 1:
        raise SigningError("isolated keychain must contain exactly one valid Developer ID Application identity for the configured team")
    return matches[0]


def cleanup_state(root):
    base = safe_path(Path(root) / "build/.serialport-signing-state")
    if base.exists() and (not base.is_dir() or stat.S_IMODE(base.stat().st_mode) != 0o700
                          or base.stat().st_uid != os.getuid()):
        raise SigningError("unsafe signing cleanup state directory")
    base.mkdir(parents=True, mode=0o700, exist_ok=True)
    return safe_path(base / "state.json")


def load_cleanup_state(root):
    state = cleanup_state(root)
    if not state.exists():
        return []
    if (not state.is_file() or stat.S_IMODE(state.stat().st_mode) != 0o600
            or state.stat().st_uid != os.getuid()):
        raise SigningError("unsafe signing cleanup state file")
    try:
        value = json.loads(state.read_text(encoding="utf-8"))
    except Exception:
        raise SigningError("invalid signing cleanup state") from None
    if (not isinstance(value, dict) or set(value) != {"schema", "entries"} or value["schema"] != 1
            or not isinstance(value["entries"], list)):
        raise SigningError("invalid signing cleanup state")
    return value["entries"]


def validate_owned(root, entry):
    if not isinstance(entry, dict) or set(entry) != {"kind", "directory", "token"}:
        raise SigningError("invalid signing cleanup entry")
    prefixes = {"keychain": ".signing-", "stage": ".dmg-stage-", "mount": ".dmg-mount-"}
    kind, text, token = entry["kind"], entry["directory"], entry["token"]
    if (not isinstance(kind, str) or kind not in prefixes or not isinstance(text, str) or not Path(text).is_absolute()
            or ".." in Path(text).parts or not isinstance(token, str)
            or not re.fullmatch(r"[a-f0-9]{32}", token)):
        raise SigningError("invalid signing cleanup entry")
    directory = safe_path(text)
    if (directory.parent != safe_path(Path(root) / "build")
            or not re.fullmatch(re.escape(prefixes[kind]) + r"[a-z0-9_]{8}", directory.name)):
        raise SigningError("cleanup path is not a job-owned signing directory")
    if directory.exists():
        marker = safe_path(directory / ".signing-owned")
        if (not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700
                or directory.stat().st_uid != os.getuid() or directory.is_mount()
                or not marker.is_file() or marker.read_text(encoding="utf-8") != token):
            raise SigningError("signing cleanup ownership marker mismatch")
        for path in directory.rglob("*"):
            safe_path(path)
            if not path.is_file() and not path.is_dir():
                raise SigningError("nonregular signing cleanup entry")
            if path.is_mount() and not (kind == "mount" and path == directory / "volume"):
                raise SigningError("unexpected mount in signing cleanup directory")
    return directory


def remove_owned(root, entry):
    directory = validate_owned(root, entry)
    if not directory.exists():
        return
    if entry["kind"] == "keychain":
        chain = directory / "signing.keychain-db"
        if chain.exists():
            run(["/usr/bin/security", "delete-keychain", chain])
    if entry["kind"] == "mount":
        mountpoint = directory / "volume"
        if mountpoint.is_mount():
            run(["/usr/bin/hdiutil", "detach", mountpoint], timeout=120)
        if mountpoint.exists():
            # Never recursively clean a live or nonempty mountpoint.
            mountpoint.rmdir()
    shutil.rmtree(directory)


def cleanup(root):
    root = safe_path(root)
    entries = load_cleanup_state(root)
    state = cleanup_state(root)
    pending = []
    for path in state.parent.iterdir():
        safe_path(path)
        if path == state:
            continue
        if (not re.fullmatch(r"\.signing-evidence-[a-z0-9_]{8}", path.name)
                or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600
                or path.stat().st_uid != os.getuid()):
            raise SigningError("unexpected file in private signing cleanup state")
        pending.append(path)
    directories = [validate_owned(root, entry) for entry in entries]
    if len(set(directories)) != len(directories):
        raise SigningError("duplicate signing cleanup entry")
    for entry in entries:
        remove_owned(root, entry)
    state.unlink(missing_ok=True)
    for path in pending:
        path.unlink()
    state.parent.rmdir()


@contextmanager
def owned_directory(root, kind, prefix):
    base = safe_path(Path(root) / "build")
    base.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    entry = {"kind": kind, "directory": str(directory), "token": uuid.uuid4().hex}
    marker = directory / ".signing-owned"
    with marker.open("x", encoding="utf-8") as stream:
        marker.chmod(0o600)
        stream.write(entry["token"])
    entries = load_cleanup_state(root)
    save_report(cleanup_state(root), {"schema": 1, "entries": [*entries, entry]})
    try:
        yield directory
    finally:
        remove_owned(root, entry)
        entries = [item for item in load_cleanup_state(root) if item != entry]
        save_report(cleanup_state(root), {"schema": 1, "entries": entries})


@contextmanager
def keychain(root, secrets):
    base = safe_path(Path(root) / "build")
    base.mkdir(parents=True, exist_ok=True)
    # Keep private scratch under the checkout; never use shared system temp paths.
    with owned_directory(root, "keychain", ".signing-") as directory:
        directory = safe_path(directory)
        directory.chmod(0o700)
        certificate = directory / "identity.p12"
        chain = directory / "signing.keychain-db"
        previous_search = shlex.split(run(["/usr/bin/security", "list-keychains", "-d", "user"]).stdout)
        previous_default = shlex.split(run(["/usr/bin/security", "default-keychain", "-d", "user"]).stdout)
        # Without a default, creation could install one that emergency cleanup
        # cannot safely restore without persisting unrelated user keychain state.
        if len(previous_default) != 1 or not Path(previous_default[0]).is_absolute():
            raise SigningError("an existing default keychain is required before isolated signing")
        attempted = False
        try:
            try:
                data = base64.b64decode("".join(secrets["APPLE_DEVELOPER_ID_CERTIFICATE"].split()),
                                        validate=True)
            except Exception:
                raise SigningError("invalid APPLE_DEVELOPER_ID_CERTIFICATE encoding") from None
            if not data:
                raise SigningError("empty signing certificate")
            with certificate.open("xb") as stream:
                certificate.chmod(0o600)
                stream.write(data)
            attempted = True
            run(["/usr/bin/security", "create-keychain", "-p", secrets["KEYCHAIN_PASSWORD"], chain])
            run(["/usr/bin/security", "set-keychain-settings", "-lut", "21600", chain])
            run(["/usr/bin/security", "unlock-keychain", "-p", secrets["KEYCHAIN_PASSWORD"], chain])
            # codesign's private-key lookup still needs the chain in the search list.
            run(["/usr/bin/security", "list-keychains", "-d", "user", "-s", chain, *previous_search])
            try:
                run(["/usr/bin/security", "import", certificate, "-k", chain,
                     "-P", secrets["APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD"],
                     "-T", "/usr/bin/codesign"])
            finally:
                certificate.unlink(missing_ok=True)
            run(["/usr/bin/security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
                 "-s", "-k", secrets["KEYCHAIN_PASSWORD"], chain])
            identity = find_signing_identity(chain, secrets["NOTARYTOOL_TEAM_ID"])
            profile = "serial-port-" + uuid.uuid4().hex
            run(["/usr/bin/xcrun", "notarytool", "store-credentials", profile,
                 "--keychain", chain, "--apple-id", secrets["NOTARYTOOL_APPLE_ID"],
                 "--team-id", secrets["NOTARYTOOL_TEAM_ID"],
                 "--password", secrets["NOTARYTOOL_PASSWORD"]])
            yield chain, profile, identity
        finally:
            certificate.unlink(missing_ok=True)
            if attempted:
                try:
                    run(["/usr/bin/security", "delete-keychain", chain])
                finally:
                    # Some security versions add a new keychain to the search list.
                    try:
                        current_search = shlex.split(run(
                            ["/usr/bin/security", "list-keychains", "-d", "user"]).stdout)
                        if current_search != previous_search:
                            run(["/usr/bin/security", "list-keychains", "-d", "user", "-s", *previous_search])
                    finally:
                        current_default = shlex.split(run(
                            ["/usr/bin/security", "default-keychain", "-d", "user"]).stdout)
                        if current_default != previous_default:
                            run(["/usr/bin/security", "default-keychain", "-d", "user", "-s", *previous_default])


def macho_files(component):
    component = safe_path(component)
    files = []
    for path in sorted(component.rglob("*")):
        safe_path(path)
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) in MACHO_MAGICS:
                    files.append(path)
        elif not path.is_dir():
            raise SigningError("nonregular component entry")
    return files


def expected_files(component, cli):
    files = macho_files(component)
    helper = component / "Resources/bin/macOS" / cli
    required = component / "Libraries/lib4d-arm64.dylib"
    if helper not in files or required not in files:
        raise SigningError("required helper or lib4d-arm64.dylib is missing or not Mach-O")
    if any(path != helper and "Libraries" != path.relative_to(component).parts[0] for path in files):
        raise SigningError("unexpected Mach-O outside helper and Libraries")
    return files


def verify_signature(path, team, *, runtime=False):
    safe_path(path)
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise SigningError("invalid NOTARYTOOL_TEAM_ID")
    requirement = ('=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists'
                   ' and certificate leaf[field.1.2.840.113635.100.6.1.13] exists'
                   ' and certificate leaf[subject.OU] = "' + team + '"')
    run(["/usr/bin/codesign", "--verify", "--strict", "--all-architectures",
         "-R", requirement, path])
    architectures = run(["/usr/bin/lipo", "-archs", path]).stdout.split()
    if (not architectures or len(architectures) != len(set(architectures))
            or set(architectures) - {"arm64", "x86_64"}
            or runtime and set(architectures) != {"arm64", "x86_64"}):
        raise SigningError("unexpected Mach-O architectures")
    for architecture in architectures:
        result = run(["/usr/bin/codesign", "--display", "--verbose=4", "--arch", architecture, path])
        verify_metadata(result.stderr, team, runtime=runtime)
    return {"developer_id": True, "team_matches": True, "secure_timestamp": True,
            "runtime": runtime, "architectures": architectures}


def verify_metadata(metadata, team, *, runtime=False):
    if (not re.search(r"^Authority=Developer ID Application: .+$", metadata, re.M)
            or re.findall(r"^TeamIdentifier=(.*)$", metadata, re.M) != [team]
            or "Signature=adhoc" in metadata
            or not re.search(r"^Timestamp=(?!none$|$).+$", metadata, re.M)):
        raise SigningError("Developer ID, team or secure timestamp verification failed")
    if runtime and not re.search(r"^CodeDirectory .*flags=0x[0-9a-fA-F]+\([^)]*\bruntime\b", metadata, re.M):
        raise SigningError("hardened runtime verification failed")


def sign_file(path, component, chain, identity, team, *, runtime=False):
    safe_path(path)
    before = sha256(path)
    arguments = ["/usr/bin/codesign", "--force", "--sign", identity,
                 "--keychain", chain, "--timestamp"]
    if runtime:
        arguments += ["--options", "runtime"]
    run([*arguments, path])
    verified = verify_signature(path, team, runtime=runtime)
    return {"path": path.relative_to(component).as_posix(), "before_sha256": before,
            "after_sha256": sha256(path), "signature": verified}


def notarization_receipt(value):
    if not isinstance(value, dict) or value.get("status") != "Accepted":
        raise SigningError("notarization must finish with status Accepted")
    identifier = value.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", identifier):
        raise SigningError("invalid notarization submission ID")
    return {"id": str(uuid.UUID(identifier)), "status": "Accepted"}


def notarize(archive, chain, profile):
    archive = safe_path(archive)
    submitted_hash = sha256(archive)
    result = run(["/usr/bin/xcrun", "notarytool", "submit", archive,
                  "--keychain", chain, "--keychain-profile", profile,
                  "--wait", "--timeout", "20m", "--output-format", "json"], timeout=1260)
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise SigningError("invalid notarization response") from None
    receipt = notarization_receipt(value)
    log = run(["/usr/bin/xcrun", "notarytool", "log", receipt["id"],
               "--keychain", chain, "--keychain-profile", profile], timeout=120)
    try:
        log_value = json.loads(log.stdout)
    except (ValueError, TypeError):
        raise SigningError("invalid notarization log") from None
    inspect_notary_log(log_value, receipt["id"], submitted_hash)
    if sha256(archive) != submitted_hash:
        raise SigningError("submitted archive changed during notarization")
    receipt.update(archive_sha256=submitted_hash, log_checked=True,
                   log_issue_count=len(log_value.get("issues") or []))
    return receipt


def inspect_notary_log(value, identifier, archive_hash):
    if (not isinstance(value, dict) or value.get("jobId") != identifier
            or value.get("status") != "Accepted" or type(value.get("statusCode")) is not int
            or value["statusCode"] != 0 or value.get("sha256") != archive_hash):
        raise SigningError("notarization log does not confirm the submitted archive")
    issues = value.get("issues")
    if issues is not None and (not isinstance(issues, list) or any(
            not isinstance(issue, dict) or issue.get("severity") not in {"warning", "info"} for issue in issues)):
        raise SigningError("notarization log contains errors or unrecognized issues")


def checked_receipt(value, archive_hash):
    notarization_receipt(value)
    if (value.get("log_checked") is not True or value.get("archive_sha256") != archive_hash
            or type(value.get("log_issue_count")) is not int or value["log_issue_count"] < 0):
        raise SigningError("notarization log inspection evidence is missing or inconsistent")


def tree_manifest(component):
    component = safe_path(component)
    if not component.is_dir():
        raise SigningError("component tree is missing")
    result = {}
    for path in [component, *sorted(component.rglob("*"))]:
        safe_path(path)
        name = path.relative_to(component).as_posix()
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_file():
            result[name] = {"mode": mode, "sha256": sha256(path), "bytes": path.stat().st_size}
        elif path.is_dir():
            result[name] = {"mode": mode, "directory": True}
        else:
            raise SigningError("nonregular component tree entry")
    return result


def equivalent_tree(component, expected):
    if tree_manifest(component) != expected:
        raise SigningError("DMG component bytes or modes differ from the final ZIP")
    return hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def create_dmg(root, component, name):
    root = safe_path(root)
    expected = tree_manifest(component)
    base = safe_path(root / "build")
    base.mkdir(parents=True, exist_ok=True)
    output = safe_path(root / "dist" / (name + ".dmg"))
    output.unlink(missing_ok=True)
    with owned_directory(root, "stage", ".dmg-stage-") as directory:
        staging = safe_path(directory / "payload")
        staging.mkdir()
        staged = staging / (name + ".4dbase")
        shutil.copytree(component, staged)
        equivalent_tree(staged, expected)
        run(["/usr/bin/hdiutil", "create", "-format", "UDZO", "-fs", "HFS+",
             "-volname", name, "-srcfolder", staging, output], timeout=300)
    safe_path(output)
    if not output.is_file() or output.stat().st_size <= 0:
        raise SigningError("hdiutil did not create a nonempty DMG")
    return output


def verify_dmg_signature(dmg, team):
    safe_path(dmg)
    if not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise SigningError("invalid NOTARYTOOL_TEAM_ID")
    requirement = ('=anchor apple generic and certificate 1[field.1.2.840.113635.100.6.2.6] exists'
                   ' and certificate leaf[field.1.2.840.113635.100.6.1.13] exists'
                   ' and certificate leaf[subject.OU] = "' + team + '"')
    run(["/usr/bin/codesign", "--verify", "--strict", "-R", requirement, dmg])
    metadata = run(["/usr/bin/codesign", "--display", "--verbose=4", dmg]).stderr
    verify_metadata(metadata, team)
    return {"developer_id": True, "team_matches": True, "secure_timestamp": True}


def sign_dmg(dmg, chain, identity, team):
    dmg = safe_path(dmg)
    before = sha256(dmg)
    run(["/usr/bin/codesign", "--force", "--sign", identity,
         "--keychain", chain, "--timestamp", dmg])
    signature = verify_dmg_signature(dmg, team)
    return {"before_sha256": before, "signed_sha256": sha256(dmg), "signature": signature}


def staple_dmg(dmg):
    dmg = safe_path(dmg)
    run(["/usr/bin/xcrun", "stapler", "staple", dmg], timeout=300)


def verify_dmg(dmg, team):
    signature = verify_dmg_signature(dmg, team)
    run(["/usr/bin/xcrun", "stapler", "validate", dmg], timeout=120)
    run(["/usr/sbin/spctl", "--assess", "--type", "open", "--context",
         "context:primary-signature", dmg], timeout=120)
    return {**signature, "stapled_ticket": True, "gatekeeper_assessment": True}


@contextmanager
def mounted_dmg(root, dmg, name):
    dmg = safe_path(dmg)
    base = safe_path(Path(root) / "build")
    base.mkdir(parents=True, exist_ok=True)
    with owned_directory(root, "mount", ".dmg-mount-") as directory:
        mountpoint = directory / "volume"
        mountpoint.mkdir(mode=0o700)
        try:
            run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-owners", "on",
                 "-mountpoint", mountpoint, dmg], timeout=120)
            if not mountpoint.is_mount():
                raise SigningError("hdiutil did not mount the DMG")
            if sorted(path.name for path in mountpoint.iterdir()) != [name + ".4dbase"]:
                raise SigningError("DMG must contain only the configured component")
            component = safe_path(mountpoint / (name + ".4dbase"))
            tree_manifest(component)
            yield component
        finally:
            run(["/usr/bin/hdiutil", "detach", mountpoint], timeout=120)
            mountpoint.rmdir()


def check_inventory(component, cli, inventory, team):
    files = expected_files(component, cli)
    actual = {path.relative_to(component).as_posix(): path for path in files}
    if (not isinstance(inventory, list) or not inventory
            or any(not isinstance(item, dict) for item in inventory)):
        raise SigningError("invalid signed file inventory")
    declared = [item.get("path") for item in inventory]
    if any(not isinstance(name, str) for name in declared) or len(set(declared)) != len(declared) or set(declared) != set(actual):
        raise SigningError("signed file inventory does not match package Mach-O files")
    for item in inventory:
        for field in ("before_sha256", "after_sha256"):
            if not isinstance(item.get(field), str) or not re.fullmatch(r"[a-f0-9]{64}", item[field]):
                raise SigningError("invalid signed file hash")
        path = actual[item["path"]]
        if sha256(path) != item["after_sha256"]:
            raise SigningError("signed file hash mismatch")
        verify_signature(path, team, runtime=item["path"] == "Resources/bin/macOS/" + cli)


def host_status(status, compiled):
    if (not isinstance(status, dict) or status.get("success") is not True
            or status.get("finished") is not True or status.get("compiled") is not compiled):
        raise SigningError("host did not confirm success, completion and required compiled mode")
    return status


def host_check(root, tool4d, component=None):
    return host_status(test_4d(root, tool4d, component), component is not None)


def tool_identity(tool4d):
    tool4d = safe_path(tool4d)
    result = run([tool4d, "--version"], timeout=30)
    if not result.stdout.strip():
        raise SigningError("tool4d version is missing")
    return {"version": result.stdout.strip(), "binary_sha256": sha256(tool4d)}


def new_report(root):
    revision = commit(root)
    if not revision:
        raise SigningError("a committed checkout is required")
    return {"schema": 1, "commit": revision, "cli_source_sha256": source_digest(root),
            "result": "partial", "finished": False, "release_ready": False,
            "phase": "build_setup",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "signed_files": [], "steps": {name: {"status": "not_run"} for name in STEPS}}


def save_report(output, report):
    outputs = output if isinstance(output, tuple) else (output,)
    content = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    for path in outputs:
        path = safe_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, pending = tempfile.mkstemp(prefix=".signing-evidence-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)
        finally:
            Path(pending).unlink(missing_ok=True)


def step(report, output, name, action):
    report["phase"] = name
    report["steps"][name] = {"status": "failed", "reason": "phase did not complete"}
    save_report(output, report)
    try:
        value = action()
    except SigningError as error:
        report["steps"][name]["reason"] = str(error)
        if error.diagnostic is not None:
            report["steps"][name]["failure"] = error.diagnostic
        save_report(output, report)
        raise
    report["steps"][name] = {"status": "passed"}
    save_report(output, report)
    return value


def unchanged_archive(archive, artifact):
    safe_path(archive)
    if (not archive.is_file() or type(artifact.get("bytes")) is not int or artifact["bytes"] <= 0
            or archive.stat().st_size != artifact["bytes"] or sha256(archive) != artifact.get("sha256")):
        raise SigningError("archive hash or byte count mismatch")


def finish(report, output):
    report.update(result="partial", finished=True, release_ready=False,
                  phase="finished", finished_at=datetime.now(timezone.utc).isoformat())
    report["steps"]["windows_authenticode"] = {
        "status": "not_run", "reason": "Windows Authenticode not implemented or provisioned"}
    save_report(output, report)
    return report


def build_signed(root, repository, tool4d):
    root = safe_path(root)
    config = load_config(root, initialized=True, repository=repository)
    output = (safe_path(root / "build/signing-status.json"),
              safe_path(root / "dist" / (config["repository"] + ".signing.json")))
    report = new_report(root)
    save_report(output, report)
    try:
        secrets = step(report, output, "preflight", preflight)
        require_tool4d_host()
        report["tool4d"] = step(report, output, "tool4d", lambda: tool_identity(tool4d))
        component = safe_path(root / "component" / config["repository"])
        step(report, output, "assembly", lambda: assemble(root))
        helper, windows = validate_resources(component, config["cli"])
        windows_hash = sha256(windows)
        report["phase"] = "keychain_setup"
        with keychain(root, secrets) as (chain, profile, identity):
            signed = step(report, output, "sign_cli",
                          lambda: sign_file(helper, component, chain, identity,
                                            secrets["NOTARYTOOL_TEAM_ID"], runtime=True))
            report["signed_files"].append(signed)
            step(report, output, "signed_cli_smoke",
                 lambda: smoke_test(helper, config["version"], root / "tests/cli-contract.json"))
            report["source_host"] = step(report, output, "source_host", lambda: host_check(root, tool4d))

            def sign_libraries(compiled):
                def action():
                    for path in expected_files(compiled, config["cli"]):
                        if path != helper:
                            report["signed_files"].append(sign_file(path, component, chain, identity,
                                                                   secrets["NOTARYTOOL_TEAM_ID"]))
                step(report, output, "sign_libraries", action)

            report["compile"] = step(report, output, "compile", lambda: compile_component(
                root, tool4d, postprocess_outputs=sign_libraries))
            archive = step(report, output, "package", lambda: package(
                root, repository=repository, tag="v" + config["version"]))
            report["artifact"] = {"path": archive.name, "sha256": sha256(archive),
                                  "bytes": archive.stat().st_size}
            unchanged_archive(archive, report["artifact"])
            # Inspect the exact packer's result, not only its source tree.
            unpacked = unpack_for_test(root, archive)
            step(report, output, "signatures", lambda: check_inventory(
                unpacked, config["cli"], report["signed_files"], secrets["NOTARYTOOL_TEAM_ID"]))
            report["notarization"] = step(report, output, "notarization",
                                         lambda: notarize(archive, chain, profile))
            report["phase"] = "post_notarization_integrity"
            checked_receipt(report["notarization"], report["artifact"]["sha256"])
            unchanged_archive(archive, report["artifact"])
            unpacked = unpack_for_test(root, archive)
            check_inventory(unpacked, config["cli"], report["signed_files"], secrets["NOTARYTOOL_TEAM_ID"])
            report["compiled_host"] = step(report, output, "compiled_host",
                                          lambda: host_check(root, tool4d, unpacked))
            def final_integrity():
                if sha256(windows) != windows_hash or sha256(
                        unpacked / "Resources/bin/Windows" / (config["cli"] + ".exe")) != windows_hash:
                    raise SigningError("Windows helper changed during macOS signing")
                report["windows_sha256"] = windows_hash
                unchanged_archive(archive, report["artifact"])
            step(report, output, "final_integrity", final_integrity)
            expected_tree = tree_manifest(unpacked)
            dmg = step(report, output, "dmg_create",
                       lambda: create_dmg(root, unpacked, config["repository"]))
            report["dmg_signing"] = step(report, output, "dmg_sign", lambda: sign_dmg(
                dmg, chain, identity, secrets["NOTARYTOOL_TEAM_ID"]))
            report["dmg_notarization"] = step(report, output, "dmg_notarization",
                                             lambda: notarize(dmg, chain, profile))
            report["phase"] = "dmg_pre_staple_integrity"
            checked_receipt(report["dmg_notarization"], report["dmg_signing"]["signed_sha256"])
            if sha256(dmg) != report["dmg_signing"]["signed_sha256"]:
                raise SigningError("signed DMG changed before stapling")
            step(report, output, "dmg_staple", lambda: staple_dmg(dmg))
            report["dmg_signature"] = step(report, output, "dmg_verify",
                                           lambda: verify_dmg(dmg, secrets["NOTARYTOOL_TEAM_ID"]))
            report["dmg_artifact"] = {"path": dmg.name, "sha256": sha256(dmg), "bytes": dmg.stat().st_size}

            def dmg_host():
                with mounted_dmg(root, dmg, config["repository"]) as mounted:
                    report["dmg_tree_sha256"] = equivalent_tree(mounted, expected_tree)
                    return host_check(root, tool4d, mounted)
            report["dmg_host"] = step(report, output, "dmg_host", dmg_host)
            def dmg_integrity():
                unchanged_archive(archive, report["artifact"])
                unchanged_archive(dmg, report["dmg_artifact"])
            step(report, output, "dmg_integrity", dmg_integrity)
            report["phase"] = "keychain_cleanup"
        return finish(report, output)
    except Exception:
        report.update(result="failed", finished=False)
        report["steps"].setdefault(report.get("phase", "setup"), {"status": "failed"})["status"] = "failed"
        save_report(output, report)
        raise SigningError("signed build failed; see signing evidence phase statuses") from None


def verify_download(root, archive, evidence, tool4d, dmg=None):
    root, archive, evidence = (safe_path(path) for path in (root, archive, evidence))
    config = load_config(root, initialized=True)
    try:
        report = json.loads(evidence.read_text(encoding="utf-8"))
    except Exception:
        raise SigningError("invalid signing evidence") from None
    # Do not rewrite untrusted evidence before validating the original build.
    if (not isinstance(report, dict) or report.get("schema") != 1
            or report.get("commit") != commit(root) or not report.get("commit")
            or report.get("cli_source_sha256") != source_digest(root)
            or report.get("result") != "partial" or report.get("finished") is not True
            or report.get("release_ready") is not False):
        raise SigningError("signing evidence does not match this checkout or a finished build")
    for name in ("assembly", "sign_cli", "signed_cli_smoke", "source_host", "compile",
                 "sign_libraries", "package", "signatures", "notarization", "compiled_host", "final_integrity",
                 "dmg_create", "dmg_sign", "dmg_notarization", "dmg_staple", "dmg_verify", "dmg_host", "dmg_integrity"):
        if report.get("steps", {}).get(name, {}).get("status") != "passed":
            raise SigningError("required signed build phase is not passed")
    if report.get("steps", {}).get("windows_authenticode", {}).get("status") != "not_run":
        raise SigningError("unexpected Windows signing claim")
    host_status(report.get("source_host"), False)
    host_status(report.get("compiled_host"), True)
    host_status(report.get("dmg_host"), True)
    artifact = report.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("path") != config["repository"] + ".zip" or archive.name != artifact["path"]:
        raise SigningError("unexpected signed archive identity")
    unchanged_archive(archive, artifact)
    checked_receipt(report.get("notarization"), artifact["sha256"])
    dmg = safe_path(dmg if dmg is not None else archive.with_suffix(".dmg"))
    dmg_artifact = report.get("dmg_artifact")
    if (not isinstance(dmg_artifact, dict) or dmg_artifact.get("path") != config["repository"] + ".dmg"
            or dmg.name != dmg_artifact["path"]):
        raise SigningError("unexpected signed DMG identity")
    unchanged_archive(dmg, dmg_artifact)
    signing = report.get("dmg_signing")
    if (not isinstance(signing, dict) or not isinstance(signing.get("signed_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", signing["signed_sha256"])):
        raise SigningError("DMG signing evidence is missing")
    checked_receipt(report.get("dmg_notarization"), signing["signed_sha256"])
    output = (safe_path(root / "build/signing-status.json"), evidence)
    report["finished"] = False
    try:
        report["phase"] = "download_preflight"
        secrets = preflight(verify_only=True)
        require_tool4d_host()
        current_tool = tool_identity(tool4d)
        if report.get("tool4d") != current_tool:
            raise SigningError("tool4d identity differs from signed build")
        unpacked = unpack_for_test(root, archive)
        step(report, output, "downloaded_package_signatures", lambda: check_inventory(
            unpacked, config["cli"], report.get("signed_files"), secrets["NOTARYTOOL_TEAM_ID"]))
        report["phase"] = "download_windows_integrity"
        windows = unpacked / "Resources/bin/Windows" / (config["cli"] + ".exe")
        if sha256(windows) != report.get("windows_sha256"):
            raise SigningError("Windows helper hash mismatch")
        report["downloaded_package_host"] = step(report, output, "downloaded_package_host",
                                                 lambda: host_check(root, tool4d, unpacked))
        step(report, output, "downloaded_dmg_verify",
             lambda: verify_dmg(dmg, secrets["NOTARYTOOL_TEAM_ID"]))
        expected_tree = tree_manifest(unpacked)
        def downloaded_dmg_host():
            with mounted_dmg(root, dmg, config["repository"]) as mounted:
                digest = equivalent_tree(mounted, expected_tree)
                if digest != report.get("dmg_tree_sha256"):
                    raise SigningError("downloaded DMG tree digest mismatch")
                return host_check(root, tool4d, mounted)
        report["downloaded_dmg_host"] = step(report, output, "downloaded_dmg_host", downloaded_dmg_host)
        report["phase"] = "download_archive_integrity"
        unchanged_archive(archive, artifact)
        unchanged_archive(dmg, dmg_artifact)
        return finish(report, output)
    except Exception:
        report.update(result="failed", finished=False)
        report["steps"].setdefault(report.get("phase", "setup"), {"status": "failed"})["status"] = "failed"
        save_report(output, report)
        raise SigningError("downloaded package verification failed; see signing evidence phase statuses") from None


def cli_diagnostic(command):
    return {"schema": 1, "command": command, "result": "partial", "finished": False,
            "release_ready": False, "phase": "preflight" if command == "preflight" else command + "_setup",
            "steps": {name: {"status": "not_run"} for name in STEPS}}


def record_cli_failure(output):
    if output is None:
        return
    try:
        safe_path(output)
        report = json.loads(output.read_text(encoding="utf-8"))
        if report.get("result") != "failed":
            report.update(result="failed", finished=False)
            phase = report.get("phase", "operation_setup")
            report["phase"] = phase
            report["steps"][phase] = {"status": "failed", "reason": "phase did not complete"}
            save_report(output, report)
    except Exception:
        # A non-writable or unsafe diagnostic destination must never turn failure into success.
        return


def cleanup_diagnostic(root, passed):
    output = safe_path(Path(root) / "build/signing-status.json")
    report = cli_diagnostic("cleanup")
    if output.is_file():
        try:
            previous = json.loads(output.read_text(encoding="utf-8"))
            if isinstance(previous, dict) and isinstance(previous.get("steps"), dict):
                report = previous
        except (ValueError, TypeError):
            pass
    report["steps"]["cleanup"] = {"status": "passed" if passed else "failed"}
    if not passed:
        report.update(result="failed", finished=False, release_ready=False, phase="cleanup")
    save_report(output, report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="validate inherited inputs; print missing names only")
    clean = commands.add_parser("cleanup", help="idempotently remove registered job-owned resources; no credentials")
    clean.add_argument("--root", type=Path, default=ROOT)
    build = commands.add_parser("build")
    verify = commands.add_parser("verify")
    for command in (build, verify):
        command.add_argument("--root", type=Path, default=ROOT)
        command.add_argument("--tool4d", type=Path, required=True)
    build.add_argument("--repository", required=True)
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--evidence", type=Path, required=True)
    verify.add_argument("--dmg", type=Path, help="downloaded DMG; defaults to archive's adjacent .dmg")
    args = parser.parse_args(argv)
    if args.command == "cleanup":
        try:
            cleanup(args.root)
            cleanup_diagnostic(args.root, True)
            print("job-owned signing cleanup completed")
            return 0
        except Exception:
            try:
                cleanup_diagnostic(args.root, False)
            except Exception:
                pass
            print("job-owned signing cleanup failed", file=sys.stderr)
            return 2
    diagnostic = None
    try:
        diagnostic = safe_path(getattr(args, "root", ROOT) / "build/signing-status.json")
        save_report(diagnostic, cli_diagnostic(args.command))
        if args.command == "preflight":
            preflight()
            report = cli_diagnostic(args.command)
            report["phase"] = "preflight"
            report["steps"]["preflight"] = {"status": "passed"}
            save_report(diagnostic, report)
            print("signing preflight passed")
        elif args.command == "build":
            build_signed(args.root, args.repository, args.tool4d)
            print("signed ZIP and stapled DMG verified; release_ready=false; see dist signing evidence")
        else:
            verify_download(args.root, args.archive, args.evidence, args.tool4d, args.dmg)
            print("downloaded ZIP and stapled DMG verified; release_ready=false")
    except SigningError as error:
        record_cli_failure(diagnostic)
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        record_cli_failure(diagnostic)
        print("signing operation failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
