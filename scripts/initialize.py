#!/usr/bin/env python3
"""Configure a template checkout once, without invoking any toolchain."""
import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
import os
import subprocess
import urllib.parse


def github_repository(root):
    """Return only owner/name, never log credential-bearing remote URLs."""
    root = Path(root).resolve()
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                         capture_output=True, text=True, check=False)
    if top.returncode or Path(top.stdout.strip()).resolve() != root:
        return None
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=root,
                            capture_output=True, text=True, check=False)
    if remote.returncode:
        return None
    url = remote.stdout.strip()
    if url.startswith("git@github.com:"):
        path = url.removeprefix("git@github.com:")
    else:
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git")
    return path if re.fullmatch(r"[^/\s]+/[^/\s]+", path) else None


def validate(repository, namespace, cli, language, feature):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", repository):
        raise ValueError("repository must be 1-80 ASCII letters, digits, underscores or hyphens, starting with a letter or digit")
    if repository.lower() == "4d-component-template":
        raise ValueError("choose the destination repository name, not 4d-component-template")
    reserved_files = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                      *(f"lpt{i}" for i in range(1, 10))}
    if repository.lower() in reserved_files:
        raise ValueError("repository is a reserved Windows device name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,30}", namespace):
        raise ValueError("namespace must be a 1-31 character 4D identifier starting with a letter")
    if namespace.lower() in {"cs", "ds", "ob", "4d", "templatecli"}:
        raise ValueError("namespace is reserved or still the template namespace")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", cli):
        raise ValueError("CLI name must be 1-63 lowercase ASCII letters, digits or hyphens, starting with a letter")
    if cli in reserved_files:
        raise ValueError("CLI name is a reserved Windows device name")
    if language not in {"go", "rust", "cpp"}:
        raise ValueError("language must be go, rust or cpp")
    if not feature.strip() or len(feature) > 200 or any(ord(c) < 32 for c in feature):
        raise ValueError("feature must be 1-200 characters, without control characters")


def initialize(root, repository, namespace, cli, language, feature):
    root = Path(root).resolve()
    validate(repository, namespace, cli, language, feature)
    config_path = root / "template.json"
    old = json.loads(config_path.read_text(encoding="utf-8"))
    desired = dict(old, initialized=True, repository=repository, namespace=namespace,
                   cli=cli, language=language, feature=feature)
    if old["initialized"]:
        if old == desired:
            return False
        raise ValueError("already initialized; reconfiguration is not supported (start from a fresh template)")
    source = root / "component" / old["repository"]
    target = root / "component" / repository
    if not source.is_dir() or target.exists():
        raise ValueError("missing template component or destination already exists")
    if (root / "cli").exists():
        raise ValueError("cli/ already exists; refusing to overwrite")
    starter = root / "cli-starters" / language
    if not starter.is_dir():
        raise ValueError("selected CLI starter is missing")
    if any(path.is_symlink() for directory in (source, starter) for path in [directory, *directory.rglob("*")]):
        raise ValueError("template component/starter must not contain symlinks")
    if any(parent.is_symlink() for directory in (source, starter) for parent in directory.parents):
        raise ValueError("initialization source directories must not traverse symlinks")
    for relative in ("template.json", "package.json", "README.md",
                     "tests/host/Project/Sources/Methods/Smoke.4dm",
                     "tests/host/Project/Sources/dependencies.json", "tests/host/environment4d.json"):
        path = root / relative
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError("initialization paths must not traverse symlinks: " + relative)
    replacements = {
        old["repository"]: repository,
        old["namespace"]: namespace,
        old["cli"]: cli,
    }
    pattern = re.compile("|".join(re.escape(value) for value in replacements))
    # Stage and validate every rewritten file before moving anything into place.
    with tempfile.TemporaryDirectory(prefix=".initialize-", dir=root) as temporary:
        stage = Path(temporary)
        staged_component = stage / repository
        shutil.copytree(source, staged_component)
        staged_cli = stage / "cli"
        shutil.copytree(starter, staged_cli)
        notices = staged_cli / "licenses"
        if notices.is_dir():
            shutil.copytree(notices, staged_component / "Resources/licenses", dirs_exist_ok=True)
        for directory in (staged_component, staged_cli):
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix not in {".png", ".jpg", ".ico"}:
                    text = path.read_text(encoding="utf-8")
                    text = pattern.sub(lambda match: replacements[match.group()], text)
                    path.write_text(text, encoding="utf-8")
        project = staged_component / "Project" / (old["repository"] + ".4DProject")
        if not project.is_file():
            raise ValueError("template .4DProject is missing")
        project.rename(project.with_name(repository + ".4DProject"))
        json.loads((staged_component / "Project" / (repository + ".4DProject")).read_text())
        ET.parse(staged_component / "Project" / "Sources" / "settings.4DSettings")
        catalog = staged_component / "Project" / "Sources" / "catalog.4DCatalog"
        text = catalog.read_text()
        text = re.sub(r'uuid="[A-F0-9]+"', 'uuid="' + uuid.uuid5(uuid.NAMESPACE_URL, repository + "/" + namespace).hex.upper() + '"', text)
        catalog.write_text(text, encoding="utf-8")
        ET.parse(catalog)
        package = json.loads((root / "package.json").read_text())
        package.update(name=repository, description=feature)
        rewritten = {}
        for relative in ("README.md", "tests/host/Project/Sources/Methods/Smoke.4dm",
                         "tests/host/Project/Sources/dependencies.json", "tests/host/environment4d.json"):
            path = root / relative
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                if relative == "README.md":
                    chunks = re.split(r"(https://github\.com/[^\s)>\]]+)", text)
                    rewritten[path] = "".join(chunk if index % 2 else
                        pattern.sub(lambda match: replacements[match.group()], chunk)
                        for index, chunk in enumerate(chunks))
                else:
                    rewritten[path] = pattern.sub(lambda match: replacements[match.group()], text)
        staged_component.rename(target)
        staged_cli.rename(root / "cli")
        for path, text in rewritten.items():
            path.write_text(text, encoding="utf-8")
        (root / "package.json").write_text(json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        shutil.rmtree(source)
        shutil.rmtree(root / "cli-starters")
        config_path.write_text(json.dumps(desired, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="exact destination repository name (not owner/name)")
    parser.add_argument("--namespace", required=True, help="unique exported 4D namespace")
    parser.add_argument("--cli", required=True)
    parser.add_argument("--language", choices=["go", "rust", "cpp"], required=True)
    parser.add_argument("--feature", required=True, help="requested feature; the agent replaces the example implementation")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        actual = os.environ.get("GITHUB_REPOSITORY")
        if not actual:
            actual = github_repository(args.root)
        if actual and actual.rsplit("/", 1)[-1] != args.repository:
            raise ValueError("--repository must equal the actual GITHUB_REPOSITORY basename")
        changed = initialize(args.root, args.repository, args.namespace, args.cli, args.language, args.feature)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, f"initialization failed: {error}\n")
    print("initialized" if changed else "already configured (unchanged)")


if __name__ == "__main__":
    main()
