"use strict";

const fs = require("fs");

const EXIT_CODES = {
    AC: 0,
    WA: 1,
    TLE: 2,
    RE: 3,
    PE: 4,
};

function finish(exitcode, message) {
    if (message) {
        fs.writeSync(2, message + "\n");
    }
    process.exit(exitcode);
}

// Node has no blocking line reader, so read stdin byte by byte and cut it into lines:
// the conversation is strictly turn-based, and buffering ahead would deadlock it.
let stdinClosed = false;

function readLine() {
    if (stdinClosed) {
        return null;
    }
    const chunk = Buffer.alloc(1);
    let line = "";
    for (;;) {
        let read = 0;
        try {
            read = fs.readSync(0, chunk, 0, 1, null);
        } catch (error) {
            if (error.code === "EAGAIN") {
                continue;
            }
            if (error.code !== "EOF") {
                throw error;
            }
            read = 0;
        }
        if (read === 0) {
            stdinClosed = true;
            return line.length > 0 ? line : null;
        }
        if (chunk[0] === 0x0a) {
            return line;
        }
        line += String.fromCharCode(chunk[0]);
    }
}

function parseInteger(text) {
    const trimmed = text.trim();
    if (!/^[+-]?\d+$/.test(trimmed)) {
        return null;
    }
    return Number(trimmed);
}

function envNumber(name) {
    const value = process.env[name];
    return value === undefined ? "null" : value;
}

function printLimits() {
    const userLanguage = process.env.USER_LANGUAGE;
    const perLanguageLimits = process.env.PER_LANGUAGE_LIMITS;

    const limits =
        "{" +
        `"problem_time_limit": ${envNumber("PROBLEM_TIME_LIMIT")}, ` +
        `"problem_output_limit": ${envNumber("PROBLEM_OUTPUT_LIMIT")}, ` +
        `"problem_memory_limit": ${envNumber("PROBLEM_MEMORY_LIMIT")}, ` +
        `"problem_pid_limit": ${envNumber("PROBLEM_PID_LIMIT")}, ` +
        `"user_language": ${userLanguage === undefined ? "null" : JSON.stringify(userLanguage)}, ` +
        `"per_language_limits": ${perLanguageLimits === undefined ? "null" : perLanguageLimits}` +
        "}";
    fs.writeSync(2, limits + "\n");
}

function readSecretValue() {
    const line = readLine();
    const value = line === null ? null : parseInteger(line);
    if (value === null) {
        finish(EXIT_CODES.RE, "Invalid secret data");
    }
    return value;
}

function main() {
    const maxValue = readSecretValue();
    const maxTries = readSecretValue();
    const secret = readSecretValue();

    printLimits();

    fs.writeSync(1, maxValue + "\n");

    for (let iteration = 0; ; iteration++) {
        const line = readLine();
        if (line === null) {
            break;
        }
        const user = line.trim();

        if (user.startsWith("!")) {
            const answer = parseInteger(user.slice(1));
            if (answer === null) {
                finish(EXIT_CODES.RE, "Invalid data");
            }
            if (answer === secret) {
                finish(EXIT_CODES.AC);
            }
            finish(EXIT_CODES.WA, `!${secret}`);
        }

        if (iteration > maxTries) {
            finish(EXIT_CODES.TLE, "Exceeded number of allowed iterations");
        }
        const value = parseInteger(user);
        if (value === null) {
            finish(EXIT_CODES.RE, "Invalid data");
        }
        fs.writeSync(1, (secret < value ? "<" : ">=") + "\n");
    }

    finish(EXIT_CODES.RE, "Unexpected flow");
}

main();
