//%attributes = {"invisible":true}
#DECLARE($target : Text; $report : Object; $statusPath : Text)
var SERIAL_Session : cs.MiyakoSerial.SerialPort
var SERIAL_Report : Object
var SERIAL_Status : 4D.File
var SERIAL_Responses : Integer
var SERIAL_Returned : Boolean
var SERIAL_Ids : Collection
var $result : Object
var $frame : Text
var $oversized : Text
var $i : Integer
var $status : 4D.File
var $bytes : Blob
var HOST_StatusFile : 4D.File
var HOST_Phase; HOST_PreviousHandler : Text
$status:=File($statusPath; fk platform path)
HOST_StatusFile:=$status
HOST_Phase:="async start"
HOST_PreviousHandler:=Method called on error(ek local)
ON ERR CALL("Host_error"; ek local)
HOST_Phase:="async report"
SERIAL_Report:=$report
SERIAL_Status:=$status
SERIAL_Responses:=0
SERIAL_Returned:=False
SERIAL_Ids:=New collection
SERIAL_Report.async:=New object("events"; New collection; "responses"; New collection; "returnedBeforeCallbacks"; True)
SERIAL_Report.async.invalidCallback:=cs.MiyakoSerial.SerialPort.new($target; New object).start()
SERIAL_Status.setText(JSON Stringify(SERIAL_Report))
HOST_Phase:="async constructor"
SERIAL_Session:=cs.MiyakoSerial.SerialPort.new($target; Formula(Serial_event($1)))
HOST_Phase:="async launch"
$result:=SERIAL_Session.start()
SERIAL_Report.async.start:=$result
If (Not($result.ok))
	SERIAL_Report.success:=False
	SERIAL_Status.setText(JSON Stringify(SERIAL_Report))
	QUIT 4D
End if
SERIAL_Report.async.binaryFrames:=New collection
$oversized:=""
For ($i; 1; 22000)
	$oversized+="界"
End for
SERIAL_Report.async.oversized:=SERIAL_Session.open($oversized; New object)
$frame:="{\"protocol\":1,\"event\":\"data\",\"connection\":\"parser-fixture\",\"data\":\"AP+A\"}"+Char(10)
SERIAL_Session._stream.onData(SERIAL_Session._stream.worker; New object("data"; Substring($frame; 1; 11)))
SERIAL_Session._stream.onData(SERIAL_Session._stream.worker; New object("data"; Substring($frame; 12)+$frame))
$result:=SERIAL_Session.list()
SERIAL_Ids.push($result.id)
$result:=SERIAL_Session.open(""; New object("baudRate"; 9600))
SERIAL_Ids.push($result.id)
$result:=SERIAL_Session.close("not-a-connection")
SERIAL_Ids.push($result.id)
$result:=SERIAL_Session.cancel("not-a-request")
SERIAL_Ids.push($result.id)
BASE64 DECODE("AP+A"; $bytes)
$result:=SERIAL_Session.write("not-a-connection"; $bytes; 1000)
SERIAL_Ids.push($result.id)
SERIAL_Returned:=True
SERIAL_Report.async.submittedIds:=SERIAL_Ids
SERIAL_Status.setText(JSON Stringify(SERIAL_Report))
