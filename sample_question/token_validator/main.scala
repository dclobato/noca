// Minimum-cost route: start -> all K mandatory houses (any order) -> end,
// using Manhattan distance. Solved with a Held-Karp bitmask DP.

object Main {

  private case class Point(x: Int, y: Int)

  private def manhattan(a: Point, b: Point): Int =
    math.abs(a.x - b.x) + math.abs(a.y - b.y)

  def main(args: Array[String]): Unit = {
    val tokens: Iterator[String] =
      Iterator
        .continually(scala.io.StdIn.readLine())
        .takeWhile(_ != null)
        .flatMap(_.trim.split("\\s+"))
        .filter(_.nonEmpty)

    def nextInt(): Int = tokens.next().toInt

    val _L = nextInt()
    val _C = nextInt()
    val K = nextInt()

    val start = Point(nextInt(), nextInt())
    val end = Point(nextInt(), nextInt())

    val houses = Array.fill(K)(Point(0, 0))
    for (i <- 0 until K) {
      val x = nextInt()
      val y = nextInt()
      houses(i) = Point(x, y)
    }

    if (K == 0) {
      println(manhattan(start, end))
      return
    }

    // pts: [0: start, 1..K: houses, K+1: end]
    val pts = (start +: houses.toIndexedSeq) :+ end
    val n = pts.size

    val dist = Array.ofDim[Int](n, n)
    for (i <- 0 until n; j <- 0 until n) {
      dist(i)(j) = manhattan(pts(i), pts(j))
    }

    val INF = 1000000000
    val limit = 1 << K
    val dp = Array.fill(limit, K)(INF)

    for (i <- 0 until K) {
      dp(1 << i)(i) = dist(0)(i + 1)
    }

    for (mask <- 0 until limit; u <- 0 until K) {
      if ((mask & (1 << u)) != 0 && dp(mask)(u) != INF) {
        for (v <- 0 until K if (mask & (1 << v)) == 0) {
          val nextMask = mask | (1 << v)
          val newCost = dp(mask)(u) + dist(u + 1)(v + 1)
          if (newCost < dp(nextMask)(v)) dp(nextMask)(v) = newCost
        }
      }
    }

    val fullMask = limit - 1
    var best = INF
    for (i <- 0 until K) {
      best = math.min(best, dp(fullMask)(i) + dist(i + 1)(K + 1))
    }

    println(best)
  }
}
