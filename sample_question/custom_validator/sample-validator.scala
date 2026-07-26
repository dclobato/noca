// Sample interactive validator for the "guess the secret number" problem.
//
// Reads three secret values from stdin (max_value, max_tries, secret), reports the
// judge-supplied limits on stderr, then plays the guessing game with the contestant
// program. The exit code is the verdict.

object Main {

  private val AC = 0
  private val WA = 1
  private val TLE = 2
  private val RE = 3
  private val PE = 4

  private def finish(exitCode: Int, message: String = ""): Nothing = {
    if (message.nonEmpty) Console.err.println(message)
    sys.exit(exitCode)
  }

  private def readTrimmedLine(): Option[String] = {
    val line = scala.io.StdIn.readLine()
    if (line == null) None
    else {
      var end = line.length
      while (end > 0 && (line.charAt(end - 1) == '\r' || line.charAt(end - 1) == ' ' ||
             line.charAt(end - 1) == '\t')) {
        end -= 1
      }
      Some(line.substring(0, end))
    }
  }

  private def parseLong(text: String): Option[Long] =
    if (text.matches("[+-]?\\d+")) text.toLongOption else None

  private def envNumber(name: String): String =
    Option(System.getenv(name)).getOrElse("null")

  private def printLimits(): Unit = {
    val userLanguage =
      Option(System.getenv("USER_LANGUAGE")).map(value => "\"" + value + "\"").getOrElse("null")
    val perLanguageLimits =
      Option(System.getenv("PER_LANGUAGE_LIMITS")).getOrElse("null")

    Console.err.println(
      "{" +
        "\"problem_time_limit\": " + envNumber("PROBLEM_TIME_LIMIT") + ", " +
        "\"problem_output_limit\": " + envNumber("PROBLEM_OUTPUT_LIMIT") + ", " +
        "\"problem_memory_limit\": " + envNumber("PROBLEM_MEMORY_LIMIT") + ", " +
        "\"problem_pid_limit\": " + envNumber("PROBLEM_PID_LIMIT") + ", " +
        "\"user_language\": " + userLanguage + ", " +
        "\"per_language_limits\": " + perLanguageLimits +
        "}"
    )
  }

  private def readSecretValue(): Long =
    readTrimmedLine().flatMap(parseLong).getOrElse(finish(RE, "Invalid secret data"))

  def main(args: Array[String]): Unit = {
    val maxValue = readSecretValue()
    val maxTries = readSecretValue()
    val secret = readSecretValue()

    printLimits()

    println(maxValue)
    Console.flush()

    var iteration = 0L
    var running = true

    while (running) {
      readTrimmedLine() match {
        case None => running = false
        case Some(line) =>
          if (line.nonEmpty && line.charAt(0) == '!') {
            parseLong(line.substring(1)) match {
              case None => finish(RE, "Invalid data")
              case Some(answer) =>
                if (answer == secret) finish(AC)
                finish(WA, "!" + secret)
            }
          } else {
            if (iteration > maxTries) finish(TLE, "Exceeded number of allowed iterations")
            parseLong(line) match {
              case None => finish(RE, "Invalid data")
              case Some(value) =>
                println(if (secret < value) "<" else ">=")
                Console.flush()
                iteration += 1
            }
          }
      }
    }

    finish(RE, "Unexpected flow")
  }
}
