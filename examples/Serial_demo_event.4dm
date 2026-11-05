#DECLARE($event : Object)
var SERIAL_Demo : cs.Serial.SerialPort
var SERIAL_Connection : Text
var SERIAL_Log : Collection
var SERIAL_LogFile : 4D.File
var $entry : Object
$entry:=OB Copy($event)
OB REMOVE($entry; "bytes")
SERIAL_Log.push($entry)
If (SERIAL_Log.length>256)
	SERIAL_Log.shift()
End if
SERIAL_LogFile.setText(JSON Stringify(SERIAL_Log))
If (Value type($event.result)=Is object)
	If (Value type($event.result.connection)=Is text)
		SERIAL_Connection:=$event.result.connection
	End if
End if
If (Value type($event.error)=Is object)
	CALL WORKER(1; Formula(ALERT($1)); JSON Stringify($event.error))
End if
If ($event.event="disconnected")
	SERIAL_Connection:=""
End if
If ($event.event="terminated")
	SERIAL_Demo:=Null
	SERIAL_Connection:=""
	KILL WORKER
End if
