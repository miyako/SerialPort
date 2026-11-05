Function quote($argument : Text)->$quoted : Text
	var $i; $slashes; $j : Integer
	var $character : Text
	$quoted:="\""
	$slashes:=0
	For ($i; 1; Length($argument))
		$character:=Substring($argument; $i; 1)
		If (Is Windows)
			Case of
				: ($character="\\")
					$slashes+=1
				: ($character="\"")
					For ($j; 1; (2*$slashes)+1)
						$quoted+="\\"
					End for
					$quoted+="\""
					$slashes:=0
				Else
					For ($j; 1; $slashes)
						$quoted+="\\"
					End for
					$quoted+=$character
					$slashes:=0
			End case
		Else
			If (($character="\\") | ($character="\""))
				$quoted+="\\"
			End if
			$quoted+=$character
		End if
	End for
	For ($j; 1; 2*$slashes)
		$quoted+="\\"
	End for
	$quoted+="\""

Function execute($file : 4D.File; $arguments : Collection)->$result : Object
	var $command; $argument : Text
	var $value : Variant
	var $options : Object
	var $previous : Text
	var $launchErrors : Collection
	var CLI_LastErrors : Collection
	var $worker : 4D.SystemWorker
	If (($file=Null) | ($arguments=Null))
		return New object("ok"; False; "error"; New object("code"; "invalid_argument"; "message"; "Executable File and arguments Collection are required"))
	End if
	$command:=This.quote($file.path)
	For each ($value; $arguments)
		If ((Value type($value)#Is text) & (Value type($value)#Is object))
			return New object("ok"; False; "error"; New object("code"; "invalid_argument"; "message"; "Arguments must be Text, File or Folder"))
		End if
		Case of
			: (Value type($value)=Is text)
				$argument:=$value
			: (OB Instance of($value; 4D.File))
				$argument:=File($value.platformPath; fk platform path).path
				If (Is Windows)
					$argument:=$value.platformPath
				End if
			: (OB Instance of($value; 4D.Folder))
				$argument:=Folder($value.platformPath; fk platform path).path
				If (Is Windows)
					$argument:=$value.platformPath
				End if
			Else
				return New object("ok"; False; "error"; New object("code"; "invalid_argument"; "message"; "Arguments must be Text, File or Folder"))
		End case
		If (Position(Char(0); $argument; *)>0)
			return New object("ok"; False; "error"; New object("code"; "invalid_argument"; "message"; "NUL is not permitted in process arguments"))
		End if
		$command+=" "+This.quote($argument)
	End for each
	$options:=New object("dataType"; "text"; "encoding"; "UTF-8"; "hideWindow"; True; "timeout"; 30; "currentDirectory"; $file.parent)
	$previous:=Method called on error(ek local)
	CLI_LastErrors:=Null
	ON ERR CALL("_CaptureError"; ek local)
	$worker:=4D.SystemWorker.new($command; $options)
	ON ERR CALL($previous; ek local)
	$launchErrors:=CLI_LastErrors
	CLI_LastErrors:=Null
	If ($worker=Null)
		return New object("ok"; False; "error"; New object("code"; "launch_failed"; "message"; "4D could not create the system worker"; "details"; $launchErrors))
	End if
	$worker.closeInput()
	$worker.wait()
	$result:=New object("ok"; False; "stdout"; $worker.response; "stderr"; $worker.responseError; "exitCode"; $worker.exitCode)
	Case of
		: ($worker.errors#Null)
			$result.error:=New object("code"; "process_error"; "message"; "System worker execution failed"; "details"; $worker.errors)
		: ($worker.exitCode=Null)
			$result.error:=New object("code"; "process_terminated"; "message"; "Process did not exit normally (including timeout)")
		: ($worker.exitCode#0)
			$result.error:=New object("code"; "cli_error"; "message"; $worker.responseError)
		Else
			$result.ok:=True
	End case
