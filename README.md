# SerialPort

First asynchronous serial communication prototype for **4D**, implemented as a
real CLI-backed component: `cs.Serial.SerialPort` -> one persistent
`4D.SystemWorker` -> bundled C++17/standalone Asio helper -> multiple serial ports.
No 4D plug-in SDK or host plug-in installation is involved.

Repository/component/project/package identity: **SerialPort**. Independent
namespace: **Serial**. Public class: **SerialPort**. Executable:
**serial-port**, version **0.1.0**. Source compatibilityVersion is 2060;
the inherited available test runtime is free **tool4d 20.7 LTS/build 101905**
on macOS ARM64. This prototype targets that runtime, not a promise of support
on every earlier release. macOS CLI deployment target is 11+, subject to the
4D host's own minimum. Windows target is x64/MSVC/static CRT; ARM64 is unsupported.

## API and owner lifetime

See [SerialPort](component/SerialPort/Documentation/Classes/SerialPort.md),
[_CLI](component/SerialPort/Documentation/Classes/_CLI.md),
[_Process](component/SerialPort/Documentation/Classes/_Process.md), and
[_LineProcess](component/SerialPort/Documentation/Classes/_LineProcess.md).

Construct/retain the session **in a live `CALL WORKER` owner**, then let the
method return. Do not call `.wait()` as the normal callback path. Commands to
that session must execute in that same worker. Native I/O is asynchronous;
strict preemptive 4D compatibility is not claimed.

Minimal host method `Serial_demo`:

```4d
#DECLARE($command : Text; $port : Text)
var SERIAL : cs.Serial.SerialPort
var $result : Object
Case of
  : ($command="start")
    var $target : Text
    $target:="macOS-universal"
    If (Is Windows)
      $target:="Windows-x64"
    End if
    SERIAL:=cs.Serial.SerialPort.new($target; Formula(Serial_demo_event($1)))
    $result:=SERIAL.start()
    If ($result.ok)
      $result:=SERIAL.open($port; New object("baudRate"; 9600; "dataBits"; 8;
        "stopBits"; "one"; "parity"; "none"; "flowControl"; "none"))
    End if
  : ($command="shutdown")
    $result:=SERIAL.shutdown()
End case
// Forward immediate errors to your UI/log; ok:true means submitted, not completed.
```

Start from the UI with
`CALL WORKER("My serial owner"; "Serial_demo"; "start"; "/dev/cu.YOUR_DEVICE")`
or the OS's `"COM3"` path. The `Serial_demo_event` callback receives one Object:

```4d
#DECLARE($event : Object)
var SERIAL : cs.Serial.SerialPort
// Store open reply's result.connection; use SERIAL.write(id; blob; 1000).
// data events carry connection and bytes (Blob); accumulate device framing.
// Forward errors/disconnections to UI via CALL FORM or CALL WORKER(1; ...).
If ($event.event="terminated")
  SERIAL:=Null
  KILL WORKER
End if
```

Implement logging/UI handling in the comments before using this demo in production.
For a complete bounded-log demo, import the two methods from
[examples/](examples/), then dispatch `start`, `write` (Blob third parameter)
and `shutdown` to the same worker. Events, including base64 binary chunks, are
written to `SerialPort-demo.json` in 4D's temporary folder; errors are forwarded
to the main process. The demo fixes 9600/8N1/no flow control: change those settings
only to equipment-approved values.
Use `CALL WORKER("My serial owner"; "Serial_demo"; "shutdown"; "")` to release
all ports. Wait for `terminated` before disposing the owner. Do not copy the
live session across workers or retain transient pointers in formulas.
The real runnable harness methods under `tests/host/` demonstrate complete
callback correlation, negative replies, owner return and clean shutdown.

## Binary I/O and IPC

`serial-port serve` accepts UTF-8 JSON lines on stdin and emits flushed ASCII
JSON lines on stdout. Diagnostics use stderr only. JSON escapes preserve Text;
base64 preserves all bytes, including NUL and non-UTF-8 data.

```json
{"protocol":1,"id":"1","op":"list"}
{"protocol":1,"id":"2","op":"open","path":"/dev/cu.device","config":{"baudRate":9600,"dataBits":8,"stopBits":"one","parity":"none","flowControl":"none"}}
{"protocol":1,"id":"3","op":"write","connection":"c1","data":"AP+A","timeoutMs":1000}
{"protocol":1,"id":"4","op":"cancel","requestId":"3"}
{"protocol":1,"id":"5","op":"close","connection":"c1"}
{"protocol":1,"id":"6","op":"shutdown"}
```

Responses are `{protocol:1,id,ok:true,result:Object}` or
`{protocol:1,id,ok:false,error:{code,message,...}}`. Connection IDs are stable
for the life of that connection and not shared across sessions.
Events include `data` with `connection`/base64 `data`, `error`, `disconnected`
and `session_limit`. The 4D facade adds decoded Blob `bytes` and local
`diagnostic`/`terminated` events. `configure` accepts a connection and config.
The exact native [protocol contract](cli/PROTOCOL.md) defines validation bounds,
queue limits, cancellation side effects and disconnect behavior.
Serial writes are full serialized operations; timeout/cancel can mean a prefix
already reached the device. Never assume atomic wire transmission or blindly
retry a partial operation. Cancel/timeout closes the affected connection and
cancels all its queued writes. Inspect error `transferred` counts.

Limits: 65536-byte IPC frame, 32768 decoded bytes/write, 16 open ports,
128 pending requests, 256 KiB queued writes/port. Oversized input, invalid
configuration, invalid base64, queue overflow and unknown IDs are explicit
errors. Arbitrarily fragmented/coalesced stdin/stdout is reassembled by LF,
not by callback boundaries. The helper bounds output queues and ends rather
than accumulating indefinitely behind a stalled reader: its 256 KiB output
queue has a two-second stall deadline; overflow/broken output exits 3 with
`output_failed` diagnostics. Ingress holds at most 128 frames before applying
pipe backpressure. Configure failure (even invalid configuration) and per-port
write queue overflow close the connection; settings are not transactional.
Parent liveness is monitored independently of stdin EOF every 250 ms:
POSIX reparent detection, or a retained Windows parent process handle.
Watcher setup/wait failures report `parent_watch_failed` rather than silently
waiving cleanup. The macOS test deliberately retains a stdin writer after
parent death and verifies helper termination and serial port release.

SystemWorker's response memory is accumulated internally: sessions deliberately
end at **8 MiB protocol output or one hour**, with explicit session-limit
notification. No silent restart/reconnect. The caller must deliberately create
a new session and reopen ports. EOF/shutdown releases serial ports and pending
operations. Closing/cancelling may invalidate a connection; inspect events and
write error details before retrying.

Data callbacks are **byte chunks, not device protocol messages**. This first
prototype prioritizes Blob correctness and has no streaming text decoder.
Keep encoding/decoder state per connection yourself; do not independently
decode chunks that may split a multibyte character. No global `USE CHARACTER SET`.

## Scope and hardware placement

Multiple devices can be open simultaneously. OS/driver errors and unexpected
disconnects are surfaced; no automatic reconnection. Port enumeration is
platform-specific, because Asio does not enumerate ports. Bluetooth Classic
works only if the OS already exposes a serial port. BLE, pairing/discovery,
direct Bluetooth, special RS485 direction control and real-time guarantees
are excluded. Opening a port may toggle driver/device control signals; validate
your actual equipment before industrial deployment.

Hardware is local to the process running the helper. On 4D Server this means
**server-local ports**, not a client's USB device. Run the component on the
client when client-local hardware is intended.

## Build and validation

Read [AGENTS.md](AGENTS.md) before changing identity, architecture or delivery.
Initialization is complete and retains only C++. CMake pins header dependencies
with SHA256; bundled notices live in
`component/SerialPort/Resources/licenses/`. Retain template MIT attribution
and dependency/runtime redistribution notices. Vendor tool4d is never bundled.

```sh
python3 scripts/doctor.py --root .
python3 -m unittest discover -s tests -v
python3 scripts/generate_icons.py --check
python3 scripts/build_cli.py --root . --target macos-arm64
```

Use the actual native target (`macos-arm64`, `macos-x64`, `windows-x64`).
The native CMake tests exercise actual Asio serial I/O through pseudoterminals
on macOS; portable protocol tests run on Windows. Existing CI builds each native
target, checks protocol vectors and binds binaries to input hashes/manifests.
No C++ cross-build shortcut or mixed-revision artifacts.

For local source integration, copy the **native** freshly built helper into
`component/SerialPort/Resources/bin/macOS/serial-port` (Windows:
`bin/Windows/serial-port.exe`), then run the real host harness. This native-only
development resource is not a universal/release artifact.

```sh
python3 scripts/test_4d.py --tool4d .tools/tool4d.app/Contents/MacOS/tool4d
python3 scripts/compile_component.py --tool4d .tools/tool4d.app/Contents/MacOS/tool4d
```

Full packaging requires all three same-source native binaries/manifests:

```sh
python3 scripts/ci_config.py assemble
python3 scripts/accept_local.py --repository miyako/SerialPort \
  --tool4d .tools/tool4d.app/Contents/MacOS/tool4d
```

The inherited assembly/packer refuses missing, stale or mixed artifacts.
It strips source, verifies real compiler output, unpacks the final ZIP and
requires actual compiled host loading. **A native helper/source-host pass is
not compiled-package acceptance or release readiness.** The follow-up milestone
authorizes a prototype branch/PR and GitHub Actions validation, not merging,
tags or release publication. The separately approved macOS signing setup is
described below; normal CI and the release workflow remain unsigned.
Optional hosted 4D is controlled
by `FOURD_ENABLED`. The fixed downloader fails closed if its approved origin
policy rejects a redirect. The downloader validates every redirect hop against
the exact HTTPS vendor hosts `resources-download.4d.com` and `builds2.4d.com`,
with bounded redirects, unchanged pinned SHA256 and safe extraction. It never
falls back to `latest` or an unpinned replacement.

## CI artifacts (not releases)

With the public Actions variable `FOURD_ENABLED=true`, branch/PR CI builds
and tests native macOS Intel, macOS ARM64 and Windows x64 helpers, verifies
same-commit/source manifests, assembles universal macOS resources, and uses
the pinned free tool4d for source, compilation and unpacked compiled-host checks.
Only after all package gates pass does it upload **SerialPort-compiled-component**,
containing `SerialPort.zip` and its SHA256-bound `SerialPort.acceptance.json`.
Artifacts are retained for 14 days; failed runs retain diagnostic reports but
do not upload a success-shaped compiled package.

Download that Actions artifact, extract its `SerialPort.zip`, then unzip the
component ZIP to obtain `SerialPort.4dbase` for a host's `Components/` folder.
This is unsigned development distribution, not a tagged release or a
Dependency-manager download/install acceptance check. No tag, release or merge
is created by the validation workflow. Follow the run/evidence links in the PR
for the exact accepted revision; an older artifact does not approve later code.

Native CLI test success does not prove native Windows/Intel **4D host loading**
or physical-device behavior. The available actual compiled-host gate is macOS
ARM64. Acceptance therefore remains `result:"partial"`, `finished:true`,
`release_ready:false`; signing/notarization and hardware checks remain separate.

## macOS signed distribution (manual CI)

**Signing credentials are not inherited from the template.** Register them in
this repository before expecting a signed artifact. CI fails closed and lists
missing **names only**; an unsigned build or a skipped signing job is not signed
acceptance. Do not paste certificates, private keys or passwords into chat,
issues, workflow files or logs.

In **Settings -> Environments**, configure `macos-signing`. Restrict it to
reviewed branches and enable a required reviewer where available **before**
adding private credentials. The six existing repository Actions secrets are
inherited by jobs using this environment; matching environment secrets can
override them. No seventh signing-identity secret is required:

| Secret | Required value |
| --- | --- |
| `APPLE_DEVELOPER_ID_CERTIFICATE` | Base64-encoded Developer ID Application `.p12`, including its private key |
| `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` | That `.p12` export password |
| `KEYCHAIN_PASSWORD` | Independent strong password for the ephemeral CI keychain |
| `NOTARYTOOL_APPLE_ID` | Authorized Apple account email |
| `NOTARYTOOL_TEAM_ID` | Matching Apple Developer Team ID |
| `NOTARYTOOL_PASSWORD` | Apple app-specific password, not the normal login password |

**The user, never the agent, provisions values.** Use the existing local
provisioner in your own terminal:

```sh
~/.claude/skills/4d-plugin-modernize/set-release-secrets.sh miyako/SerialPort
```

By default it reuses the certificate and credentials already stored in the
login Keychain service `4d-plugin-release`, checks the certificate and asks you
to confirm it before setting the six repository secrets. Do not manually
recreate known Keychain values or add `CERT_P12` by default. Only if the script
reports no stored certificate, or you choose to replace it, select the `.p12`
yourself and use its documented `CERT_P12` option. The agent must not run this
script, retrieve Keychain values or upload credentials.

CI derives the signing identity from the **new isolated keychain only** and
requires exactly one valid Developer ID Application identity for
`NOTARYTOOL_TEAM_ID`. Missing, incompatible or ambiguous identities fail; CI
never picks an unrelated identity from the runner's login keychain.

Run **Actions -> CI -> Run workflow**, select the reviewed branch, and enable
`sign`. The CLI equivalent is:

```sh
gh workflow run ci.yml --repo miyako/SerialPort \
  --ref YOUR_REVIEWED_BRANCH -f sign=true
```

Only this explicit manual event can access signing credentials; push, PR and
reusable release calls cannot activate signing. The workflow retains all
native/provenance checks and uses an isolated keychain, minimal signing-tool
access, unconditional cleanup and no helper entitlements. It signs the assembled
universal helper **before** source-host testing and compilation, then signs
compiler-generated Mach-O libraries **before** recording their output digest.
It never edits stale compiler markers to approve changed outputs.

The flat tool4d-20.7-compatible `.4dbase` layout is preserved. Its `.4DZ` is data,
not a Mach-O executable or an application bundle: the workflow does not pretend
to code-sign it. Native code is individually Developer ID signed, and a signed
DMG seals the complete distributable, including compiled 4D data. This does not
silently adopt the newer `Contents`/`BNDL` component layout or claim a verified
4D Application Builder signing integration for this pinned compiler.

Both ZIP and DMG submissions must return Apple's **Accepted** status. The DMG
is stapled and its ticket validated; the bare helper and ZIP are **not**
stapleable. Use the DMG for a ticket-bearing container; the ZIP's bare-code ticket
lookup can require network access. No signature, notarization or quarantine
checks are bypassed.

After signature, notarization and compiled-host checks pass, CI uploads and
downloads a candidate, verifies the exact returned ZIP/DMG bytes and signatures,
and repeats real compiled-host acceptance. Only then is
**SerialPort-macOS-signed-notarized** uploaded, containing `SerialPort.zip`,
`SerialPort.dmg` and SHA256-bound `SerialPort.signing.json`. The short-lived
`SerialPort-macOS-signing-candidate` is an intermediate round-trip input, not the
final handoff. No tag, Release or merge is created.

**Windows is separate:** the Windows helper still has no configured Authenticode
signing setup; Apple credentials/notarization do not sign PE files or establish
SmartScreen reputation. Do not treat this macOS artifact as a Windows-signed
distribution. A suitable existing Windows certificate or Artifact Signing
service arrangement must be approved and configured separately.

Actual Developer ID/notary execution remains `not_run` until credentials are
provisioned and the signed workflow passes. Even then, clean-machine,
quarantined-download acceptance in the user's intended 4D host and physical
hardware checks remain distinct; CI loading is not a universal Gatekeeper or
offline-use guarantee.

References: [Apple signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac),
[Apple notarization](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow),
[4D component layout change](https://blog.4d.com/notarization-on-macos-changes-to-component-structure/),
[Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool).

## Hardware acceptance

Supply two actual OS port paths. Open both with equipment-approved settings,
send known binary vectors containing `00`, `80`, `FF`, and bytes split across
device frames, verify byte-for-byte replies and ID isolation, exercise
write timeout/cancel, then physically unplug one device while the other remains
active. Verify explicit disconnect/error, no reconnect, close/shutdown and
immediate ability to reopen released ports. Check driver/control-line behavior.

Pseudoterminal disconnect is **not physical USB unplug evidence**. Windows
native/4D, actual hardware/Bluetooth, compiled ZIP loading, dependency-manager
installation and signing must be reported separately as `not_run` until actually
executed. Partial local acceptance remains **NOT release ready**.

## Initial local evidence: 2026-11-05

This initial snapshot was **uncommitted**, based on
`e400910a9a295de2ee7dc6c42aa4d10d4982828a`, before the subsequently approved
branch/PR and cross-platform CI milestone. Native CLI input SHA256:
`1b724d4662c2dba706ecacd22c9d232863af92596512d74442d49eb935a659ee`.

| Check | Status | Observed scope |
| --- | --- | --- |
| Full Python suite | `passed` | 135 tests, 19 intentional template/platform skips |
| Icons | `passed` | Existing deterministic icon check |
| macOS ARM64 helper | `passed` | Native CMake/Apple clang 21.0.0; 26 protocol/PTY tests plus four CLI tests |
| Actual serial engine | `passed` | macOS pseudoterminals: binary bytes, two ports, timeout/cancel, overflow, disconnect, EOF/shutdown and retained-stdin parent death |
| Source 4D host | `passed` | tool4d 20.7/build 101905, owner return, six correlated replies, negative paths, binary write submission, Blob parsing and shutdown |
| Real 4D compilation | `passed` | `x86_64_generic` and `arm64_macOS_lib`; not native Intel host execution |
| Universal/release assembly | `failed` | Missing same-source native Intel and Windows artifacts/manifests |
| Final compiled ZIP/host | `not_run` | No release ZIP produced; assembly gate preserved |
| Native Intel CLI/4D | `not_run` | Native runtime/build unavailable locally |
| Windows x64 CLI/4D | `not_run` | Existing native CI/CTest wiring retained, not dispatched |
| Physical USB/Bluetooth/unplug | `not_run` | No physical serial hardware acceptance performed |
| Dependency-manager install/signing | `not_run` | No publication; unsigned development only |

The 4D fragmented/coalesced Blob checks use explicit parser fixtures; they are
not physical serial I/O evidence. Actual serial engine evidence comes from
the native PTY tests. The real source host launches the final bundled helper
and exercises its asynchronous protocol; it does not open a physical device.
The one-hour lifetime deadline has not been waited out in a test.

Commands: `python3 -m unittest discover -s tests -v`,
`python3 scripts/generate_icons.py --check`,
`python3 scripts/build_cli.py --root . --target macos-arm64`,
`python3 scripts/test_4d.py --root . --tool4d .tools/tool4d.app/Contents/MacOS/tool4d`,
`python3 scripts/compile_component.py --root . --tool4d .tools/tool4d.app/Contents/MacOS/tool4d`.
`otool -L build/macos-arm64/serial-port` shows only system libc++/libSystem.
Windows DLL/static-CRT redistribution and hardware behavior still require
native acceptance; notice presence is not a legal audit.

The final native development executable is `build/macos-arm64/serial-port`,
also copied into the component's native-only development Resources. SHA256:
`f39d50483a9398c2e9a17d1d72f220220ac2c2c15d9b8906c19646246e2a5c54`.
Adjacent `build-manifest.json` binds its source input and binary hashes.
`build/source-host-status.json`, `build/compile-status.json`,
`build/python-tests.log` and `build/source-host-run.log` retain local evidence.

`python3 scripts/accept_local.py --root . --repository miyako/SerialPort --tool4d .tools/tool4d.app/Contents/MacOS/tool4d`
records the mandatory assembly failure in `build/acceptance.json` with
`result:"failed"`, `finished:false`, `release_ready:false`. Its downstream
host/compile/package rows remain `not_run` for that blocked orchestration
attempt; the separately executed source/compile reports above are distinct
evidence. **No `dist/SerialPort.zip` or ZIP SHA256 is claimed.**
