#!/usr/bin/env python3
"""Package a compiled component as <repository>.4dbase/<repository>.4DZ plus resources.

This is an unsigned dependency-manager archive, not a notarized application.
Project metadata and compiled code are retained, but interpreted methods/classes
are excluded. Both ZIP levels are deterministic and require verified compilation.
"""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import zipfile

from ci_config import ROOT, load_config
from compile_component import input_digest, output_digest


COMPILE_TARGETS = {"x86_64_generic", "arm64_macOS_lib"}
RUNTIME_NOTICES = {"go": "Go-LICENSE.txt", "rust": "Rust-LICENSE-MIT.txt"}
EXCLUDED = {"data", "logs", ".git", ".ds_store", "__macosx"}


def write_zip(stream, entries):
    directories = {parent.as_posix() + "/" for name, _, _ in entries
                   for parent in PurePosixPath(name).parents if parent.as_posix() != "."}
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content, mode in sorted([*((name, b"", 0o755) for name in directories), *entries]):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            directory = name.endswith("/")
            info.external_attr = ((stat.S_IFDIR if directory else stat.S_IFREG) | mode) << 16
            info.compress_type = zipfile.ZIP_STORED if directory else zipfile.ZIP_DEFLATED
            archive.writestr(info, content, compresslevel=9)


def validate_resources(component, cli):
    mac = component / "Resources" / "bin" / "macOS" / cli
    windows = component / "Resources" / "bin" / "Windows" / (cli + ".exe")
    for path in (mac, windows):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing regular native resource: {path}")
    data = mac.read_bytes()
    if len(data) < 8 or data[:4] not in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):
        raise ValueError("macOS resource must be a universal Mach-O")
    wide = data[:4] == b"\xca\xfe\xba\xbf"
    count = struct.unpack_from(">I", data, 4)[0]
    entry_size = 32 if wide else 20
    if count != 2 or len(data) < 8 + count * entry_size:
        raise ValueError("universal resource must contain exactly Intel and ARM slices")
    architectures = set()
    ranges = []
    for index in range(count):
        position = 8 + index * entry_size
        cpu = struct.unpack_from(">I", data, position)[0]
        offset, size = struct.unpack_from(">QQ" if wide else ">II", data, position + 8)
        if (offset < 8 + count * entry_size or size < 32 or offset + size > len(data)
                or data[offset:offset + 4] != b"\xcf\xfa\xed\xfe"
                or struct.unpack_from("<I", data, offset + 4)[0] != cpu):
            raise ValueError("invalid Mach-O slice header or bounds")
        if any(offset < end and start < offset + size for start, end in ranges):
            raise ValueError("overlapping Mach-O slices")
        ranges.append((offset, offset + size))
        architectures.add(cpu)
    if architectures != {0x01000007, 0x0100000C}:
        raise ValueError("macOS resource must contain x86_64 and arm64")
    data = windows.read_bytes()
    if len(data) < 64 or data[:2] != b"MZ":
        raise ValueError("Windows resource must be a PE executable")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if (offset < 64 or len(data) < offset + 26 or data[offset:offset + 4] != b"PE\0\0"
            or struct.unpack_from("<H", data, offset + 4)[0] != 0x8664
            or struct.unpack_from("<H", data, offset + 24)[0] != 0x20B):
        raise ValueError("Windows resource must have an x64 PE32+ header")
    return mac, windows


def validate_compile(root, component, repository):
    marker = root / "build" / "compile-status.json"
    if not marker.is_file() or marker.is_symlink():
        raise ValueError("successful compile-status.json is required; source-only releases are prohibited")
    status = json.loads(marker.read_text(encoding="utf-8"))
    project = component / "Project" / (repository + ".4DProject")
    if (status.get("success") is not True or not isinstance(status.get("targets"), list)
            or set(status["targets"]) != COMPILE_TARGETS):
        raise ValueError("compile marker must confirm success for both required targets")
    marked_project = status.get("project")
    if not isinstance(marked_project, str):
        raise ValueError("compile marker must identify the project path")
    candidate = Path(marked_project)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.resolve() != project.resolve():
        raise ValueError("compile marker project does not match configured project")
    if status.get("input_sha256") != input_digest(root):
        raise ValueError("compile marker input_sha256 does not match current component inputs; recompile")
    if not project.is_file():
        raise ValueError("configured .4DProject is missing")
    compiled = project.parent / "DerivedData" / "CompiledCode"
    required = [
        component / "Libraries" / "lib4d-arm64.dylib",
        compiled / "classes.json", compiled / "map.json",
    ]
    for path in required:
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            raise ValueError(f"required compiled artifact is missing or empty: {path.relative_to(component)}")
    objects = [path for path in compiled.iterdir()
               if re.fullmatch(r"MX64 [0-9]+", path.name) and path.is_file()]
    if not objects or any(path.is_symlink() or path.stat().st_size == 0 for path in objects):
        raise ValueError("CompiledCode must contain nonempty regular MX64 numeric-id files")
    if status.get("output_sha256") != output_digest(component):
        raise ValueError("compile marker output_sha256 does not match compiled artifacts; recompile")


def include_project(relative):
    parts = relative.parts
    if len(parts) < 2 or parts[0] != "Project" or relative.suffix.lower() == ".4dm":
        return False
    if len(parts) == 2:
        return relative.suffix == ".4DProject"
    if parts[1] == "Sources":
        return not any(part.lower() in {"classes", "methods"} for part in parts[2:])
    if parts[1] == "DerivedData":
        return (parts[2] == "CompiledCode"
                or len(parts) == 3 and parts[2] in {
                    "methodAttributes.json", "formAttributes.json", "webservices.json"})
    return False


def package(root=ROOT, *, repository=None, tag=None, language=None):
    root = Path(root).resolve()
    repository = repository if repository is not None else os.environ.get("GITHUB_REPOSITORY")
    tag = tag if tag is not None else os.environ.get("GITHUB_REF_NAME")
    if not repository or not tag:
        raise ValueError("actual owner/repository and release tag are required")
    config = load_config(root, initialized=True, repository=repository, tag=tag)
    if language is not None and language != config["language"]:
        raise ValueError("requested language does not match template.json")
    component = root / "component" / config["repository"]
    if component.is_symlink() or component.parent.is_symlink():
        raise ValueError("component directories must not be symlinks")
    validate_resources(component, config["cli"])
    if config["language"] in RUNTIME_NOTICES:
        notice = component / "Resources/licenses" / RUNTIME_NOTICES[config["language"]]
        if not notice.is_file() or notice.is_symlink() or not notice.read_bytes().strip():
            raise ValueError("selected runtime redistribution notice is missing: " + str(notice))
    validate_compile(root, component, config["repository"])
    license_path = root / "LICENSE"
    if not license_path.is_file() or license_path.is_symlink():
        raise ValueError("root LICENSE is required")
    files = []
    for path in sorted(component.rglob("*")):
        relative = path.relative_to(component)
        if any(part.lower() in {".tools", "tool4d.app"} for part in relative.parts):
            raise ValueError("vendor tooling/local installation must not be included in component packages")
        if any(part.lower() in EXCLUDED for part in relative.parts):
            continue
        if ("deriveddata" in (part.lower() for part in relative.parts)
                and relative.parts[:2] != ("Project", "DerivedData")):
            continue
        if path.is_symlink():
            raise ValueError(f"symlinks are prohibited in component packages: {relative}")
        if any("\\" in part or ":" in part for part in relative.parts):
            raise ValueError("unsafe archive path")
        if path.is_file():
            if include_project(relative) or relative.parts[0] in {"Resources", "Documentation", "Libraries"}:
                files.append((relative.as_posix(), path))
        elif not path.is_dir():
            raise ValueError(f"non-regular component entry: {relative}")
    files = [(name, path) for name, path in files if name != "Resources/licenses/LICENSE"]
    files.append(("Resources/licenses/LICENSE", license_path))
    # Keep the Project/ anchor inside the standard-ZIP .4DZ, as emitted by 4D.
    project_entries = [
        (name, path.read_bytes(), 0o644)
        for name, path in files if name.startswith("Project/")
    ]
    packed = io.BytesIO()
    write_zip(packed, project_entries)
    prefix = config["repository"] + ".4dbase/"
    destination = root / "dist" / (config["repository"] + ".zip")
    if any(path.is_symlink() for path in [destination, *destination.parents]):
        raise ValueError("package destination must not traverse symlinks")
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries = [(prefix + config["repository"] + ".4DZ", packed.getvalue(), 0o644)]
    for name, path in files:
        if name.startswith("Project/"):
            continue
        executable = name in (f"Resources/bin/macOS/{config['cli']}",
                              f"Resources/bin/Windows/{config['cli']}.exe")
        library = name.startswith("Libraries/") and path.stat().st_mode & 0o111
        entries.append((prefix + name, path.read_bytes(), 0o755 if executable or library else 0o644))
    write_zip(destination, entries)
    return destination


def unpack_for_test(root, archive):
    """Restore only this generated .4dbase for the real compiled-host test."""
    root = Path(root).resolve()
    config = load_config(root, initialized=True)
    base = root / "build" / "release-test"
    destination = base / (config["repository"] + ".4dbase")
    if any(path.is_symlink() for path in (destination, base, base.parent)):
        raise ValueError("release-test destination must not contain symlinks")
    prefix = destination.name
    with zipfile.ZipFile(archive) as packed:
        members = packed.infolist()
        names = set()
        for entry in members:
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (not path.parts or path.parts[0] != prefix or path.is_absolute()
                    or ".." in path.parts or "\\" in entry.filename or ":" in entry.filename
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or entry.filename in names):
                raise ValueError("unsafe compiled component archive")
            names.add(entry.filename)
        if prefix + "/" + config["repository"] + ".4DZ" not in names:
            raise ValueError("compiled component archive is missing its .4DZ")
        if destination.exists():
            shutil.rmtree(destination)
        for entry in members:
            path = base.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with packed.open(entry) as source, path.open("wb") as output:
                    shutil.copyfileobj(source, output)
                path.chmod((entry.external_attr >> 16) & 0o777 or 0o644)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repository", help="actual owner/name; defaults to GITHUB_REPOSITORY")
    parser.add_argument("--tag", help="release v<SemVer>; defaults to GITHUB_REF_NAME")
    parser.add_argument("--language", choices=("go", "rust", "cpp"))
    parser.add_argument("--unpack-test", action="store_true",
                        help="also unpack the generated archive under build/release-test")
    args = parser.parse_args()
    try:
        archive = package(args.root, repository=args.repository, tag=args.tag, language=args.language)
        print(unpack_for_test(args.root, archive) if args.unpack_test else archive)
    except (ValueError, OSError, TypeError, KeyError, struct.error) as error:
        parser.exit(2, f"component packaging failed: {error}\n")


if __name__ == "__main__":
    main()
