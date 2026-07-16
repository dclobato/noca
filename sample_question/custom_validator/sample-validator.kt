import kotlin.system.exitProcess

const val AC = 0
const val WA = 1
const val TLE = 2
const val RE = 3
const val PE = 4

fun finish(exitcode: Int, message: String? = null): Nothing {
    if (message != null) {
        System.err.println(message)
        System.err.flush()
    }
    exitProcess(exitcode)
}

fun readTrimmedLine(): String? = readlnOrNull()?.trim()

fun parseLong(text: String): Long? = text.trim().toLongOrNull()

fun envNumber(name: String): String = System.getenv(name) ?: "null"

fun printLimits() {
    val userLanguage = System.getenv("USER_LANGUAGE")
    val perLanguageLimits = System.getenv("PER_LANGUAGE_LIMITS")

    System.err.println(
        "{" +
            "\"problem_time_limit\": ${envNumber("PROBLEM_TIME_LIMIT")}, " +
            "\"problem_output_limit\": ${envNumber("PROBLEM_OUTPUT_LIMIT")}, " +
            "\"problem_memory_limit\": ${envNumber("PROBLEM_MEMORY_LIMIT")}, " +
            "\"problem_pid_limit\": ${envNumber("PROBLEM_PID_LIMIT")}, " +
            "\"user_language\": ${if (userLanguage == null) "null" else "\"$userLanguage\""}, " +
            "\"per_language_limits\": ${perLanguageLimits ?: "null"}" +
            "}"
    )
    System.err.flush()
}

fun readSecretValue(): Long {
    val line = readTrimmedLine() ?: finish(RE, "Invalid secret data")
    return parseLong(line) ?: finish(RE, "Invalid secret data")
}

fun main() {
    val maxValue = readSecretValue()
    val maxTries = readSecretValue()
    val secret = readSecretValue()

    printLimits()

    println(maxValue)
    System.out.flush()

    var iteration = 0L
    while (true) {
        val user = readTrimmedLine() ?: break

        if (user.startsWith("!")) {
            val answer = parseLong(user.substring(1)) ?: finish(RE, "Invalid data")
            if (answer == secret) {
                finish(AC)
            }
            finish(WA, "!$secret")
        }

        if (iteration > maxTries) {
            finish(TLE, "Exceeded number of allowed iterations")
        }
        val value = parseLong(user) ?: finish(RE, "Invalid data")
        println(if (secret < value) "<" else ">=")
        System.out.flush()
        iteration++
    }

    finish(RE, "Unexpected flow")
}
