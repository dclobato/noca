<?php

// Sample interactive validator for the "guess the secret number" problem.
//
// Reads three secret values from stdin (max_value, max_tries, secret), reports the
// judge-supplied limits on stderr, then plays the guessing game with the contestant
// program. The exit code is the verdict.

const AC = 0;
const WA = 1;
const TLE = 2;
const RE = 3;
const PE = 4;

function finish(int $exitCode, string $message = ""): never
{
    if ($message !== "") {
        fwrite(STDERR, $message . "\n");
    }
    exit($exitCode);
}

function readTrimmedLine(): ?string
{
    $line = fgets(STDIN);
    if ($line === false) {
        return null;
    }
    return rtrim($line, "\r\n \t");
}

function parseLong(string $text): ?int
{
    if (preg_match('/^[+-]?\d+$/', $text) !== 1) {
        return null;
    }
    return (int) $text;
}

function envNumber(string $name): string
{
    $value = getenv($name);
    return $value === false ? "null" : $value;
}

function printLimits(): void
{
    $userLanguage = getenv("USER_LANGUAGE");
    $perLanguageLimits = getenv("PER_LANGUAGE_LIMITS");

    fwrite(
        STDERR,
        "{"
            . '"problem_time_limit": ' . envNumber("PROBLEM_TIME_LIMIT") . ", "
            . '"problem_output_limit": ' . envNumber("PROBLEM_OUTPUT_LIMIT") . ", "
            . '"problem_memory_limit": ' . envNumber("PROBLEM_MEMORY_LIMIT") . ", "
            . '"problem_pid_limit": ' . envNumber("PROBLEM_PID_LIMIT") . ", "
            . '"user_language": ' . ($userLanguage !== false ? '"' . $userLanguage . '"' : "null") . ", "
            . '"per_language_limits": ' . ($perLanguageLimits !== false ? $perLanguageLimits : "null")
            . "}\n"
    );
}

function readSecretValue(): int
{
    $line = readTrimmedLine();
    if ($line === null) {
        finish(RE, "Invalid secret data");
    }
    $value = parseLong($line);
    if ($value === null) {
        finish(RE, "Invalid secret data");
    }
    return $value;
}

$maxValue = readSecretValue();
$maxTries = readSecretValue();
$secret = readSecretValue();

printLimits();

echo $maxValue, "\n";
flush();

for ($iteration = 0; ; $iteration++) {
    $line = readTrimmedLine();
    if ($line === null) {
        break;
    }

    if ($line !== "" && $line[0] === "!") {
        $answer = parseLong(substr($line, 1));
        if ($answer === null) {
            finish(RE, "Invalid data");
        }
        if ($answer === $secret) {
            finish(AC);
        }
        finish(WA, "!" . $secret);
    }

    if ($iteration > $maxTries) {
        finish(TLE, "Exceeded number of allowed iterations");
    }

    $value = parseLong($line);
    if ($value === null) {
        finish(RE, "Invalid data");
    }

    echo ($secret < $value ? "<" : ">="), "\n";
    flush();
}

finish(RE, "Unexpected flow");
