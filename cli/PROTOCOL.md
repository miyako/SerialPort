# serial-port 0.1.0

C++17 standalone Asio process; no 4D SDK or host-installed plug-in. Bluetooth
devices are supported only when the OS exposes them as serial ports. No pairing,
discovery, reconnect, shell invocation or runtime dependency download.

`--version` returns `{"protocol":1,"version":"0.1.0"}`.
`list` returns `{"protocol":1,"ports":[{"path":"…","name":"…"}]}`.
macOS lists `/dev/cu.*` and `/dev/tty.*`; these can be two aliases of a device.
Windows-x64 lists OS SERIALCOMM registry entries, with paths `\\.\COMn`.
Enumeration is a snapshot, not a promise that an entry is openable.
`echo --text TEXT` returns `{"protocol":1,"text":"…"}` (empty Text allowed).
Invalid argv, including malformed UTF-8, exits 2 with JSON error on stderr.

## Persistent transport

Launch `serial-port serve` with redirected stdin/stdout pipes. Submit strict
UTF-8 JSON Objects followed by LF. Input may be arbitrarily fragmented or
coalesced. Stdout contains only LF-delimited ASCII JSON (Unicode escaped),
written immediately without stdio buffering. Stderr is diagnostic-only.
Windows serve requires pipes, not an interactive console.

Every request has numeric integer `protocol:1`, nonempty Text `id` (at most 128
UTF-8 bytes, no NUL), and nonempty Text `op`. IDs must be unique among pending
writes; completed IDs may be reused. Malformed frames return an error with
`id:""` when no trustworthy ID was parsed. Extra fields are ignored.
JSON nesting is limited to 64 levels.

Responses:

```json
{"protocol":1,"id":"r1","ok":true,"result":{}}
{"protocol":1,"id":"r1","ok":false,"error":{"code":"invalid_request","message":"…"}}
```

| op | Required additional fields | Success result |
| --- | --- | --- |
| list | none | `{"ports":[{"path":"…","name":"…"}]}` |
| open | `path:Text`, `config:Object` | `{"connection":"connection-N"}` |
| configure | `connection:Text`, complete `config:Object` | `{"connection":"…"}` |
| close | `connection:Text` | `{"connection":"…"}` |
| write | `connection:Text`, `data:Text` canonical base64, `timeoutMs:integer` | `{"connection":"…","transferred":N}` |
| cancel | `requestId:Text` of a pending write | `{"requestId":"…","cancelled":true}` |
| shutdown | none | `{}`, then process exits 0 |

Config always supplies integer `baudRate` 1–4000000, integer `dataBits` 5–8,
`stopBits:"one"|"onePointFive"|"two"` (integer 1/2 also accepted),
`parity:"none"|"odd"|"even"` and
`flowControl:"none"|"software"|"hardware"`. OS/device constraints can reject
otherwise valid values, including one-and-a-half stop bits on macOS.
Configuration is not transactional: any configure
failure closes the connection to avoid leaving partially applied settings.
Open failure releases the handle and returns `serial_error` for OS errors or
`invalid_request` for field errors. Connection IDs increase for each attempted
open, are stable until closed, never reused during a helper lifetime, and are
not globally unique across helpers. Opening an alias/already-open port follows
OS sharing/exclusivity semantics; the helper adds no cross-process lock.

Reads are asynchronous and carry arbitrary binary bytes as canonical base64:

```json
{"protocol":1,"event":"data","connection":"connection-1","data":"AAE="}
{"protocol":1,"event":"error","connection":"connection-1","error":{"code":"read_failed","message":"…"}}
{"protocol":1,"event":"disconnected","connection":"connection-1","error":{"code":"closed","message":"connection closed"}}
{"protocol":1,"event":"session_limit","connection":"","error":{"code":"output_limit","message":"…"}}
```

Read chunks are at most 4096 bytes; no message boundaries are inferred.
Requests on different ports run concurrently. Per-port writes are fully
serialized through `asio::async_write`, including zero-length writes.
`transferred` means accepted by the OS serial write, not acknowledged or fully
drained by the device.

Write timeout is 1–3600000 milliseconds, measured from enqueue (includes queue
waiting). Timeout and cancel **close the affected connection**, cancelling all
its queued writes and its read. The targeted write returns `timeout` or
`cancelled`, with `error.transferred` (possibly partial for an active write;
zero for queued writes). Other affected writes return the close reason and
their transferred count. OS write failure returns `write_failed`. These errors
are never success; retry may duplicate bytes already delivered. Cancel response
and active-write terminal response may arrive in either order. Unknown target
returns `request_not_found`. Disconnect events can precede the close response.

## Hard bounds and cleanup

* Frame: 65536 bytes excluding LF. Oversized frame returns `frame_limit`, closes
  all ports and exits 2. Partial frame at EOF returns `invalid_frame`, including
  Windows broken-pipe EOF reported by either PeekNamedPipe or ReadFile.
  Complete frames already received retain their responses before the terminal
  frame error; queued stdout drains before process exit.
* Decoded write: 32768 bytes; only canonical padded RFC 4648 base64 accepted.
  Empty base64 is valid. Invalid/oversized writes return `invalid_request`.
* Connections: 16 (`port_limit`).
* Pending asynchronous writes: 128 (`pending_limit`); cancel/list/shutdown
  remain available. Ingress engine backlog: 128 frames, then reader backpressure
  stops consuming the bounded OS input pipe.
* Per-port queued write bytes: 256 KiB including active write. Exceeding this
  returns `write_queue_limit` and disconnects that port, cancelling its writes.
* Cumulative protocol stdout: at most 8 MiB, with 1024 bytes reserved for the
  terminal `session_limit` event. Helper lifetime: 3600 seconds. Either limit
  releases all ports and exits 0. Pending responses are not guaranteed after
  shutdown, EOF, termination, or session-limit termination; the session-ending
  condition itself is authoritative.
* Stdout queued bytes: 256 KiB, including the writer's active frame. A separate
  writer never blocks the Asio loop; POSIX uses nonblocking writes/poll. Windows
  uses a cancellable synchronous pipe writer monitored by the Asio watchdog.
  Queue exhaustion, broken pipe or a write stalled over 2 seconds terminates
  with exit 3 and `output_failed` on stderr. There is no silent lossy mode.
  When stdout is unusable a terminal stdout event cannot be promised.
  A final 3-second join deadline guarantees exit even if Windows synchronous
  cancellation does not complete; ports have already been released.

EOF, shutdown, SIGINT/SIGTERM release all handles and stop; no detached processes
or orphan reader/writer threads remain. Independently of EOF, a 250-millisecond
Asio timer monitors the immediate native parent. POSIX captures `getppid()` and
stops when it changes through reparenting. Windows finds the parent using
Toolhelp32 and retains an `OpenProcess(SYNCHRONIZE)` handle; a signaled handle
stops the session. Watcher setup failure produces `parent_watch_failed` on
stderr and exit 3; a Windows wait failure emits an error event and exits 3.
The Windows process handle identifies the retained process rather than relying
on repeated PID lookup. Parent death therefore releases ports even if another
process retains the stdin pipe writer. Windows hard helper termination itself
relies on OS handle cleanup.
Never reuse this session automatically after an error/limit; explicitly start
a new helper.

## Dependencies and validation

Header-only standalone Asio 1.30.2 and nlohmann/json 3.11.3 are fetched by
immutable commit archive URL and SHA256 in CMakeLists.txt (no mutable `latest`).
They compile into the helper; no separately deployed library is needed.
Asio is Boost Software License 1.0, json is MIT; upstream COPYING/license texts
are retained in component Resources/licenses. Apple system libraries and the
Windows static CRT remain platform runtime dependencies/redistribution scope.

`python3 scripts/build_cli.py --root . --target macos-arm64` uses the existing
build/provenance gates and runs CTest (portable pipe and actual macOS PTY tests).
`python3 -m unittest discover -s tests -p test_cli.py -v` builds and tests argv.
CTest uses the existing native Windows-x64 CMake pattern; Windows serial hardware,
native Intel execution and full-duration lifetime still require their own evidence.
CTest runs the native harness unbuffered and verbose with a 120-second suite
timeout. The harness dumps Python thread stacks after 60 seconds, and pipe/PTY
test writes and POSIX test reads have individual deadlines. A shutdown-ingress
backpressure regression covers condition-variable wakeup and helper teardown.
