import java.io.BufferedReader
import java.io.InputStreamReader

fun main() {
    val br = BufferedReader(InputStreamReader(System.`in`))
    var upper = br.readLine().trim().toInt()
    var lower = 1

    while (lower < upper) {
        val number = lower + (upper - lower) / 2 + 1
        println(number)
        System.out.flush()

        val answer = br.readLine().trim()

        when (answer) {
            "<"  -> upper = number - 1
            ">=" -> lower = number
            else -> throw RuntimeException("Invalid answer from validator: $answer")
        }
    }

    println("!$lower")
    System.out.flush()
}
