"""Content identity for same-source CLI builds, independent of checkout directory."""
import hashlib
import json
from pathlib import Path
import subprocess


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_digest(root):
    root = Path(root).resolve()
    files = [root / "template.json", root / "package.json"]
    files += sorted((path for path in (root / "cli").rglob("*")
                     if path.is_file() and path.relative_to(root / "cli").parts[0] not in {"target", "build"}),
                    key=lambda path: path.relative_to(root).as_posix())
    digest = hashlib.sha256()
    for path in files:
        if any(parent.is_symlink() for parent in [path, *path.parents]):
            raise ValueError("CLI source inputs must not traverse symlinks")
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def commit(root):
    result = subprocess.run(["git", "rev-parse", "--show-toplevel", "HEAD"], cwd=root, capture_output=True, text=True)
    if result.returncode:
        return None  # Disposable Use-this-template validation fixtures may have no .git.
    lines = result.stdout.splitlines()
    if len(lines) != 2 or Path(lines[0]).resolve() != Path(root).resolve():
        return None
    return lines[1]


def record(root, target, executable, tested, toolchain):
    status = {"schema": 1, "target": target, "source_sha256": source_digest(root),
              "commit": commit(root), "binary_sha256": sha256(executable),
              "execution": "passed" if tested else "not_run", "toolchain": toolchain}
    executable.with_name("build-manifest.json").write_text(json.dumps(status, indent=2) + "\n")
    return status


def verify(root, target, executable):
    manifest = executable.with_name("build-manifest.json")
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("missing build-manifest.json; rebuild all targets from the current source")
    status = json.loads(manifest.read_text())
    if (status.get("target") != target or status.get("source_sha256") != source_digest(root)
            or status.get("commit") != commit(root) or status.get("binary_sha256") != sha256(executable)):
        raise ValueError("stale or mixed-source CLI build manifest: " + target)
    return status
