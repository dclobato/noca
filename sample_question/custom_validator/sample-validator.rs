use std::env;
use std::io::{self, BufRead, Write};
use std::process::exit;

const AC: i32 = 0;
const WA: i32 = 1;
const TLE: i32 = 2;
const RE: i32 = 3;
#[allow(dead_code)]
const PE: i32 = 4;

fn finish(exitcode: i32, message: Option<String>) -> ! {
    if let Some(message) = message {
        eprintln!("{}", message);
        let _ = io::stderr().flush();
    }
    exit(exitcode);
}

fn read_line(stdin: &mut io::StdinLock) -> Option<String> {
    let mut line = String::new();
    match stdin.read_line(&mut line) {
        Ok(0) | Err(_) => None,
        Ok(_) => Some(line.trim().to_string()),
    }
}

fn parse_integer(text: &str) -> Option<i64> {
    text.trim().parse::<i64>().ok()
}

fn env_number(name: &str) -> String {
    env::var(name).unwrap_or_else(|_| "null".to_string())
}

fn print_limits() {
    let user_language = match env::var("USER_LANGUAGE") {
        Ok(value) => format!("\"{}\"", value),
        Err(_) => "null".to_string(),
    };
    let per_language_limits = env::var("PER_LANGUAGE_LIMITS").unwrap_or_else(|_| "null".to_string());

    eprintln!(
        "{{\"problem_time_limit\": {}, \"problem_output_limit\": {}, \"problem_memory_limit\": {}, \
         \"problem_pid_limit\": {}, \"user_language\": {}, \"per_language_limits\": {}}}",
        env_number("PROBLEM_TIME_LIMIT"),
        env_number("PROBLEM_OUTPUT_LIMIT"),
        env_number("PROBLEM_MEMORY_LIMIT"),
        env_number("PROBLEM_PID_LIMIT"),
        user_language,
        per_language_limits,
    );
    let _ = io::stderr().flush();
}

fn read_secret_value(stdin: &mut io::StdinLock) -> i64 {
    match read_line(stdin).as_deref().and_then(parse_integer) {
        Some(value) => value,
        None => finish(RE, Some("Invalid secret data".to_string())),
    }
}

fn main() {
    let stdin = io::stdin();
    let mut stdin = stdin.lock();
    let mut stdout = io::stdout();

    let max_value = read_secret_value(&mut stdin);
    let max_tries = read_secret_value(&mut stdin);
    let secret = read_secret_value(&mut stdin);

    print_limits();

    writeln!(stdout, "{}", max_value).unwrap();
    stdout.flush().unwrap();

    let mut iteration: i64 = 0;
    while let Some(user) = read_line(&mut stdin) {
        if let Some(answer) = user.strip_prefix('!') {
            match parse_integer(answer) {
                None => finish(RE, Some("Invalid data".to_string())),
                Some(answer) if answer == secret => finish(AC, None),
                Some(_) => finish(WA, Some(format!("!{}", secret))),
            }
        }

        if iteration > max_tries {
            finish(TLE, Some("Exceeded number of allowed iterations".to_string()));
        }
        let value = match parse_integer(&user) {
            Some(value) => value,
            None => finish(RE, Some("Invalid data".to_string())),
        };
        writeln!(stdout, "{}", if secret < value { "<" } else { ">=" }).unwrap();
        stdout.flush().unwrap();
        iteration += 1;
    }

    finish(RE, Some("Unexpected flow".to_string()));
}
