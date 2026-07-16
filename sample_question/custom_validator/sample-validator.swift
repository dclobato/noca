import Foundation

let AC: Int32 = 0
let WA: Int32 = 1
let TLE: Int32 = 2
let RE: Int32 = 3
let PE: Int32 = 4

func finish(_ exitcode: Int32, _ message: String? = nil) -> Never {
    if let message = message {
        FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    }
    exit(exitcode)
}

func readTrimmedLine() -> String? {
    guard let line = readLine(strippingNewline: true) else {
        return nil
    }
    return line.trimmingCharacters(in: .whitespaces)
}

func parseInteger(_ text: String) -> Int64? {
    return Int64(text.trimmingCharacters(in: .whitespaces))
}

func envNumber(_ name: String) -> String {
    return ProcessInfo.processInfo.environment[name] ?? "null"
}

func printLimits() {
    let userLanguage = ProcessInfo.processInfo.environment["USER_LANGUAGE"]
    let perLanguageLimits = ProcessInfo.processInfo.environment["PER_LANGUAGE_LIMITS"]

    let limits = "{"
        + "\"problem_time_limit\": \(envNumber("PROBLEM_TIME_LIMIT")), "
        + "\"problem_output_limit\": \(envNumber("PROBLEM_OUTPUT_LIMIT")), "
        + "\"problem_memory_limit\": \(envNumber("PROBLEM_MEMORY_LIMIT")), "
        + "\"problem_pid_limit\": \(envNumber("PROBLEM_PID_LIMIT")), "
        + "\"user_language\": \(userLanguage.map { "\"\($0)\"" } ?? "null"), "
        + "\"per_language_limits\": \(perLanguageLimits ?? "null")"
        + "}"
    FileHandle.standardError.write((limits + "\n").data(using: .utf8)!)
}

func readSecretValue() -> Int64 {
    guard let line = readTrimmedLine(), let value = parseInteger(line) else {
        finish(RE, "Invalid secret data")
    }
    return value
}

let maxValue = readSecretValue()
let maxTries = readSecretValue()
let secret = readSecretValue()

printLimits()

print(maxValue)
fflush(stdout)

var iteration: Int64 = 0
while let user = readTrimmedLine() {
    if user.hasPrefix("!") {
        guard let answer = parseInteger(String(user.dropFirst())) else {
            finish(RE, "Invalid data")
        }
        if answer == secret {
            finish(AC)
        }
        finish(WA, "!\(secret)")
    }

    if iteration > maxTries {
        finish(TLE, "Exceeded number of allowed iterations")
    }
    guard let value = parseInteger(user) else {
        finish(RE, "Invalid data")
    }
    print(secret < value ? "<" : ">=")
    fflush(stdout)
    iteration += 1
}

finish(RE, "Unexpected flow")
