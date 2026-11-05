package main

import (
	"fmt"
	"os"
	"strings"
	"unicode/utf8"
)

const invalidArguments = `{"protocol":1,"error":{"code":"invalid_arguments","message":"expected greet --name <non-empty UTF-8 name> or --version"}}`

func quote(value string) string {
	var result strings.Builder
	result.WriteByte('"')
	for _, char := range value {
		switch char {
		case '"', '\\':
			result.WriteByte('\\')
			result.WriteRune(char)
		case '\b':
			result.WriteString(`\b`)
		case '\f':
			result.WriteString(`\f`)
		case '\n':
			result.WriteString(`\n`)
		case '\r':
			result.WriteString(`\r`)
		case '\t':
			result.WriteString(`\t`)
		default:
			if char < 0x20 {
				fmt.Fprintf(&result, `\u%04x`, char)
			} else {
				result.WriteRune(char)
			}
		}
	}
	result.WriteByte('"')
	return result.String()
}

func main() {
	args := os.Args[1:]
	for _, arg := range args {
		if !utf8.ValidString(arg) {
			fmt.Fprintln(os.Stderr, invalidArguments)
			os.Exit(2)
		}
	}
	if len(args) == 1 && args[0] == "--version" {
		fmt.Println(`{"protocol":1,"version":"0.1.0"}`)
		return
	}
	if len(args) == 3 && args[0] == "greet" && args[1] == "--name" && args[2] != "" {
		fmt.Println(`{"protocol":1,"message":` + quote("Hello, "+args[2]+"!") + `}`)
		return
	}
	fmt.Fprintln(os.Stderr, invalidArguments)
	os.Exit(2)
}
