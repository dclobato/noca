use std::io::{self, BufRead, Write};

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    let mut lines = stdin.lock().lines();

    let upper: i64 = lines
        .next()
        .unwrap()
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    let mut lower: i64 = 1;
    let mut upper = upper;

    while lower < upper {
        let number = lower + (upper - lower) / 2 + 1;
        writeln!(out, "{}", number).unwrap();
        out.flush().unwrap();

        let answer = lines.next().unwrap().unwrap();
        let answer = answer.trim();

        match answer {
            "<"  => upper = number - 1,
            ">=" => lower = number,
            _    => {
                eprintln!("Invalid answer from validator: {:?}", answer);
                std::process::exit(1);
            }
        }
    }

    writeln!(out, "!{}", lower).unwrap();
    out.flush().unwrap();
}
