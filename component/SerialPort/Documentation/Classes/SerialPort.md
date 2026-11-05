# SerialPort

`cs.Serial.SerialPort.new(target; callback)` creates an owner-scoped
session. **Construct, retain and call it inside a live `CALL WORKER` worker.**
The constructor does not launch anything; `start()` launches one bundled
`serial-port serve` process. The worker must return to its event loop to deliver
SystemWorker callbacks. Do not call `.wait()` in the normal communication path.
Public 4D preemptive-process compatibility is not promised. Native serial I/O
runs asynchronously in standalone Asio, not in the 4D process.

`target` is `"macOS-universal"` on macOS or explicitly `"Windows-x64"` on
Windows. `callback` is a `Formula` (`4D.Function` in the tested 20.7 runtime)
receiving one Object. It runs in the owner
worker; forward UI notifications with `CALL FORM` or `CALL WORKER(1; ...)`.
Do not capture transient pointers. Standard objects are copied across workers;
do not send a live session object to another process. Retain it as a process
variable in its owner and dispatch commands to that worker.

| Method | Immediate return / behavior |
| --- | --- |
| `start()` | `{ok:true}` or explicit launch/configuration error |
| `list()` | Queues port enumeration |
| `open(path : Text; config : Object)` | Queues validated open; reply contains stable connection ID |
| `configure(connection : Text; config : Object)` | Queues serial configuration |
| `write(connection : Text; bytes : Blob; timeoutMs : Integer)` | Queues a full serialized write, maximum 32768 bytes |
| `cancel(requestId : Text)` | Cancels the indicated pending write |
| `close(connection : Text)` | Releases that connection |
| `shutdown()` | Queues shutdown, closes stdin, rejects new requests |
| `terminate()` | Emergency process termination; pending requests fail |
| `enumerate()` / `version()` | Synchronous diagnostics only, separate short-lived process |

Queued methods return `{ok:true,id:Text}` meaning **accepted for submission**,
not completed I/O. Immediate failures are `{ok:false,error:{code,message}}`.
The callback gets correlated protocol responses
`{protocol:1,id,ok:true,result:Object}` or
`{protocol:1,id,ok:false,error:{code,message,...}}`.
Timeouts/cancellation close the affected connection and cancel all its queued
writes. Error `transferred` counts may indicate partially written bytes;
never blindly retry.
See the helper protocol in the repository README for precise semantics.

Configuration uses `baudRate` (integer 1..4000000, default 9600),
`dataBits` (5..8, default 8),
`stopBits` (`"one"`, `"onePointFive"`, `"two"`), `parity`
(`"none"`, `"odd"`, `"even"`), `flowControl` (`"none"`, `"software"`,
`"hardware"`). The OS may reject combinations; those errors are not success. Configuration
is not transactional: **any configure failure, including validation failure,
closes the connection**. Per-port write queue overflow also closes that
connection and cancels its pending writes.

Async `data` events include `connection`, base64 `data`, and decoded `bytes`
as a **4D.Blob**. Assign to a scalar Blob when mutable bytes are needed.
Base64 is strictly validated and round-tripped before decoding.
Callbacks contain byte chunks, **not device messages**. Assemble device frames
yourself. Binary bytes including NUL are preserved. This prototype deliberately
does not supply streaming text decoders: keep encoding/decoder state separately
per connection, because a multibyte character may straddle chunks. No global
`USE CHARACTER SET` is used.

Other events include `error`, `disconnected`, `session_limit`, `diagnostic`
(stderr), and local `terminated` with available `exitCode`, `errors`, and
`expected` (shutdown was requested). All pending IDs are failed on termination.
Unplug/driver failures have explicit error/disconnection; no automatic reconnect.
Shutdown breaks session/transport references. Wait for `terminated` before
clearing your owner process variable and `KILL WORKER`-ing the owner; do not kill
it while callbacks or requests remain queued.

Frames and requests are bounded (65536-byte frame including LF,
measured as UTF-8 bytes, 128 pending requests). SystemWorker retains
accumulated response internally, so the helper caps output at 8 MiB and lifetime
at one hour; a session-limit event requires deliberate replacement, not silent
reopening. 4D also enforces the output budget and frame limit. Keep callbacks
short; the helper's 256 KiB output queue fails with `output_failed`/exit 3
on overflow, broken pipe or a stall exceeding two seconds. Those fatal
diagnostics use stderr when protocol stdout is unavailable.

Hardware belongs to the machine executing the helper. A component running in
4D Server accesses **server-local** ports, not a remote client's USB devices.
Bluetooth Classic works only through an already exposed OS serial port. BLE,
pairing/discovery, direct Bluetooth, specialized RS485 direction control, and
real-time timing guarantees are outside this prototype.
