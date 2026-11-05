# _LineProcess (internal convention)

Generic owner-worker streaming transport for a bundled external executable.
Uses `_Process.quote()` and `4D.SystemWorker.new(commandLineText; options)`;
no shell, plug-in, PATH fallback or polling. Options are callback class methods
as documented by 4D: `onData`, `onDataError`, `onError`, `onTerminate`.

Reassembles arbitrary Text chunks into LF-delimited lines before invoking the
receiver's `_line` method. Protocol stdout is ASCII JSON with UTF-8 represented
by JSON escapes; serial bytes are base64. Retains only a partial frame locally,
bounds frame/output size, captures narrow launch/postMessage errors and forwards
stderr separately. A one-hour process timeout is a final safeguard; the helper
owns its earlier graceful session limit. It never blocks on `.wait()`.

The receiver implements `_line`, `_diagnostic`, `_transportError`, `_terminated`.
Termination clears accumulated partial data and both directions of object
references. `_CLI.resolve()` supplies the checked bundled physical File; the
synchronous `_Process.execute()` remains available for short diagnostics and
generic argument tests.
