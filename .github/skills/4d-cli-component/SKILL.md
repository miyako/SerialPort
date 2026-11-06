---
name: 4d-cli-component
description: WHEN TO USE - select the CLI-backed component versus host-installed native 4D plug-in architecture; create or adapt a real 4D component wrapping a Go, Rust or C++ CLI from this template; initialize an exact destination repository; change a feature class or namespace; replace Greeting with a feature API; diagnose SystemWorker quoting and errors; compile locally with free pinned tool4d; run source and compiled host tests; or package and assess release readiness.
---

# 4D CLI component adoption and acceptance

Read the repository root `AGENTS.md` first: it is the canonical detailed
THINK/ASK → RUN playbook, including stop rules and the dated UUID case study.
Then read `README.md`, `template.json`, class documentation and relevant
scripts/tests. Resolve all paths below from the repository root, not this folder.

## Architecture gate: CLI-backed component or host-installed plug-in

This template supports a component facade over a **bundled native CLI executable**
running as a separate process through `4D.SystemWorker`. Install the component
through the Dependency manager or the host's `Components/` folder; the CLI ships
in `Resources/bin/`. If native 4D SDK integration is required, choose a **separate
native 4D plug-in architecture**, with the plug-in installed in the host and its
installation/compatibility requirements explicit.

Never generate or propose a component-over-plug-in hybrid as a self-contained
component solution. A component cannot privately load or deploy a 4D plug-in;
component packaging cannot hide, eliminate or satisfy host plug-in installation.
Wrapping commands from an already installed plug-in leaves an explicit host
dependency; this is not a blanket claim about whether host code can call those
commands. Component compiler `Libraries/` and standalone CLI libraries are not
a private 4D plug-in backend. Follow AGENTS.md's architecture gate before adoption
or packaging; do not turn an SDK requirement into a component resource.

## Adoption and acceptance sequence

1. **THINK / ASK:** select the architecture above, then establish exact
   case-sensitive GitHub identity, one
   user-selected language, independent unique namespace, CLI basename, feature
   API/protocol/error/limits, licenses and acceptance scope. Never initialize the
   original template repository or guess material missing decisions.
2. **Inspect:** `python3 scripts/doctor.py --root .` reports configuration,
   platform/tools; optional `--tool4d PATH` checks the supplied tool. Do not
   interpret this read-only report as feature/runtime acceptance.
3. **Initialize the destination only:** use the initializer flags documented
   in AGENTS.md. It records the feature description and selects/renames a starter;
   it does not implement the feature.
4. **Replace the whole feature contract:** API class, CLI, docs, language/Python
   tests, `tests/cli-contract.json` native smoke vectors, host fixture and `tests/host/contract.json`
   feature/report expectations, metadata/notices and compiler completion metadata.
   Preserve the registry's separate generic execution checks. Optional
   `python3 scripts/rename_feature.py --root . --class NAME` is a bounded Greeting
   identity rewrite, not automatic feature implementation.
5. **Preserve real 4D execution:** keep `_CLI`/`_Process` generic, quote into
   SystemWorker command-line Text, guard scalar values before object-instance
   checks, use strict `Position(Char(0); argument; *)`, close stdin and preserve
   separate stdout/stderr/exitCode/system errors. Test native argument round trips.
6. **Validate:** follow AGENTS.md's Python/icon/native CLI/source-host/real
   compiler/pack/unpack/compiled-host sequence. Optional
   `python3 scripts/accept_local.py --root . --repository OWNER/NAME --tool4d PATH`
   orchestrates the existing stages and records `build/acceptance.json`.
   `--cross` is optional **Go-only artifact production**, not native foreign
   execution; available Rosetta CLI tests are separate translated evidence.
   Rust/C++ cross mode is unsupported. Packed output is `dist/<repository>.zip`
   only when required artifact/package checks succeed.
   Without `--cross`, all three current artifacts/manifests are still needed.
   For Rust/C++, restore binaries plus `build-manifest.json` from same-commit
   native CI into `build/<target>/`; never mix stale or changed inputs/outputs.
7. **Report:** identify exact revision/platform/tools, commands, artifact/evidence
   paths, `passed` / `failed` / `not_run` per check and blockers. Partial local success
   is **NOT release ready**. Do not publish or add credentials without approval.
   Unsigned free tool4d local compilation needs no license/signing secrets.
   In this adopted destination, explicit manual CI and main-only Publish use
   six Apple secrets through the approved signing pipeline. Publish derives
   its version/tag automatically and remains a prototype prerelease; credential
   registration is not proof of actual signing or package-host acceptance.

## Discovery is supported, invocation is not guaranteed

Official Copilot documentation supports project skills at
`.github/skills/<lowercase-hyphen-name>/SKILL.md`, with YAML `name` and
`description`; Copilot chooses relevant skills using the prompt/description.
This file does not install a toolchain, run scripts automatically, or guarantee
that every client/version/session sees it. No shell tools are pre-approved here.

On a supported Copilot CLI, start a new session or use `/skills reload`, then
`/skills info 4d-cli-component` to inspect discovery. These are **CLI slash
commands**, not shell commands; do not run them through Bash. Other surfaces
use their documented controls. If unavailable, read this file and AGENTS.md
directly and report skill invocation as unverified.

Conventions verified November 1, 2026:
[Agent skills](https://docs.github.com/en/copilot/concepts/customizations/agent-skills),
[adding skills](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/add-skills),
[CLI skill setup/discovery](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).
