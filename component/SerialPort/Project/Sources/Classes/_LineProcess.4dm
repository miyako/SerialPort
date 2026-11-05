property worker : 4D.SystemWorker
property receiver : Object
property buffer : Text
property received : Integer
property dataType : Text
property encoding : Text
property hideWindow : Boolean
property timeout : Integer
property currentDirectory : 4D.Folder
property failed : Boolean

Class constructor($file : 4D.File; $receiver : Object)
	This.receiver:=$receiver
	This.buffer:=""
	This.received:=0
	This.failed:=False
	This.dataType:="text"
	This.encoding:="UTF-8"
	This.hideWindow:=True
	This.timeout:=3605
	This.currentDirectory:=$file.parent

Function start($file : 4D.File)->$result : Object
	var $previous : Text
	var $errors : Collection
	var CLI_LastErrors : Collection
	$previous:=Method called on error(ek local)
	CLI_LastErrors:=Null
	ON ERR CALL("_CaptureError"; ek local)
	This.worker:=4D.SystemWorker.new(cs._Process.new().quote($file.path)+" serve"; This)
	ON ERR CALL($previous; ek local)
	$errors:=CLI_LastErrors
	CLI_LastErrors:=Null
	If (This.worker=Null)
		This.receiver:=Null
		return New object("ok"; False; "error"; New object("code"; "launch_failed"; "message"; "Could not launch streaming helper"; "details"; $errors))
	End if
	$result:=New object("ok"; True)

Function send($frame : Text)->$result : Object
	var $previous : Text
	var $errors : Collection
	var CLI_LastErrors : Collection
	If ((This.worker=Null) | This.failed)
		return New object("ok"; False; "error"; New object("code"; "session_closed"; "message"; "Streaming process is not running"))
	End if
	If (This.worker.terminated)
		return New object("ok"; False; "error"; New object("code"; "session_closed"; "message"; "Streaming process has terminated"))
	End if
	$previous:=Method called on error(ek local)
	CLI_LastErrors:=Null
	ON ERR CALL("_CaptureError"; ek local)
	This.worker.postMessage($frame+Char(10))
	ON ERR CALL($previous; ek local)
	$errors:=CLI_LastErrors
	CLI_LastErrors:=Null
	If ($errors#Null)
		This.abort("ipc_write"; "Could not write helper stdin"; $errors)
		return New object("ok"; False; "error"; New object("code"; "ipc_write"; "message"; "Could not write helper stdin"; "details"; $errors))
	End if
	$result:=New object("ok"; True)

Function onData($worker : Object; $info : Object)
	var $position : Integer
	var $line : Text
	var $utf8 : Blob
	If (This.failed)
		return
	End if
	If (Value type($info.data)#Is text)
		This.abort("invalid_protocol"; "Expected Text stream"; Null)
		return
	End if
	CONVERT FROM TEXT($info.data; "UTF-8"; $utf8)
	This.received+=BLOB size($utf8)
	If (This.received>8388608)
		This.abort("session_limit"; "Session output budget reached; create a new session explicitly"; Null)
		return
	End if
	This.buffer+=$info.data
	$position:=Position(Char(10); This.buffer; *)
	While ($position>0)
		If ($position>65536)
			This.abort("frame_overflow"; "Helper frame exceeds 65536 characters"; Null)
			return
		End if
		$line:=Substring(This.buffer; 1; $position-1)
		CONVERT FROM TEXT($line; "UTF-8"; $utf8)
		If (BLOB size($utf8)>65535)
			This.abort("frame_overflow"; "Helper frame exceeds byte limit"; Null)
			return
		End if
		This.buffer:=Substring(This.buffer; $position+1)
		This.receiver._line($line)
		If (This.failed)
			return
		End if
		$position:=Position(Char(10); This.buffer; *)
	End while
	CONVERT FROM TEXT(This.buffer; "UTF-8"; $utf8)
	If (BLOB size($utf8)>65535)
		This.abort("frame_overflow"; "Unterminated helper frame exceeds limit"; Null)
	End if

Function onDataError($worker : Object; $info : Object)
	If (This.receiver#Null)
		This.receiver._diagnostic($info.data)
	End if

Function onResponse($worker : Object; $info : Object)
	return

Function onError($worker : Object; $info : Object)
	This.abort("process_error"; "SystemWorker execution failed"; $worker.errors)

Function abort($code : Text; $message : Text; $details : Collection)
	If (Not(This.failed))
		This.failed:=True
		If (This.worker#Null)
			This.worker.terminate()
		End if
		If (This.receiver#Null)
			This.receiver._transportError($code; $message; $details)
		End if
	End if

Function onTerminate($worker : Object; $info : Object)
	var $receiver : Object
	$receiver:=This.receiver
	This.receiver:=Null
	If ($receiver#Null)
		If ((Length(This.buffer)>0) & Not(This.failed))
			$receiver._transportError("invalid_protocol"; "Helper ended inside a frame"; Null)
		End if
		$receiver._terminated($worker.exitCode; $worker.errors)
	End if
	This.buffer:=""
	This.worker:=Null
