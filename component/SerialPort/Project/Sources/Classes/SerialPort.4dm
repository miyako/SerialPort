property _cli : cs._CLI
property _stream : cs._LineProcess
property _callback : Object
property _pending : Object
property _sequence : Integer
property _owner : Integer
property running : Boolean
property closing : Boolean

Class constructor($target : Text; $callback : Object)
	This._cli:=cs._CLI.new("serial-port"; $target)
	This._callback:=$callback
	This._pending:=New object
	This._sequence:=0
	This._owner:=Current process
	This.running:=False
	This.closing:=False

Function start()->$result : Object
	If (Current process#This._owner)
		return This._failure("wrong_owner"; "Use the original CALL WORKER owner")
	End if
	If (This.running | This.closing)
		return This._failure("already_started"; "Session already started or closing")
	End if
	If (This._callback=Null)
		return This._failure("invalid_argument"; "A callback Formula is required")
	End if
	If (Not(OB Instance of(This._callback; 4D.Function)))
		return This._failure("invalid_argument"; "A callback Formula is required")
	End if
	$result:=This._cli.resolve()
	If (Not($result.ok))
		return $result
	End if
	This._stream:=cs._LineProcess.new($result.file; This)
	$result:=This._stream.start($result.file)
	This.running:=$result.ok

Function list()->$result : Object
	$result:=This._request("list"; New object)

Function open($path : Text; $config : Object)->$result : Object
	$result:=This._request("open"; New object("path"; $path; "config"; $config))

Function configure($connection : Text; $config : Object)->$result : Object
	$result:=This._request("configure"; New object("connection"; $connection; "config"; $config))

Function close($connection : Text)->$result : Object
	$result:=This._request("close"; New object("connection"; $connection))

Function write($connection : Text; $bytes : Blob; $timeoutMs : Integer)->$result : Object
	var $encoded : Text
	If (BLOB size($bytes)>32768)
		return This._failure("write_overflow"; "Write exceeds 32768 bytes")
	End if
	BASE64 ENCODE($bytes; $encoded)
	$encoded:=Replace string(Replace string($encoded; Char(13); ""); Char(10); "")
	$result:=This._request("write"; New object("connection"; $connection; "data"; $encoded; "timeoutMs"; $timeoutMs))

Function cancel($requestId : Text)->$result : Object
	$result:=This._request("cancel"; New object("requestId"; $requestId))

Function shutdown()->$result : Object
	$result:=This._request("shutdown"; New object)
	If ($result.ok)
		This.closing:=True
		This._stream.worker.closeInput()
	End if

Function terminate()->$result : Object
	If (Current process#This._owner)
		return This._failure("wrong_owner"; "Use the original CALL WORKER owner")
	End if
	If (This._stream=Null)
		return This._failure("session_closed"; "Session is not running")
	End if
	This._stream.abort("cancelled"; "Session forcibly terminated"; Null)
	$result:=New object("ok"; True)

Function _failure($code : Text; $message : Text)->$result : Object
	$result:=New object("ok"; False; "error"; New object("code"; $code; "message"; $message))

Function _request($op : Text; $request : Object)->$result : Object
	var $frame; $id : Text
	var $utf8 : Blob
	If (Current process#This._owner)
		return This._failure("wrong_owner"; "Use the original CALL WORKER owner")
	End if
	If (Not(This.running) | This.closing)
		return This._failure("session_closed"; "Session is not accepting requests")
	End if
	If (OB Keys(This._pending).length>=128)
		return This._failure("request_overflow"; "128 requests are already pending")
	End if
	This._sequence+=1
	$id:=String(This._sequence)
	$request.protocol:=1
	$request.id:=$id
	$request.op:=$op
	$frame:=JSON Stringify($request)
	CONVERT FROM TEXT($frame; "UTF-8"; $utf8)
	If (BLOB size($utf8)>65535)
		return This._failure("frame_overflow"; "Request exceeds safe frame limit")
	End if
	This._pending[$id]:=$op
	$result:=This._stream.send($frame)
	If ($result.ok)
		$result.id:=$id
	Else
		OB REMOVE(This._pending; $id)
	End if

Function _line($line : Text)
	var $payload : Variant
	var $previous; $id; $encoded; $op : Text
	var $errors : Collection
	var $bytes : Blob
	var CLI_LastErrors : Collection
	$previous:=Method called on error(ek local)
	CLI_LastErrors:=Null
	ON ERR CALL("_CaptureError"; ek local)
	$payload:=JSON Parse($line)
	ON ERR CALL($previous; ek local)
	$errors:=CLI_LastErrors
	CLI_LastErrors:=Null
	If (($errors#Null) | (Value type($payload)#Is object))
		This._stream.abort("invalid_protocol"; "Expected a JSON object"; $errors)
		return
	End if
	If ((Value type($payload.protocol)#Is real) & (Value type($payload.protocol)#Is longint))
		This._stream.abort("invalid_protocol"; "Expected numeric protocol"; Null)
		return
	End if
	If ($payload.protocol#1)
		This._stream.abort("invalid_protocol"; "Unsupported protocol"; Null)
		return
	End if
	If (Value type($payload.id)=Is text)
		$id:=$payload.id
		If (Value type(This._pending[$id])#Is text)
			This._stream.abort("invalid_protocol"; "Unknown or duplicate response ID"; Null)
			return
		End if
		If (Value type($payload.ok)#Is boolean)
			This._stream.abort("invalid_protocol"; "Response needs Boolean ok"; Null)
			return
		End if
		If (Value type($payload.event)#Is undefined)
			This._stream.abort("invalid_protocol"; "Frame cannot be both event and reply"; Null)
			return
		End if
		$op:=This._pending[$id]
		If ($payload.ok)
			If (Value type($payload.result)#Is object)
				This._stream.abort("invalid_protocol"; "Response needs result Object"; Null)
				return
			End if
			If ($op="list")
				If (Value type($payload.result.ports)#Is collection)
					This._stream.abort("invalid_protocol"; "List response needs ports Collection"; Null)
					return
				End if
			End if
			If ($op="open")
				If (Value type($payload.result.connection)#Is text)
					This._stream.abort("invalid_protocol"; "Open response needs connection Text"; Null)
					return
				End if
			End if
		Else
			If (Value type($payload.error)#Is object)
				This._stream.abort("invalid_protocol"; "Response needs error Object"; Null)
				return
			End if
			If ((Value type($payload.error.code)#Is text) | (Value type($payload.error.message)#Is text))
				This._stream.abort("invalid_protocol"; "Error needs code and message Text"; Null)
				return
			End if
		End if
		OB REMOVE(This._pending; $id)
	Else
		If (Value type($payload.event)#Is text)
			This._stream.abort("invalid_protocol"; "Expected event or response ID"; Null)
			return
		End if
		If (New collection("data"; "error"; "disconnected"; "session_limit").indexOf($payload.event)<0)
			This._stream.abort("invalid_protocol"; "Unknown helper event"; Null)
			return
		End if
		If (($payload.event="error") | ($payload.event="disconnected"))
			If (Value type($payload.connection)#Is text)
				This._stream.abort("invalid_protocol"; "Connection event needs connection Text"; Null)
				return
			End if
		End if
		If ($payload.event="error")
			If (Value type($payload.error)#Is object)
				This._stream.abort("invalid_protocol"; "Error event needs error Object"; Null)
				return
			End if
		End if
		If ($payload.event="data")
			If ((Value type($payload.connection)#Is text) | (Value type($payload.data)#Is text))
				This._stream.abort("invalid_protocol"; "Data event needs connection and base64"; Null)
				return
			End if
			If (Not(Match regex("^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"; $payload.data)))
				This._stream.abort("invalid_protocol"; "Invalid base64"; Null)
				return
			End if
			BASE64 DECODE($payload.data; $bytes)
			BASE64 ENCODE($bytes; $encoded)
			$encoded:=Replace string(Replace string($encoded; Char(13); ""); Char(10); "")
			If ($encoded#$payload.data)
				This._stream.abort("invalid_protocol"; "Noncanonical base64"; Null)
				return
			End if
			$payload.bytes:=4D.Blob.new($bytes)
		End if
	End if
	This._emit($payload)

Function _emit($event : Object)
	If (This._callback#Null)
		This._callback.call(Null; $event)
	End if

Function _diagnostic($text : Text)
	This._emit(New object("event"; "diagnostic"; "message"; $text))

Function _transportError($code : Text; $message : Text; $details : Collection)
	This.running:=False
	This._emit(New object("event"; "error"; "error"; New object("code"; $code; "message"; $message; "details"; $details)))

Function _terminated($exitCode : Variant; $errors : Collection)
	var $id : Text
	This.running:=False
	If (Not(This.closing) | ($errors#Null) | ($exitCode=Null))
		This._transportError("process_terminated"; "Helper stopped unexpectedly; no automatic reconnect"; $errors)
	Else
		If ($exitCode#0)
			This._transportError("process_terminated"; "Helper exited with a failure status"; $errors)
		End if
	End if
	For each ($id; OB Keys(This._pending))
		This._emit(New object("protocol"; 1; "id"; $id; "ok"; False; "error"; New object("code"; "session_closed"; "message"; "Helper terminated before request completion")))
	End for each
	This._pending:=New object
	This._stream:=Null
	This._emit(New object("event"; "terminated"; "exitCode"; $exitCode; "errors"; $errors; "expected"; This.closing))
	This._callback:=Null

Function version()->$result : Object
	$result:=This._decode(This._cli.execute(New collection("--version")); "version")

Function enumerate()->$result : Object
	$result:=This._decode(This._cli.execute(New collection("list")); "ports")

Function _decode($result : Object; $field : Text)->$decoded : Object
	var $payload : Variant
	var $previous : Text
	var $parseErrors : Collection
	var CLI_LastErrors : Collection
	If (Not($result.ok))
		return $result
	End if
	$previous:=Method called on error(ek local)
	CLI_LastErrors:=Null
	ON ERR CALL("_CaptureError"; ek local)
	$payload:=JSON Parse($result.stdout)
	ON ERR CALL($previous; ek local)
	$parseErrors:=CLI_LastErrors
	CLI_LastErrors:=Null
	If (($parseErrors#Null) | (Value type($payload)#Is object))
		$result.ok:=False
		$result.error:=New object("code"; "invalid_protocol"; "message"; "CLI response must be a JSON object"; "details"; $parseErrors)
		return $result
	End if
	If ((Value type($payload.protocol)#Is real) & (Value type($payload.protocol)#Is longint))
		$result.ok:=False
		$result.error:=New object("code"; "invalid_protocol"; "message"; "Unsupported CLI protocol version")
		return $result
	End if
	If ($payload.protocol#1)
		$result.ok:=False
		$result.error:=New object("code"; "invalid_protocol"; "message"; "Unsupported CLI protocol version")
		return $result
	End if
	If ($field="ports")
		If (Value type($payload.ports)#Is collection)
			$result.ok:=False
			$result.error:=New object("code"; "invalid_protocol"; "message"; "CLI ports must be a Collection")
			return $result
		End if
	Else
		If (Value type($payload[$field])#Is text)
			$result.ok:=False
			$result.error:=New object("code"; "invalid_protocol"; "message"; "Unsupported CLI protocol version")
			return $result
		End if
	End if
	$result.value:=$payload
	$decoded:=$result
