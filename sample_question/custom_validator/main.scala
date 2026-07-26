// Interactive binary search: guess the secret number the validator is holding.
// Every message must be flushed immediately, or the two sides deadlock.

object Main {
  def main(args: Array[String]): Unit = {
    var upper = scala.io.StdIn.readLine().trim.toInt
    var lower = 1

    while (lower < upper) {
      val number = lower + (upper - lower) / 2 + 1

      println(number)
      Console.flush()

      val answer = scala.io.StdIn.readLine().trim

      if (answer == "<") {
        upper = number - 1
      } else if (answer == ">=") {
        lower = number
      } else {
        Console.err.println(s"Invalid answer from validator: $answer")
        sys.exit(1)
      }
    }

    println(s"!$lower")
    Console.flush()
    sys.exit(0)
  }
}
