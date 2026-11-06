#!/usr/bin/env python3
"""Validate version refs and create new tags only after explicit asset acceptance."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

from ci_config import ROOT, SEMVER, load_config


def validate_tag(tag):
    if not isinstance(tag, str) or not re.fullmatch("v?" + SEMVER, tag, flags=re.ASCII):
        raise ValueError("release tag must be SemVer (legacy v prefix accepted), not an arbitrary ref")
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


def require_main(env=None):
    env = os.environ if env is None else env
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or env.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Publish must be manually dispatched on main")


def available(root, repository, tag):
    validate_tag(tag)
    if tag.startswith("v"):
        raise ValueError("new release tags must be prefix-free")
    if not isinstance(repository, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_-]{0,79}", repository, re.ASCII):
        raise ValueError("invalid GitHub repository identity")
    load_config(root, initialized=True, repository=repository, tag=tag)
    for resource in ("git/ref/tags/", "releases/tags/"):
        result = subprocess.run(
            ["gh", "api", "--include", "repos/" + repository + "/" + resource + tag],
            cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
        status = re.match(r"HTTP/[0-9.]+ ([0-9]{3})[ \r\n]", result.stdout)
        if status and status[1] == "200" and result.returncode == 0:
            raise ValueError("release tag or GitHub release already exists: " + tag)
        if not (status and status[1] == "404" and result.returncode == 1):
            raise ValueError("cannot establish release availability; GitHub did not return an explicit 404")


def create_tag(root, repository, tag, revision):
    require_main()
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision, re.ASCII):
        raise ValueError("tag creation requires the full normalized accepted commit SHA")
    available(root, repository, tag)
    subprocess.run(["git", "fetch", "--no-tags", "origin",
                    "+refs/heads/main:refs/remotes/origin/main"], cwd=root, check=True)
    subprocess.run(["git", "merge-base", "--is-ancestor", revision, "refs/remotes/origin/main"],
                   cwd=root, check=True)
    # Atomic reference creation rejects a racing tag, even at the same SHA.
    subprocess.run(["gh", "api", "--method", "POST", "repos/" + repository + "/git/refs",
                    "-f", "ref=refs/tags/" + tag, "-f", "sha=" + revision],
                   cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    subprocess.run(["git", "fetch", "--no-tags", "origin",
                    "refs/tags/" + tag + ":refs/tags/" + tag], cwd=root, check=True)
    result = subprocess.run(["git", "ls-remote", "--exit-code", "origin", "refs/tags/" + tag],
                            cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    if result.stdout.strip() != revision + "\trefs/tags/" + tag:
        raise ValueError("remote release tag does not match the accepted commit")
    verify(root, tag, revision)


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
    availability = commands.add_parser("available")
    availability.add_argument("--repository", required=True)
    availability.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        if args.command == "available":
            require_main()
            available(args.root, args.repository, args.tag)
            print("Release tag and GitHub release are available.")
        elif args.command == "verify":
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
    except (ValueError, OSError, TypeError, subprocess.SubprocessError) as error:
        print(f"release_ref: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
