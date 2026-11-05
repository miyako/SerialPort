Read `AGENTS.md` (canonical THINK/ASK → RUN playbook), `README.md`,
`template.json`, class documentation and relevant scripts/tests before editing.
For component adoption, also read `.github/skills/4d-cli-component/SKILL.md` if
the client did not load it; its presence does not prove invocation.
Choose either this CLI-backed component architecture or a separate native 4D
plug-in installed in the host. Never generate or propose a component-over-plug-in
hybrid as a self-contained component solution. A component cannot privately
load/deploy a plug-in; component packaging cannot hide, eliminate or satisfy
host plug-in installation. If native 4D SDK integration is required, choose the
separate plug-in architecture and document the host dependency explicitly.
This is a deployment boundary, not a blanket claim about host code calling
commands from an installed plug-in.
Never initialize the original `miyako/4d-component-template` repository.
In an uninitialized destination, use `scripts/initialize.py` with the exact
case-sensitive GitHub basename, user-selected Go/Rust/C++ language, independent
unique namespace, CLI basename and feature description. Initialization and
`rename_feature.py` do not implement the feature: replace its API/CLI/docs/tests.
Keep repository/component/project/catalog/package/release names equal; retain
only the selected language and keep generic execution separate from feature code.
Use real 4D APIs, scalar argument guards and strict NUL Position comparison;
never invent SystemWorker argv overloads, setup actions or success fallbacks.
Require same-revision binaries, actual source host tests, real compilation and
host tests against the unpacked source-stripped compiled ZIP. Report `not_run`
separately from `passed`; partial local acceptance is NOT release ready.
Follow AGENTS.md for fixed free tool4d, licensing, packaging and optional signing.
Unsigned local acceptance needs no license activation or signing secrets.
