#!/usr/bin/env python3
"""Rename the configured example class identity, NOT its feature implementation."""
import argparse
import json
from pathlib import Path
import re

from ci_config import ROOT, load_config


def replace_class_tokens(text, pattern, name):
    """Rewrite cs references only outside 4D Text literals and // comments."""
    chunks = re.split(r'("(?:\\.|[^"\\])*"|//[^\n]*)', text)
    return "".join(chunk if index % 2 else pattern.sub(lambda match: match[1] + name, chunk)
                   for index, chunk in enumerate(chunks))


def rename_feature(root, name):
    root = Path(root).resolve()
    config = load_config(root, initialized=True)
    if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,30}", name)
            or name.lower() in {"cs", "ds", "ob", "4d", "greeting", "con", "prn", "aux", "nul",
                               *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}):
        raise ValueError("choose a new non-reserved 1-31 character public 4D class identifier")
    old = config.get("feature_class", "Greeting")
    if name == old:
        return False
    component = root / "component" / config["repository"]
    classes = component / "Project/Sources/Classes"
    docs = component / "Documentation/Classes"
    if any(path.stem.lower() == name.lower() for path in classes.iterdir()):
        raise ValueError("class identity collides with an existing class")
    paths = [classes / (old + ".4dm"), docs / (old + ".md"), root / "README.md",
             root / "tests/host/Project/Sources/Methods/Smoke.4dm", root / "template.json"]
    if (docs / (name + ".md")).exists():
        raise ValueError("class documentation destination exists")
    updates = {}
    token = re.compile(r"(\bcs(?::C1710)?\.(?:" + re.escape(config["namespace"]) +
                       r"\.)?)" + re.escape(old) + r"\b")
    for path in paths:
        if not path.is_file() or any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError("rename input must be a regular path without symlinks: " + str(path))
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".4dm":
            text = replace_class_tokens(text, token, name)
        elif path.name != "template.json":
            text = re.sub(r"\b" + re.escape(old) + r"\b", lambda _: name, text)
        updates[path] = text
    config["feature_class"] = name
    updates[root / "template.json"] = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    for path, text in updates.items():
        path.write_text(text, encoding="utf-8")
    (classes / (old + ".4dm")).rename(classes / (name + ".4dm"))
    (docs / (old + ".md")).rename(docs / (name + ".md"))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--class", dest="name", required=True)
    args = parser.parse_args()
    try:
        changed = rename_feature(args.root, args.name)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"class rename failed: {error}\n")
    print("renamed; implement feature and replace CLI/host contracts next" if changed else "unchanged")


if __name__ == "__main__":
    main()
