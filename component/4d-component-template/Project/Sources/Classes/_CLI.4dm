property executableName : Text
property target : Text

Class constructor($executableName : Text; $target : Text)
	This.executableName:=$executableName
	This.target:=$target

Function get compiled()->$compiled : Boolean
	$compiled:=Is compiled mode

Function execute($arguments : Collection)->$result : Object
	var $platform; $name : Text
	var $file : 4D.File
	If (Not(Match regex("^[a-z][a-z0-9-]{0,62}$"; This.executableName)))
		return New object("ok"; False; "error"; New object("code"; "invalid_argument"; "message"; "Executable name must be a CLI basename, not a path"))
	End if
	If ($arguments=Null)
		return New object("ok"; False; "error"; New object("code"; "invalid_argument"; "message"; "Arguments collection is required"))
	End if
	$name:=This.executableName
	Case of
		: (Is macOS)
			If ((This.target#"") & (This.target#"macOS-universal"))
				return New object("ok"; False; "error"; New object("code"; "unsupported_architecture"; "message"; "macOS requires macOS-universal"))
			End if
			$platform:="macOS"
		: (Is Windows)
			If (This.target#"Windows-x64")
				return New object("ok"; False; "error"; New object("code"; "unsupported_architecture"; "message"; "Explicit Windows-x64 selection is required; native Windows ARM64 is not supported"))
			End if
			$platform:="Windows"
			$name+=".exe"
		Else
			return New object("ok"; False; "error"; New object("code"; "unsupported_platform"; "message"; "Only macOS and Windows are supported"))
	End case
	$file:=Folder("/RESOURCES").folder("bin").folder($platform).file($name)
	If (Not($file.exists))
		return New object("ok"; False; "error"; New object("code"; "missing_executable"; "message"; $file.path))
	End if
	$file:=File($file.platformPath; fk platform path)
	$result:=cs._Process.new().execute($file; $arguments)
