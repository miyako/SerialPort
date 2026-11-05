#!/usr/bin/env python3
"""Run the real free tool4d compiler and require a successful result file."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import shutil
import hashlib


def input_digest(root):
    root = Path(root).resolve()
    config = json.loads((root / "template.json").read_text())
    component = root / "component" / config["repository"]
    files = [root / "template.json", root / "package.json", root / "LICENSE",
             component / "Project" / (config["repository"] + ".4DProject")]
    for folder in ("Project/Sources", "Documentation", "Resources"):
        directory = component / folder
        if directory.exists():
            files.extend(p for p in directory.rglob("*") if p.is_file()
                         and not (folder == "Resources" and p.relative_to(directory).as_posix() == "en.lproj/syntaxEN.json"))
    digest = hashlib.sha256()
    for path in sorted(files):
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError("component inputs must not be symlinks: " + str(path))
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def output_digest(component):
    component = Path(component)
    files = sorted((component / "Project/DerivedData").rglob("*"))
    files += sorted((component / "Libraries").rglob("*"))
    digest = hashlib.sha256()
    for path in files:
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError("compiled outputs must not traverse symlinks")
        if path.is_file():
            digest.update(path.relative_to(component).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def compile_component(root, tool4d, *, postprocess_outputs=None):
    root = Path(root).resolve()
    config = json.loads((root / "template.json").read_text())
    if not config["initialized"]:
        raise ValueError("initialize the destination repository before compiling")
    name = config["repository"]
    project = root / "component" / name / "Project" / (name + ".4DProject")
    output = root / "build" / "compile-status.json"
    if any(path.is_symlink() for path in [output, *output.parents]):
        raise ValueError("compiler status path must not traverse symlinks")
    output.parent.mkdir(exist_ok=True)
    output.unlink(missing_ok=True)
    request = output.parent / "compile-request.json"
    request.write_text(json.dumps({"project": str(project), "status": str(output)}) + "\n")
    derived = project.parent / "DerivedData"
    if any(path.is_symlink() for path in [derived, *derived.parents]):
        raise ValueError("compiler cleanup path must not traverse symlinks")
    if derived.exists():
        shutil.rmtree(derived)
    library = project.parent.parent / "Libraries" / "lib4d-arm64.dylib"
    if any(path.is_symlink() for path in [library, *library.parents]):
        raise ValueError("compiler library cleanup path must not traverse symlinks")
    library.unlink(missing_ok=True)
    original_digest = input_digest(root)
    subprocess.run([str(Path(tool4d).resolve()),
                    str(root / "tooling" / "compiler" / "Project" / "compiler.4DProject"),
                    "--skip-onstartup", "--startup-method=Compile",
                    "--user-param=" + str(request), "--dataless"],
                   check=True, timeout=180)
    if not output.is_file():
        raise ValueError("tool4d did not write compile-status.json; compilation is unverified")
    status = json.loads(output.read_text())
    if status.get("success") is not True:
        raise ValueError("4D compilation failed: " + json.dumps(status.get("errors", [])))
    if status.get("project") != project.as_posix() or set(status.get("targets", [])) != {"x86_64_generic", "arm64_macOS_lib"}:
        raise ValueError("compiler result does not identify the required project and targets")
    if input_digest(root) != original_digest:
        raise ValueError("component inputs changed during compilation; refusing stale output")
    if postprocess_outputs is not None:
        postprocess_outputs(project.parent.parent)
        if input_digest(root) != original_digest:
            raise ValueError("component inputs changed during output processing; refusing stale output")
    status["input_sha256"] = original_digest
    status["output_sha256"] = output_digest(project.parent.parent)
    output.write_text(json.dumps(status, indent=2) + "\n")
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tool4d", type=Path, required=True)
    args = parser.parse_args()
    try:
        compile_component(args.root, args.tool4d)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"4D compilation failed: {error}", file=sys.stderr)
        return 1
    print("4D compilation succeeded for macOS ARM64 and x86_64 generic targets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
