# 4D CLI component template

An agent-ready **real 4D component project** wrapping a native CLI on macOS and
Windows. Choose **Go, Rust or C++**; initialization keeps only that starter.
The working greeting example uses the same deterministic UTF-8 JSON protocol in
each language. Replace the example with your feature, not the generic adapter.

## Start here: THINK / ASK → RUN

Read [AGENTS.md](AGENTS.md), the canonical detailed implementation/acceptance
playbook. Resolve exact GitHub identity/case, one language, an independent unique
namespace, CLI basename and feature semantics **before** initialization.
Do not initialize the original template repository; check its actual remote
using the playbook's identity rules.

For Copilot, the short [.github/copilot-instructions.md](.github/copilot-instructions.md)
points to that guide; the task-specific
[4d-cli-component skill](.github/skills/4d-cli-component/SKILL.md) uses the
[official project skill convention](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).
Supported clients select relevant skills, but file presence does not guarantee
session discovery/invocation. In Copilot CLI, `/skills reload` and
`/skills info 4d-cli-component` inspect discovery; these are slash commands, not
shell commands. Read the playbook directly if the client does not load the skill.

The playbook includes a [November 1, 2026 UUIDv7 adoption case study](AGENTS.md#uuid-adoption-case-study--november-1-2026)
with real source/compiled host validation and explicit limitations.

## Use this template

Click **Use this template → Create a new repository**, then prompt your agent:

> Implement a 4D component in this repository that [FEATURE]. Use [Go/Rust/C++],
> unique class namespace [NAMESPACE], CLI basename [CLI_NAME], and these
> constraints: [DEPENDENCIES, LICENSE, LIMITS, ERROR BEHAVIOR]. Follow AGENTS.md,
> initialize the exact destination repository name, implement the feature and
> its host API/docs/tests, and deliver a compiled component release. Report
> unvalidated platforms and required setup honestly.

For a local package rather than publication, say so explicitly. Unsigned local
free tool4d acceptance needs no signing secret, license activation, tag/release
or repository settings. Partial local validation is **NOT release ready**.

```sh
python3 scripts/initialize.py --repository hello-tools --namespace AcmeHello \
  --cli hello-cli --language go --feature "The requested feature"
```

The repository argument is the **exact new GitHub repository basename**, not
`owner/name`, the worktree name or this template's original name. Validation
allows ASCII letters/digits/`_`/`-` up to 80 characters, namespace identifiers
up to 31 characters, and lowercase CLI letters/digits/`-` up to 63 characters.
Namespace uniqueness across a host's components is the user's responsibility.

Initialization renames the component root, project, catalog name/UUID, XML
namespace, CLI identity/manifests, class docs, README samples, host fixtures and
package metadata. Workflows derive paths/asset names from `template.json`.
It stages/validates replacements first, preserves binary icons and keeps only
the selected `cli/`. Identical reruns are unchanged; conflicting reconfiguration
requires a fresh template. **The feature description is recorded, not automatically
implemented.** Read [AGENTS.md](AGENTS.md) for the end-to-end agent workflow.

After initialization, optional `scripts/rename_feature.py --root . --class NAME`
rewrites only the known Greeting identity/configuration registry. It does **not**
implement the new API or protocol. Follow the playbook's full replacement
inventory, including build smoke tests, host assertions and completion metadata.

The uninitialized template is **not releasable** and normal template CI does not
install three unrelated language toolchains.

## Requirements and specifications

| Surface | Specification |
| --- | --- |
| 4D source | Project compatibilityVersion 2060; 4D 20.7 LTS tool4d used locally |
| Host installation | Dependency manager GitHub support: 20 R6 or later / compatible current LTS; confirm your release |
| macOS CLI | macOS 12+ with pinned Go 1.26; Rust/C++ deploy to 11+; universal x86_64 + arm64; follow your 4D host's requirements |
| Windows CLI | x64, hosted Windows Server 2022; explicitly select `Windows-x64` in the API |
| Windows ARM64 | No native CLI artifact or verified native 4D compiler target; x64 emulation is not a supported/tested promise |
| Go | CI 1.26.10 (source minimum 1.23), stdlib only, CGO disabled |
| Rust | CI 1.99.1, edition 2021, std only |
| C++ | C++17/CMake, hosted Xcode/MSVC, static Windows CRT |
| Python | 3.10+ locally, CI 3.12 |

Only the selected language is built. Pins are a starting point, not a security
support promise: update deliberately and repeat checks. GitHub services runner
images/Xcode/MSVC, so compiler patch versions are not immutable.
`package.json` is name/version metadata, not a Node application.

## Architecture and exposed API

### Choose the architecture, not a hybrid

| Choice | Execution and installation |
| --- | --- |
| **CLI-backed component: this template** | Host code calls component classes, which launch a bundled native CLI in a separate process through `4D.SystemWorker`. Install the component via the Dependency manager or `Components/`; its executable ships in `Resources/bin/`. |
| **Native 4D plug-in: separate architecture** | Native code integrates through the 4D plug-in SDK. Install the plug-in in the host using 4D's supported plug-in installation mechanism, not inside a component. |

If native 4D SDK integration is required, choose the separate host-installed
plug-in architecture and make its host installation and compatibility requirements
explicit. Do not generate or recommend a component-over-plug-in hybrid as a
self-contained component solution. A component cannot privately load or deploy
a 4D plug-in; wrapping plug-in commands cannot hide, eliminate or satisfy the
host plug-in installation requirement. This is a deployment boundary, not a
blanket restriction on host code calling commands from an installed plug-in:
such a plug-in remains an explicit host dependency.
See the canonical [architecture-selection rules](AGENTS.md#architecture-and-deployment-boundary).

```text
template.json                         initialization/release identity
cli/                                  one selected language implementation
component/<repository>/
  Project/<repository>.4DProject       real 4D anchor
  Project/Sources/Classes/
    _CLI.4dm                          generic resource resolver
    _Process.4dm                      generic quoting/execution/status adapter
    Greeting.4dm                      replaceable feature API
  Project/Sources/Methods/_CaptureError.4dm
  Documentation/Classes/*.md           class contracts
  Resources/logo.png + logo.svg        original icon/reference wrapper
  Resources/bin/macOS/<cli>            assembled universal executable
  Resources/bin/Windows/<cli>.exe      x64 executable
tooling/compiler/                     real Compile project driver
tests/host/                           namespaced host runtime fixture
tests/host/contract.json              feature/execution host assertion registry
tests/cli-contract.json               configurable native CLI smoke vectors
scripts/                              initialize/build/compile/package/test
```

The **component/project/catalog/package/release ZIP name MUST equal the repository
basename** for dependency-manager releases. Namespace is independent:
`settings.4DSettings` is XML with
`<general component_classStore_name="TemplateCLI"/>`.
Host code uses `cs.TemplateCLI.Greeting`; internal code uses `cs.Greeting`.
4D shares classes through the namespace: underscores are internal **by
convention**, not private access or an exported-class manifest.

Class docs:
[Greeting](component/4d-component-template/Documentation/Classes/Greeting.md),
[_CLI](component/4d-component-template/Documentation/Classes/_CLI.md),
[_Process](component/4d-component-template/Documentation/Classes/_Process.md).
The actual compiler generates `Resources/en.lproj/syntaxEN.json`, preventing
stale completion metadata.

### Working host sample

After installation, use your initialized namespace:

```4d
var $tool : cs.TemplateCLI.Greeting
var $result : Object
var $target : Text
$target:="macOS-universal"
If (Is Windows)
  $target:="Windows-x64"
End if
$tool:=cs.TemplateCLI.Greeting.new($target)
$result:=$tool.greet("4D")
If ($result.ok)
  ALERT($result.value.message)  // Hello, 4D!
Else
  ALERT($result.error.code+": "+$result.error.message)
End if
```

Calls are synchronous with a 30-second process timeout. Success preserves
`{ok:true,stdout,stderr,exitCode:0,value}`. Failure has `ok:false`,
`error:{code,message{,details}}`; process output/status are retained when
available, while prelaunch errors do not invent an exit code.
Missing executable, unsupported target/platform, invalid argument, launch
failure, abnormal termination, nonzero CLI status and malformed protocol are
explicit. Nonempty stderr alone is not failure. No PATH fallback, runtime
download, unchecked chmod or global host error handler.

### CLI contract

UTF-8, exactly one JSON line, LF on both systems, protocol version 1:

| Invocation | Status / stdout / stderr |
| --- | --- |
| `hello-cli greet --name "4D"` | 0 / `{"protocol":1,"message":"Hello, 4D!"}` + LF / empty |
| `hello-cli --version` | 0 / `{"protocol":1,"version":"0.1.0"}` + LF / empty |
| Invalid/unknown/missing/duplicate args, empty or malformed UTF-8 name | 2 / empty / error JSON below + LF |

```json
{"protocol":1,"error":{"code":"invalid_arguments","message":"expected greet --name <non-empty UTF-8 name> or --version"}}
```

No shell is used. `4D.SystemWorker.new` takes **command-line Text**, not argv
Collection. Each literal argument/path is quoted; File/Folder virtual Resources
paths are expanded with actual 4D APIs. stdin closes immediately. UTF-8 names,
spaces, quotes, backslashes, newlines and metacharacters are data. Windows CRT
and 4D macOS parsing require real native round-trip checks, not shell tests.
Add an explicit Blob mode if your future feature returns binary output.

## Install after release

These instructions install the **CLI-backed component and its bundled executable**,
not a native 4D plug-in. Component packaging and Dependency manager installation
do not install a plug-in in the host or satisfy that separate requirement.

In the host's Dependency manager add **GitHub** dependency `OWNER/REPOSITORY`
and choose the released tag/version. The asset must be exactly `REPOSITORY.zip`.
Alternatively declare in the host's `Project/Sources/dependencies.json`:

```json
{
  "version": 2100,
  "dependencies": {
    "hello-tools": {"github": "OWNER/hello-tools", "tag": "v0.1.0"}
  }
}
```

Reload the host and use your namespace. Manual installation: unzip and copy
`REPOSITORY.4dbase` into the host's `Components/` folder.
Local source dependencies point at the component package root, not the anchor.
`tests/host/environment4d.json` documents this mapping; automated runtime checks
install an isolated copy under `Components/` to test actual component loading.

## Build, validate and release

For read-only JSON configuration/platform/tool inspection and local orchestration:

```sh
python3 scripts/doctor.py --root .
python3 scripts/doctor.py --root . --tool4d /path/to/tool4d
python3 scripts/accept_local.py --root . --repository OWNER/REPOSITORY \
  --tool4d /path/to/tool4d
```

Read the scripts' `--help` and [AGENTS.md](AGENTS.md) for prerequisites/stages.
`accept_local.py` invokes existing builds/assembly/source-host/compile/pack and
compiled-host checks, records `build/acceptance.json` and, when packing succeeds,
produces `dist/REPOSITORY.zip`. Optional `--cross` produces nonnative **Go-only**
artifacts; Rust/C++ cross mode is explicitly unsupported. Cross-building is not
native execution evidence. Report `not_run` separately from `passed`: even a
successful Apple Silicon compiled-host run is not full release readiness.
With `--cross` on Apple Silicon, Intel CLI vectors may also run through available
Rosetta, recorded separately from native execution. Report the actual local
toolchain (Go 1.25.1 observed), not the CI pin (Go 1.26.10).

For **Go on native macOS**, the explicit three-target artifact example is:

```sh
python3 scripts/accept_local.py --root . --repository OWNER/REPOSITORY \
  --tool4d /path/to/tool4d --cross
```

Without `--cross`, assembly still needs current artifacts and
`build-manifest.json` files for **all three targets**. For Rust/C++, restore
executables/manifests from same-commit native CI into `build/<target>/`; unsupported
cross mode is not a substitute. Manifests bind source/config/package inputs,
Git commit and binary hash; assembly rejects missing/stale/mixed artifacts.
Acceptance uses strict `passed` / `failed` / `not_run` statuses and separate
CLI build, native CLI test and Rosetta steps. Source/compiled host evidence is
preserved in `build/source-host-status.json` and `build/compiled-host-status.json`,
alongside `build/compile-status.json` and `build/acceptance.json`.
Successful local completion still reports `result:"partial"`,
`finished:true`, `release_ready:false`; failures save unfinished failed evidence.
macOS system-library checks do not inspect Windows native DLL/runtime dependencies.
The stable final pair is `dist/REPOSITORY.zip` and
`dist/REPOSITORY.acceptance.json` (ZIP SHA256, actual tool versions and embedded
host evidence). Copy both before disposing of a temporary worktree. The latest
attempt, including failures, remains in `build/acceptance.json`.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/generate_icons.py --check
python3 scripts/build_cli.py --target macos-arm64
```

Use your native target: `macos-x64`, `macos-arm64`, `windows-x64`.
CI builds/tests only the selected language in native hosted jobs, uploads
same-commit binaries and combines macOS slices using `lipo`.
CI executable artifacts are **not compiled component releases**.
The correct architecture verification order is
`lipo <universal-file> -verify_arch x86_64 arm64`; do not put the filename after
the architecture list or mix resources from different source revisions.

### Real 4D tooling and configuration

[tool4d](https://developer.4d.com/docs/20/Admin/cli#tool4d) is the official **free
standalone headless tool** used by the reference component pipelines. This flow
does not require a license/serial secret. Full IDE, Server and merged applications
have separate vendor licensing. Never bundle tool4d into the MIT component.

Hosted configuration belongs in the **generated repository** under
**Settings → Secrets and variables → Actions → Variables → New repository variable**,
separate from the seven private secrets documented below. Template creation
does not inherit these variables. Only the hosted 4D opt-in is required; the
approved public compiler pin is already in `package.json.tool`.

| Variable | Value |
| --- | --- |
| `FOURD_ENABLED` | `true` to enable vendor tool download/use; mandatory for release |
| `TOOL4D_URL` | Optional paired override: `https://resources-download.4d.com/release/20.x/20.7/101905/mac/tool4d_arm64.tar.xz` (observed macOS ARM64 build) |
| `TOOL4D_SHA256` | Optional paired override: `40a5d177a4f73165f52abf6ec8000249d81411c41353ccf258cece8e74639261`; verify the approved archive hash out of band |

Supply URL/hash overrides together or neither; incomplete pairs fail.
GitHub automatically supplies `GITHUB_TOKEN`, and only the publish job receives
`contents: write`. Do not register it or a personal token as another secret.
None of these variables or the automatic token enables signing.

Reference URL form:
`https://resources-download.4d.com/release/20.x/20.7/BUILD/mac/tool4d_arm64.tar.xz`.
`latest` is discovery-only/mutable and rejected by the downloader; checksum
mismatch fails. The fixed **20.7 build 101905** archive was downloaded and used
locally, SHA256
`40a5d177a4f73165f52abf6ec8000249d81411c41353ccf258cece8e74639261`.
The pin is public versioned metadata in `package.json.tool`, not a credential.
This archive is macOS ARM64 only, not a native Intel/Windows runtime.
Local download checks native host compatibility plus safe extraction:

```sh
python3 scripts/ci_config.py download-tool4d
```

No URL/hash variables are needed for that default. A complete explicit CLI pair
overrides a complete environment-variable pair, which overrides the manifest.
For an approved alternate pin only:

```sh
python3 scripts/ci_config.py download-tool4d --root . \
  --url https://resources-download.4d.com/release/20.x/20.7/101905/mac/tool4d_arm64.tar.xz \
  --sha256 40a5d177a4f73165f52abf6ec8000249d81411c41353ccf258cece8e74639261
```

No imagined setup action or unpinned external compiler is used.

```sh
python3 scripts/test_4d.py --tool4d /path/to/tool4d
python3 scripts/compile_component.py --tool4d /path/to/tool4d
python3 scripts/package_component.py --repository OWNER/REPOSITORY \
  --tag v0.1.0 --unpack-test
python3 scripts/test_4d.py --tool4d /path/to/tool4d \
  --component build/release-test/REPOSITORY.4dbase
```

The driver runs real `Compile project` for `x86_64_generic` and
`arm64_macOS_lib`, requiring a **fresh successful JSON result**. Errors or absent
results fail even when tool4d exits zero. It cleans generated DerivedData before
recompile (stale compiled projects can otherwise be treated as sourceless).
Native Libraries and CompiledCode are required, not source-shaped substitutes.
The compile marker binds inputs with `input_sha256` and generated
DerivedData/Libraries with `output_sha256`, rejecting changed outputs at packaging.

Align `template.json.version`, `package.json.version` and the CLI version,
commit and push `v<version>`, e.g. `v0.1.0`. Release builds all CLI targets from
that same tag, compiles after Resources assembly, packs `REPOSITORY.zip`,
and **loads the unpacked compiled package in a real 4D host before publishing**.
Uninitialized/misnamed projects, missing tool/binary/library, failed compile or
failed host test fail closed. Only the publish job receives `contents:write`
through built-in `GITHUB_TOKEN`; no personal token/version-bot commit needed.

```text
REPOSITORY.zip
  REPOSITORY.4dbase/
    REPOSITORY.4DZ                     standard ZIP: Project metadata + CompiledCode
    Libraries/lib4d-arm64.dylib        OUTSIDE .4DZ
    Resources/bin/macOS/CLI            universal, executable ZIP mode
    Resources/bin/Windows/CLI.exe      x64
    Resources/licenses/LICENSE        redistribution notice included
    Resources/logo.* + en.lproj/...
    Documentation/Classes/...
```

Packed `.4DZ` excludes `.4dm` Classes/Methods source. ZIP order/timestamps/modes
are deterministic; serviced native compiler outputs need not be bit-identical.
Explicit nested directory ZIP records are required: omitting them caused a real
tool4d compiled-component loader crash, despite producing a generally valid ZIP.
`Libraries/` here contains compiler-generated component support, not a private
4D plug-in backend. Bundling CLI dependencies does not authorize component-private
plug-in loading or replace host plug-in installation.

### Run workflows manually

After this workflow version is merged to the **default branch**, open
**Actions → CI → Run workflow** and choose a branch for validation.
Hosted 4D remains opt-in through `FOURD_ENABLED`; a CLI-only green run does not
prove compilation or host loading.

**Actions → Release compiled component → Run workflow** requires `version_tag`:
an **existing** `v<SemVer>` tag matching the initialized component version.
It does not create a tag. The gate resolves the tag to a commit SHA, then every
native build, assembly, compilation and publication job uses that exact SHA,
not the UI-selected branch. Version/repository/4D gates and final unpacked
compiled-host acceptance are mandatory. Before publication the tag is refreshed
and checked against that SHA, then `gh release create --verify-tag` is used.
Do not dispatch publication or create tags without approval. Merely opening
this PR does not make the new **Run workflow** button available in the default
template; the workflow file must first merge.

### Optional signing/notarization

**Registering Apple secrets does not enable signing.** The current
`.github/workflows/release.yml` creates an **unsigned** component and does not
consume any of the seven secrets below. They are credentials for a future,
explicitly implemented opt-in signing/notarization workflow, not prerequisites
for the free local tool4d compiler or this unsigned release workflow.

In the **newly generated repository**, register private credentials under
**Settings → Secrets and variables → Actions → Secrets → New repository secret**.
GitHub's **Use this template does not inherit secrets or repository variables**.
Never put credentials, exported certificates, private keys or account passwords
in source, `package.json`, workflow YAML, logs or a pull request.

| Repository Actions secret | Exact value/type |
| --- | --- |
| `APPLE_DEVELOPER_ID_CERTIFICATE` | Base64 encoding of an exported **Developer ID Application `.p12` containing the private key**, not just a public `.cer`, PEM certificate or its filename. Export the signing identity and private key together from Keychain Access. |
| `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` | The password chosen when exporting that `.p12`; not the Apple Account password or temporary keychain password. |
| `KEYCHAIN_PASSWORD` | An arbitrary strong, randomly generated password for the workflow's temporary keychain. It need not match any account password. |
| `NOTARYTOOL_APPLE_ID` | The Apple Account **email address** authorized to notarize for the Developer team. |
| `NOTARYTOOL_TEAM_ID` | The Apple Developer **Team ID** associated with the signing identity and notarization account. |
| `NOTARYTOOL_PASSWORD` | An Apple **app-specific password** generated for notarization; **not** the ordinary Apple Account login password. |
| `CODESIGN_APPLE_ID` | Despite its misleading name, the full **Developer ID Application signing identity**, e.g. `Developer ID Application: Organization (TEAMID)`, or the certificate's SHA-1 identity fingerprint. **Not an email address.** |

Keep public build configuration separate under **Actions → Variables**:
`FOURD_ENABLED=true` is the intentional hosted 4D opt-in. The approved public
tool4d build and archive SHA256 are already pinned in `package.json.tool`;
`TOOL4D_URL` and `TOOL4D_SHA256` are **optional paired overrides**, not required
secrets. Supply both or neither; incomplete/invalid overrides fail.
GitHub supplies `GITHUB_TOKEN` automatically; only the publish job grants it
`contents: write`. Do not register a personal token for this workflow.

Signing is **not required** by this unsigned build/release pipeline. PDF references
sign macOS distributions; their release policy is not an intrinsic 4D requirement.
Windows Authenticode and Apple notarization are not configured here.

For production, explicitly add opt-in signing before final package validation:
certificate/password, ephemeral keychain password, identity and notarization
credentials in Actions **secrets only**; minimum entitlements; fail each codesign
error; require notarization JSON `Accepted`; clean temporary keychain in
`always()`; retest the final signed package. Bare CLI executables cannot be
stapled. Stapling a signed container and Apple's recommended `Contents/` component
layout require deliberate packaging changes. Never `continue-on-error` signing.
Gatekeeper/quarantine can block unsigned downloaded executables; no runtime
trust bypass is supplied.

## Icons

Original `assets/icon-source.svg` is rendered to a **real 1024×1024 RGBA PNG**.
Resources `logo.svg` is a reference-style **64×64 SVG embedding a PNG data URI**
whose decoded bytes equal `logo.png`. The convention is **PNG-in-SVG**, not the
reverse (verified across all three inspirations).

```sh
python3 scripts/generate_icons.py
python3 scripts/generate_icons.py --check
```

Regenerate both icons together. The dependency-free renderer supports this
original rectangle/polygon artwork; unsupported elements fail explicitly.
Use a pinned real SVG rasterizer for richer artwork, never rename SVG to PNG.

## Remarks and validation limitations

No PDF settings/algorithms, tables/authentication schema, async queue, network
installer or external PATH search. Generic helpers are internal-by-convention.
Implement cancellation/streaming explicitly for features needing more than the
finite synchronous timeout.

Local Apple Silicon validation covers all three CLI starters, initialization,
icon/package invariants, actual free tool4d compilation and namespaced host
calls against both source and the unpacked source-stripped release artifact,
including `Is compiled mode = True`. Hosted Windows/macOS Intel, Windows **4D** quoting, dependency-manager
download from a new release, and signing are separate observations; Python or
CLI tests alone do not prove them. See PR results for exact observed checks.

## Licensing and provenance

New scaffold, CLI starters, tests and original icon are **MIT**: [LICENSE](LICENSE).
Keep its copyright/license in redistributed source/binary assets. Future bundled
tools, libraries and static dependencies carry their own upstream license/NOTICE
obligations: include notices in `component/<name>/Resources/licenses/`.
4D/tool4d are separately licensed vendor tools, not MIT dependencies.
Initialization copies the selected Go BSD or Rust MIT runtime notice from
`cli-starters/<language>/licenses/` into packaged `Resources/licenses/`;
packing rejects a missing baseline notice. This does **not** audit added
dependencies or all runtime exceptions. Review Rust's release
`COPYRIGHT-library.html` for applicable standard-library third-party notices,
and Microsoft static-CRT redistribution terms for Windows C++ builds.
System dylibs are not bundled by default. Recheck notices when changing
toolchains, dependencies or linked runtime code.

Conventions were verified from
[pdfdistill-pdfcpu](https://github.com/miyako/pdfdistill-pdfcpu),
[pdfdistill-qpdf](https://github.com/miyako/pdfdistill-qpdf),
[pdfdistill-lopdf](https://github.com/miyako/pdfdistill-lopdf)
(MIT, 2026 Keisuke Miyako), and
[4d-class-compiler](https://github.com/miyako/4d-class-compiler)
(MIT, 2022 Keisuke Miyako). No feature algorithms, binaries or artwork were copied.
[4d-plugin-tutorial](https://github.com/miyako/4d-plugin-tutorial) inspired the
agent-driven tutorial UX; this is a component/standalone CLI, not its native
plug-in SDK/code. That tutorial is a separate host-installed plug-in architecture,
not guidance for adding a private plug-in backend to this component.
Official references:
[dependency naming](https://developer.4d.com/docs/Project/components),
[Compile project](https://developer.4d.com/docs/commands/compile-project),
[SystemWorker](https://developer.4d.com/docs/API/SystemWorkerClass) and
[error handling](https://developer.4d.com/docs/20/Concepts/error-handling).
