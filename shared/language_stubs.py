#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Per-language starter code shown in the Arena submission editor.

This is UI-only boilerplate with no judge dependency. The templates use
line-oriented EOF loops so contestants have a working skeleton to start from.
"""

from __future__ import annotations

_DEFAULT_STUBS: dict[str, str] = {
    "gcc-c17": (
        "#include <stdio.h>\n"
        "#include <string.h>\n"
        "\n"
        "int main(void) {\n"
        "    char line[4096];\n"
        "\n"
        "    while (fgets(line, sizeof(line), stdin) != NULL) {\n"
        "        fwrite(line, 1, strlen(line), stdout);\n"
        "    }\n"
        "\n"
        "    return 0;\n"
        "}\n"
    ),
    "gcc-cpp23": (
        "#include <bits/stdc++.h>\n"
        "using namespace std;\n"
        "\n"
        "int main() {\n"
        "    ios::sync_with_stdio(false);\n"
        "    cin.tie(nullptr);\n"
        "\n"
        "    string line;\n"
        "    while (getline(cin, line)) {\n"
        '        cout << line << "\\n";\n'
        "    }\n"
        "\n"
        "    return 0;\n"
        "}\n"
    ),
    "python3": (
        "import sys\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    while True:\n"
        "        line = sys.stdin.buffer.readline()\n"
        '        if line == b"":\n'
        "            break\n"
        "        sys.stdout.buffer.write(line)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    ),
    "java": (
        "import java.io.*;\n"
        "\n"
        "public class Main {\n"
        "    public static void main(String[] args) throws IOException {\n"
        "        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));\n"
        "        BufferedWriter out = new BufferedWriter(new OutputStreamWriter(System.out));\n"
        "        String line;\n"
        "\n"
        "        while ((line = br.readLine()) != null) {\n"
        "            out.write(line);\n"
        "            out.newLine();\n"
        "        }\n"
        "\n"
        "        out.flush();\n"
        "    }\n"
        "}\n"
    ),
    "javascript": (
        "const data = require('fs').readFileSync(0, 'utf8');\n"
        "const input = data.split('\\n');\n"
        "let line = 0;\n"
        "\n"
        "while (line < input.length) {\n"
        "    const suffix = line + 1 < input.length ? '\\n' : '';\n"
        "    process.stdout.write(input[line] + suffix);\n"
        "    line += 1;\n"
        "}\n"
    ),
    "kotlin": (
        "import java.io.BufferedReader\n"
        "import java.io.InputStreamReader\n"
        "\n"
        "fun main() {\n"
        "    val br = BufferedReader(InputStreamReader(System.`in`))\n"
        "    var line = br.readLine()\n"
        "\n"
        "    while (line != null) {\n"
        "        println(line)\n"
        "        line = br.readLine()\n"
        "    }\n"
        "}\n"
    ),
    "fpc-pascal": (
        "program Main;\n"
        "\n"
        "var\n"
        "    line: ansistring;\n"
        "\n"
        "begin\n"
        "    while not eof(input) do\n"
        "    begin\n"
        "        readln(line);\n"
        "        writeln(line);\n"
        "    end;\n"
        "end.\n"
    ),
    "go": (
        "package main\n"
        "\n"
        "import (\n"
        '    "bufio"\n'
        '    "io"\n'
        '    "os"\n'
        ")\n"
        "\n"
        "func main() {\n"
        "    reader := bufio.NewReader(os.Stdin)\n"
        "    writer := bufio.NewWriter(os.Stdout)\n"
        "    defer writer.Flush()\n"
        "\n"
        "    for {\n"
        "        line, err := reader.ReadSlice('\\n')\n"
        "        if len(line) > 0 {\n"
        "            writer.Write(line)\n"
        "        }\n"
        "        if err == io.EOF {\n"
        "            break\n"
        "        }\n"
        "        if err == bufio.ErrBufferFull {\n"
        "            continue\n"
        "        }\n"
        "        if err != nil {\n"
        "            break\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
    "rust": (
        "use std::io::{self, BufRead, Write};\n"
        "\n"
        "fn main() {\n"
        "    let stdin = io::stdin();\n"
        "    let stdout = io::stdout();\n"
        "    let mut reader = stdin.lock();\n"
        "    let mut out = io::BufWriter::new(stdout.lock());\n"
        "    let mut line = String::new();\n"
        "\n"
        "    while reader.read_line(&mut line).unwrap() > 0 {\n"
        "        out.write_all(line.as_bytes()).unwrap();\n"
        "        line.clear();\n"
        "    }\n"
        "}\n"
    ),
    "c-sharp": (
        "using System;\n"
        "using System.IO;\n"
        "\n"
        "class Program\n"
        "{\n"
        "    static void Main()\n"
        "    {\n"
        "        using var reader = new StreamReader(Console.OpenStandardInput());\n"
        "        using var writer = new StreamWriter(Console.OpenStandardOutput());\n"
        "        string? line;\n"
        "\n"
        "        while ((line = reader.ReadLine()) != null)\n"
        "        {\n"
        "            writer.WriteLine(line);\n"
        "        }\n"
        "    }\n"
        "}\n"
    ),
    "haskell": (
        "import System.IO (isEOF)\n"
        "\n"
        "echoUntilEof :: IO ()\n"
        "echoUntilEof = do\n"
        "    done <- isEOF\n"
        "    if done\n"
        "        then return ()\n"
        "        else do\n"
        "            line <- getLine\n"
        "            putStrLn line\n"
        "            echoUntilEof\n"
        "\n"
        "main :: IO ()\n"
        "main = echoUntilEof\n"
    ),
    "lua": (
        "while true do\n"
        '    local line = io.read("*line")\n'
        "    if line == nil then\n"
        "        break\n"
        "    end\n"
        '    io.write(line, "\\n")\n'
        "end\n"
    ),
    "prolog": (
        ":- initialization(main).\n"
        "\n"
        "main :-\n"
        "    read_line_to_string(user_input, Line),\n"
        "    echo_until_eof(Line).\n"
        "\n"
        "echo_until_eof(end_of_file) :- !.\n"
        "echo_until_eof(Line) :-\n"
        '    format("~s~n", [Line]),\n'
        "    read_line_to_string(user_input, Next),\n"
        "    echo_until_eof(Next).\n"
    ),
    "fortran": (
        "program main\n"
        "    implicit none\n"
        "    character(len=4096) :: line\n"
        "    integer :: status\n"
        "\n"
        "    do\n"
        "        read(*, '(A)', iostat=status) line\n"
        "        if (status /= 0) exit\n"
        "        write(*, '(A)') trim(line)\n"
        "    end do\n"
        "end program main\n"
    ),
    "swift": ("while let line = readLine(strippingNewline: true) {\n    print(line)\n}\n"),
    "perl": ("use strict;\nuse warnings;\n\nwhile (my $line = <STDIN>) {\n    print $line;\n}\n"),
}


def default_stub_for_language_id(language_id: str) -> str:
    """Return the starter source code shown when a language is selected.

    Args:
        language_id: Registered language identifier, e.g. ``"python3"``.

    Returns:
        str: Per-language starter code, or an empty string when none is defined.
    """
    return _DEFAULT_STUBS.get(language_id, "")
