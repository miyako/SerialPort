//%attributes = {"invisible":true}
#DECLARE($event : Object)
var SERIAL_Session : cs.MiyakoSerial.SerialPort
var SERIAL_Report : Object
var SERIAL_Status : 4D.File
var SERIAL_Responses : Integer
var SERIAL_Returned : Boolean
var SERIAL_Ids : Collection
var $result : Object
var $encoded : Text
var $bytes : Blob
If ($event.event="data")
	$bytes:=$event.bytes
	BASE64 ENCODE($bytes; $encoded)
	$encoded:=Replace string(Replace string($encoded; Char(13); ""); Char(10); "")
	SERIAL_Report.async.binaryFrames.push($encoded)
	return
End if
If (Not(SERIAL_Returned))
	SERIAL_Report.async.returnedBeforeCallbacks:=False
	SERIAL_Report.success:=False
End if
If (Value type($event.id)=Is text)
	SERIAL_Report.async.responses.push($event)
	SERIAL_Responses+=1
	If (SERIAL_Responses<=5)
		If (SERIAL_Ids.indexOf($event.id)<0)
			SERIAL_Report.success:=False
		End if
		If ($event.id=SERIAL_Ids[0])
			If (Not($event.ok))
				SERIAL_Report.success:=False
			End if
		Else
			If ($event.ok)
				SERIAL_Report.success:=False
			End if
		End if
		If ($event.id=SERIAL_Ids[4])
			SERIAL_Report.async.writeError:=$event.error.code
		End if
	End if
	If (SERIAL_Responses=5)
		$result:=SERIAL_Session.shutdown()
		SERIAL_Report.async.shutdown:=$result
	End if
Else
	SERIAL_Report.async.events.push($event)
	If ($event.event="terminated")
		SERIAL_Report.async.exitCode:=$event.exitCode
		SERIAL_Report.async.responseCount:=SERIAL_Responses
		If (($event.exitCode#0) | Not($event.expected) | (SERIAL_Responses#6))
			SERIAL_Report.success:=False
		End if
		SERIAL_Report.finished:=True
		SERIAL_Status.setText(JSON Stringify(SERIAL_Report))
		SERIAL_Session:=Null
		QUIT 4D
	Else
		If ($event.event="error")
			SERIAL_Report.success:=False
		End if
	End if
End if
