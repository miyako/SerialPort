#!/usr/bin/env python3
"""Validated configuration, native CI matrix, resource assembly and tool4D download."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from initialize import validate as validate_initialization


ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("macos-x64", "macos-arm64", "windows-x64")
SEMVER = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:0|[1-9]\d*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"


def load_config(root=ROOT, *, initialized=False, repository=None, tag=None):
    config = json.loads((Path(root) / "template.json").read_text(encoding="utf-8"))
    if (not isinstance(config, dict) or type(config.get("schema")) is not int
            or config.get("schema") != 1 or type(config.get("initialized")) is not bool):
        raise ValueError("unsupported template configuration")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", config.get("repository", "")):
        raise ValueError("invalid repository name")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", config.get("cli", "")):
        raise ValueError("invalid CLI name")
    if not re.fullmatch(SEMVER, config.get("version", ""), flags=re.ASCII):
        raise ValueError("version must be SemVer")
    manifest = json.loads((Path(root) / "package.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("name") != config["repository"]:
        raise ValueError("package.json name must match template.json repository")
    if manifest.get("version") != config["version"]:
        raise ValueError("package.json version must match template.json version")
    if config["initialized"]:
        validate_initialization(
            config["repository"], config.get("namespace", ""), config["cli"],
            config.get("language"), config.get("feature", ""))
        project = Path(root) / "component" / config["repository"] / "Project"
        if not (project / (config["repository"] + ".4DProject")).is_file():
            raise ValueError("configured component .4DProject name/path is missing")
        try:
            settings = ET.parse(project / "Sources" / "settings.4DSettings")
            catalog = ET.parse(project / "Sources" / "catalog.4DCatalog")
        except (ET.ParseError, OSError) as error:
            raise ValueError(f"invalid 4D project metadata: {error}") from error
        general = settings.find(".//general")
        if general is None or general.attrib.get("component_classStore_name") != config["namespace"]:
            raise ValueError("4D project namespace must match template.json")
        if catalog.getroot().attrib.get("name") != config["repository"]:
            raise ValueError("4D catalog name must match template.json repository")
        feature_class = config.get("feature_class", "Greeting")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,30}", feature_class):
            raise ValueError("feature_class must be a non-underscore 4D identifier")
        if not (project / "Sources" / "Classes" / (feature_class + ".4dm")).is_file():
            raise ValueError("configured feature class is missing")
    elif config.get("language") is not None:
        raise ValueError("uninitialized template must not select a language")
    if initialized and not config["initialized"]:
        raise ValueError("initialize the template before building or releasing")
    if repository is not None:
        if not re.fullmatch(r"[^/]+/[^/]+", repository):
            raise ValueError("repository identity must be owner/name")
        if repository.split("/")[1] != config["repository"]:
            raise ValueError("actual repository does not match template.json")
    if tag is not None and tag != "v" + config["version"]:
        raise ValueError("release tag must equal v<template.json version>")
    return config


def matrix(config):
    if not config["initialized"]:
        return {"include": []}
    runners = ("macos-15-intel", "macos-15", "windows-2022")
    return {"include": [
        {"target": target, "runner": runner, "language": config["language"]}
        for target, runner in zip(TARGETS, runners)
    ]}


def assemble(root=ROOT):
    from build_provenance import verify
    root = Path(root)
    config = load_config(root, initialized=True, repository=os.environ.get("GITHUB_REPOSITORY"))
    destination = root / "component" / config["repository"] / "Resources" / "bin"
    if any(path.is_symlink() for path in [destination, *destination.parents]):
        raise ValueError("resource assembly destination must not traverse symlinks")
    mac = destination / "macOS" / config["cli"]
    windows = destination / "Windows" / (config["cli"] + ".exe")
    inputs = [root / "build" / target / (config["cli"] + (".exe" if target == "windows-x64" else ""))
              for target in TARGETS]
    if not all(path.is_file() and not path.is_symlink() for path in inputs):
        raise ValueError("all three native build artifacts are required")
    for target, path in zip(TARGETS, inputs):
        verify(root, target, path)
    mac.parent.mkdir(parents=True, exist_ok=True)
    windows.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["lipo", "-create", str(inputs[0]), str(inputs[1]), "-output", str(mac)], check=True)
    subprocess.run(["lipo", str(mac), "-verify_arch", "x86_64", "arm64"], check=True)
    mac.chmod(0o755)
    shutil.copyfile(inputs[2], windows)
    windows.chmod(0o755)
    return destination


def extract_tar(archive, tools):
    """Extract regular files first, then validated app-framework symlinks."""
    base = tools.resolve()
    with tarfile.open(archive, "r:xz") as packed:
        members = packed.getmembers()
        links = {}
        destinations = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.name or path.is_absolute() or ".." in path.parts
                    or "\\" in member.name or ":" in member.name
                    or not (member.isdir() or member.isfile() or member.issym())):
                raise ValueError("unsafe path or non-regular entry in tool4D TAR.XZ")
            destination = tools.joinpath(*path.parts)
            if destination in destinations:
                raise ValueError("duplicate tool4D archive path")
            destinations.add(destination)
            if (not destination.resolve().is_relative_to(base) or destination.is_symlink()
                    or any(parent.is_symlink() for parent in destination.parents)):
                raise ValueError("unsafe tool4D extraction destination")
            if member.issym():
                target = PurePosixPath(member.linkname)
                if (target.is_absolute() or "\\" in member.linkname or ":" in member.linkname
                        or not (destination.parent / member.linkname).resolve().is_relative_to(base)):
                    raise ValueError("unsafe tool4D symlink target")
                links[destination] = member.linkname
        if any(parent in links for destination in destinations for parent in destination.parents):
            raise ValueError("archive entries must not traverse a symlink")
        for member in members:
            destination = tools.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with packed.extractfile(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
                destination.chmod(member.mode & 0o777 or 0o644)
        created = []
        try:
            for destination, target in links.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(target)
                created.append(destination)
            for destination in created:
                if not destination.resolve().is_relative_to(base):
                    raise ValueError("tool4D symlink chain escapes extraction directory")
        except (ValueError, OSError, RuntimeError):
            for destination in created:
                destination.unlink()
            raise


def require_tool4d_host():
    if platform.system() != "Darwin" or platform.machine().lower() not in {"arm64", "aarch64"}:
        raise ValueError("pinned tool4d is macOS ARM64 only; use a matching Apple Silicon host")


def tool_pin(root=ROOT):
    """Derive the official URL from versioned public metadata, not mutable latest."""
    manifest = json.loads((Path(root) / "package.json").read_text(encoding="utf-8"))
    tool = manifest.get("tool")
    if not isinstance(tool, dict):
        raise ValueError("package.json.tool must specify the approved compiler build and SHA256")
    if (tool.get("platform") != "macos" or tool.get("arch") != "arm"
            or not isinstance(tool.get("branch"), str) or not re.fullmatch(r"[0-9]+\.x", tool["branch"])
            or not isinstance(tool.get("version"), str) or not re.fullmatch(r"[0-9]+\.[0-9]+", tool["version"])
            or not isinstance(tool.get("build"), str) or not re.fullmatch(r"[1-9][0-9]*", tool["build"])
            or not isinstance(tool.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", tool["sha256"])):
        raise ValueError("package.json.tool requires fixed numeric build, macos/arm and a 64-hex SHA256")
    url = (f"https://resources-download.4d.com/release/{tool['branch']}/"
           f"{tool['version']}/{tool['build']}/mac/tool4d_arm64.tar.xz")
    return url, tool["sha256"].lower()


def resolve_tool_pin(root=ROOT, *, url=None, digest=None):
    if url is not None or digest is not None:
        if not url or not digest:
            raise ValueError("--url and --sha256 must be supplied together and nonempty")
        return checked_tool_pin(url, digest)
    env_url = os.environ.get("TOOL4D_URL", "")
    env_digest = os.environ.get("TOOL4D_SHA256", "")
    if env_url or env_digest:
        if not env_url or not env_digest:
            raise ValueError("TOOL4D_URL and TOOL4D_SHA256 overrides must be supplied together")
        return checked_tool_pin(env_url, env_digest)
    return checked_tool_pin(*tool_pin(root))


def checked_tool_pin(url, digest):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "resources-download.4d.com"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
        raise ValueError("official HTTPS tool4d URL and 64-hex SHA256 are required")
    if any(part.lower() == "latest" for part in
           PurePosixPath(urllib.parse.unquote(parsed.path)).parts):
        raise ValueError("tool4d URL must pin a concrete build, not latest")
    return url, digest.lower()


def download_tool4d(root=ROOT, *, url=None, digest=None):
    """Download a concrete, SHA-256-pinned HTTPS TAR.XZ or ZIP app archive."""
    root = Path(root).resolve()
    url, digest = resolve_tool_pin(root, url=url, digest=digest)
    tools = root / ".tools"
    if any(path.is_symlink() for path in [tools, *tools.parents]):
        raise ValueError(".tools must not be a symlink")
    tools.mkdir(exist_ok=True)
    archive = tools / "tool4d.download"
    if archive.is_symlink():
        raise ValueError("tool4D download destination must not be a symlink")
    sha = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
            redirected = urllib.parse.urlsplit(response.geturl())
            if redirected.scheme != "https" or redirected.hostname != "resources-download.4d.com":
                raise ValueError("tool4D download redirected outside official HTTPS origin")
            while chunk := response.read(1024 * 1024):
                sha.update(chunk)
                output.write(chunk)
    except (OSError, ValueError):
        archive.unlink(missing_ok=True)
        raise
    if sha.hexdigest().lower() != digest.lower():
        archive.unlink()
        raise ValueError("tool4D SHA-256 mismatch")
    try:
        with tempfile.TemporaryDirectory(prefix="tool4d-stage-", dir=tools) as temporary:
            stage = Path(temporary)
            if not zipfile.is_zipfile(archive):
                extract_tar(archive, stage)
            else:
                extract_zip(archive, stage)
            app = stage / "tool4d.app"
            executable = app / "Contents" / "MacOS" / "tool4d"
            if not executable.is_file() or executable.is_symlink():
                raise ValueError("archive must contain tool4d.app/Contents/MacOS/tool4d")
            executable.chmod(0o755)
            installed = tools / "tool4d.app"
            if installed.is_symlink():
                raise ValueError("installed tool4d.app must not be a symlink")
            if installed.exists():
                raise ValueError("tool4d.app already exists; inspect/remove only that app before reinstalling")
            app.rename(installed)
        (tools / "tool4d-install.json").write_text(json.dumps({
            "url": url, "sha256": sha.hexdigest(), "host": "macOS-arm64",
        }, indent=2) + "\n", encoding="utf-8")
    finally:
        archive.unlink(missing_ok=True)
    return installed / "Contents" / "MacOS" / "tool4d"


def extract_zip(archive, tools):
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        names = set()
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (not member.filename or "\\" in member.filename or path.is_absolute()
                    or ".." in path.parts or ":" in member.filename
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or path.as_posix() in names):
                raise ValueError("unsafe path or non-regular file in tool4D archive")
            names.add(path.as_posix())
            destination = tools.joinpath(*path.parts)
            if not destination.resolve().is_relative_to(tools.resolve()):
                raise ValueError("tool4D archive would escape .tools")
            if destination.is_symlink() or any(parent.is_symlink() for parent in destination.parents):
                raise ValueError("symlink in tool4D extraction destination")
        for member in members:
            path = tools.joinpath(*PurePosixPath(member.filename).parts)
            if member.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(member) as source, path.open("wb") as output:
                    shutil.copyfileobj(source, output)
                path.chmod((member.external_attr >> 16) & 0o777 or 0o644)
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("matrix", "validate", "assemble", "download-tool4d"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--tag", help="explicit normalized release tag (manual dispatch); defaults to GITHUB_REF_NAME")
    parser.add_argument("--url", help="optional official URL override, paired with --sha256")
    parser.add_argument("--sha256", help="optional approved hash override; otherwise env pair/package.json.tool")
    args = parser.parse_args()
    try:
        if args.command == "download-tool4d":
            require_tool4d_host()
            print(download_tool4d(args.root, url=args.url, digest=args.sha256))
            return
        if args.release and not os.environ.get("GITHUB_REPOSITORY"):
            raise ValueError("GITHUB_REPOSITORY is required for release validation")
        config = load_config(
            args.root, initialized=args.release or args.command == "assemble",
            repository=os.environ.get("GITHUB_REPOSITORY"),
            tag=(args.tag if args.tag is not None else os.environ.get("GITHUB_REF_NAME", "")) if args.release else None)
        if args.release and os.environ.get("FOURD_ENABLED") != "true":
            raise ValueError("releases require FOURD_ENABLED=true")
        if args.command == "matrix":
            value = json.dumps(matrix(config), separators=(",", ":"))
            print(value)
            if output := os.environ.get("GITHUB_OUTPUT"):
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"matrix={value}\n")
                    stream.write(f"initialized={str(config['initialized']).lower()}\n")
                    stream.write(f"language={config['language'] or 'none'}\n")
        elif args.command == "assemble":
            destination = assemble(args.root)
            print(destination)
            if output := os.environ.get("GITHUB_OUTPUT"):
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(f"resources={destination.resolve()}\n")
        else:
            print("configuration validated")
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, tarfile.TarError,
            zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        parser.exit(2, f"delivery configuration failed: {error}\n")


if __name__ == "__main__":
    main()
