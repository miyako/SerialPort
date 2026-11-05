#DECLARE($command : Text; $path : Text; $data : Blob)
var SERIAL_Demo : cs.Serial.SerialPort
var SERIAL_Connection : Text
var SERIAL_Log : Collection
var SERIAL_LogFile : 4D.File
var $result : Object
var $target : Text
Case of
	: ($command="start")
		If (SERIAL_Demo#Null)
			CALL WORKER(1; Formula(ALERT($1)); "Serial demo is already running")
			return
		End if
		SERIAL_Log:=New collection
		SERIAL_LogFile:=Folder(fk temporary folder).file("SerialPort-demo.json")
		$target:="macOS-universal"
		If (Is Windows)
			$target:="Windows-x64"
		End if
		SERIAL_Demo:=cs.Serial.SerialPort.new($target; Formula(Serial_demo_event($1)))
		$result:=SERIAL_Demo.start()
		If ($result.ok)
			$result:=SERIAL_Demo.open($path; New object("baudRate"; 9600; "dataBits"; 8; "stopBits"; "one"; "parity"; "none"; "flowControl"; "none"))
		End if
	: ($command="write")
		If (SERIAL_Connection="")
			CALL WORKER(1; Formula(ALERT($1)); "No open serial connection")
			return
		End if
		$result:=SERIAL_Demo.write(SERIAL_Connection; $data; 1000)
	: ($command="shutdown")
		If (SERIAL_Demo=Null)
			CALL WORKER(1; Formula(ALERT($1)); "Serial demo is not running")
			return
		End if
		$result:=SERIAL_Demo.shutdown()
	Else
		CALL WORKER(1; Formula(ALERT($1)); "Unknown serial demo command")
		return
End case
If (Not($result.ok))
	SERIAL_Log.push($result)
	SERIAL_LogFile.setText(JSON Stringify(SERIAL_Log))
	CALL WORKER(1; Formula(ALERT($1)); JSON Stringify($result.error))
	If (Not(SERIAL_Demo.running))
		SERIAL_Demo:=Null
	End if
End if
