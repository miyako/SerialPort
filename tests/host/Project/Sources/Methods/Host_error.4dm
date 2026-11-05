//%attributes = {"invisible":true}
var HOST_StatusFile : 4D.File
var HOST_Phase : Text
var HOST_PreviousHandler : Text
var $errors : Collection
$errors:=Last errors
ON ERR CALL(HOST_PreviousHandler; ek local)
HOST_StatusFile.setText(JSON Stringify(New object("success"; False; "finished"; False; "phase"; HOST_Phase; "errors"; $errors)))
QUIT 4D
