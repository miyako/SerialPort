# Greeting

`cs.TemplateCLI.Greeting.new({target : Text})` creates the example feature API.
The namespace is configured independently of the repository/component name.
Within the component, the class is `cs.Greeting`.

`target` may be omitted on macOS (universal). On Windows, explicitly pass
`"Windows-x64"`. `"macOS-universal"` is also accepted on macOS. Other target
values produce `unsupported_architecture`; no processor-name heuristic is used.

| Method | Result |
| --- | --- |
| `greet(name : Text) : Object` | Versioned greeting JSON decoded into `result.value` |
| `version() : Object` | Versioned CLI version JSON decoded into `result.value` |

Both are synchronous, close stdin and enforce a 30-second process timeout.
Success is `{ok:true, stdout:Text, stderr:Text, exitCode:0, value:Object}`.
For `greet`, `value` has `{protocol:1,message:"Hello, NAME!"}`; for `version`,
`{protocol:1,version:"0.1.0"}`. Empty names fail with `cli_error`, exitCode 2
and JSON diagnostics on stderr. Name Unicode, spaces, quotes, backslashes,
newlines and shell metacharacters are literal data, never shell instructions.

Failures have `ok:false` and `error:{code,message{,details}}`. Process results
retain stdout/stderr/exitCode when available; prelaunch failures have no exitCode.
Codes: `missing_executable`, `unsupported_platform`, `unsupported_architecture`,
`invalid_argument`, `launch_failed`, `process_error`, `process_terminated`,
`cli_error`, `invalid_protocol`. Invalid JSON/required field/version is not success.
The protocol version must be numeric before comparison with version 1; Boolean
and Null versions are rejected without a 4D type-comparison error. Top-level
Boolean, Null, numeric and Collection payloads are rejected before field access.
Unexpected system errors are captured narrowly with diagnostic details.

```4d
var $tool : cs.TemplateCLI.Greeting
var $target : Text
$target:="macOS-universal"
If (Is Windows)
  $target:="Windows-x64"
End if
$tool:=cs.TemplateCLI.Greeting.new($target)
$result:=$tool.greet("4D")
If ($result.ok)
  ALERT($result.value.message)
Else
  ALERT($result.error.code+": "+$result.error.message)
End if
```

Replace this class, example CLI and host assertions with your requested feature.
`_decode` and `_cli` are implementation details; underscore names are not an
access-control boundary in 4D.
