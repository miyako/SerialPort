#!/usr/bin/env python3
"""Resolve only existing version tags, then detect movement before publication."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from ci_config import ROOT, SEMVER


def validate_tag(tag):
    if not isinstance(tag, str) or not re.fullmatch("v" + SEMVER, tag, flags=re.ASCII):
        raise ValueError("release tag must be v<SemVer>, not a branch or arbitrary ref")
    return tag


def tag_commit(root, tag):
    tag = validate_tag(tag)
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/tags/" + tag + "^{commit}"],
        cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode:
        raise ValueError("release tag must already exist and point to a commit")
    sha = result.stdout.strip()
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
        raise ValueError("git returned an invalid commit SHA")
    return sha


def verify(root, tag, expected_sha):
    if not isinstance(expected_sha, str) or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected_sha):
        raise ValueError("expected SHA must be a full normalized commit SHA")
    sha = tag_commit(root, tag)
    if sha != expected_sha:
        raise ValueError("release tag moved: it no longer points to the expected commit")
    return sha


def resolve(root, *, event, ref_type="", ref_name="", version_tag="", event_sha=""):
    if event == "push":
        if ref_type != "tag":
            raise ValueError("push release events must refer to a tag")
        tag = validate_tag(ref_name)
        sha = verify(root, tag, event_sha)
    elif event == "workflow_dispatch":
        tag = validate_tag(version_tag)
        sha = tag_commit(root, tag)
    else:
        raise ValueError("release supports only push tag and workflow_dispatch events")
    return {"tag": tag, "sha": sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    resolver = commands.add_parser("resolve")
    resolver.add_argument("--event", required=True)
    resolver.add_argument("--ref-type", default="")
    resolver.add_argument("--ref-name", default="")
    resolver.add_argument("--version-tag", default="")
    resolver.add_argument("--event-sha", default="")
    verifier = commands.add_parser("verify")
    verifier.add_argument("--tag", required=True)
    verifier.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify(args.root, args.tag, args.expected_sha)
            print("Release tag still points to the expected commit.")
        else:
            metadata = resolve(args.root, event=args.event, ref_type=args.ref_type,
                               ref_name=args.ref_name, version_tag=args.version_tag,
                               event_sha=args.event_sha)
            output = "".join(f"{key}={value}\n" for key, value in metadata.items())
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
                    stream.write(output)
            print(output, end="")
    except (ValueError, OSError) as error:
        print(f"release_ref: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
