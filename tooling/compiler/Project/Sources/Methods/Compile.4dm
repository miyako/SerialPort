//%attributes = {"invisible":true}
var $ignored : Integer
var $parameter : Text
var $request; $status; $options : Object
var $project; $output : 4D.File
$ignored:=Get database parameter(User param value; $parameter)
$request:=JSON Parse(File($parameter; fk posix path).getText())
$project:=File($request.project; fk posix path)
$output:=File($request.status; fk posix path)
$options:=New object("targets"; New collection("x86_64_generic"; "arm64_macOS_lib"); "generateSymbols"; False; "generateSyntaxFile"; True)
$status:=Compile project($project; $options)
$status.project:=$project.path
$status.targets:=$options.targets
$output.setText(JSON Stringify($status))
QUIT 4D
