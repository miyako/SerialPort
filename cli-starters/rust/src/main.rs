use std::fmt::Write;

const INVALID_ARGUMENTS: &str = r#"{"protocol":1,"error":{"code":"invalid_arguments","message":"expected greet --name <non-empty UTF-8 name> or --version"}}"#;

fn quote(value: &str) -> String {
    let mut result = String::from("\"");
    for character in value.chars() {
        match character {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            '\u{08}' => result.push_str("\\b"),
            '\u{0c}' => result.push_str("\\f"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            character if character < '\u{20}' => {
                write!(result, "\\u{:04x}", character as u32).unwrap();
            }
            character => result.push(character),
        }
    }
    result.push('"');
    result
}

fn main() {
    let args: Result<Vec<String>, _> = std::env::args_os()
        .skip(1)
        .map(|arg| arg.into_string())
        .collect();
    if let Ok(args) = args {
        if args.len() == 1 && args[0] == "--version" {
            println!(r#"{{"protocol":1,"version":"0.1.0"}}"#);
            return;
        }
        if args.len() == 3 && args[0] == "greet" && args[1] == "--name" && !args[2].is_empty() {
            println!(
                "{{\"protocol\":1,\"message\":{}}}",
                quote(&format!("Hello, {}!", args[2]))
            );
            return;
        }
    }
    eprintln!("{INVALID_ARGUMENTS}");
    std::process::exit(2);
}
