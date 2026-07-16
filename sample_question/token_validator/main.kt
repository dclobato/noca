import java.util.Scanner
import kotlin.math.abs
import kotlin.math.min

data class Point(val x: Int, val y: Int)

fun manhattan(a: Point, b: Point): Int {
    return abs(a.x - b.x) + abs(a.y - b.y)
}

fun main() {
    val scanner = Scanner(System.`in`)
    if (!scanner.hasNext()) return

    // Reading inputs
    val L = scanner.nextInt()
    val C = scanner.nextInt()
    val K = scanner.nextInt()

    val start = Point(scanner.nextInt(), scanner.nextInt())
    val end = Point(scanner.nextInt(), scanner.nextInt())

    val houses = Array(K) { Point(0, 0) }
    for (i in 0 until K) {
        houses[i] = Point(scanner.nextInt(), scanner.nextInt())
    }

    // Edge case: No houses to visit
    if (K == 0) {
        println(manhattan(start, end))
        return
    }

    // Create full list of points: [Start, House1...HouseK, End]
    val allPoints = mutableListOf<Point>()
    allPoints.add(start)
    allPoints.addAll(houses)
    allPoints.add(end)

    val n = allPoints.size
    val dist = Array(n) { IntArray(n) }

    // Precalculate distances
    for (i in 0 until n) {
        for (j in 0 until n) {
            dist[i][j] = manhattan(allPoints[i], allPoints[j])
        }
    }

    // DP Initialization
    // dp[mask][i] = min cost to visit set 'mask' ending at house index 'i'
    val numStates = 1 shl K
    val dp = Array(numStates) { IntArray(K) { 1000000000 } }

    // Base cases: From Start (index 0) to first house i
    for (i in 0 until K) {
        dp[1 shl i][i] = dist[0][i + 1]
    }

    // Iterate through masks
    for (mask in 0 until numStates) {
        for (u in 0 until K) {
            // If house u is not in the current mask, skip
            if ((mask and (1 shl u)) == 0) continue

            for (v in 0 until K) {
                // If house v is already in mask, skip
                if ((mask and (1 shl v)) != 0) continue

                val nextMask = mask or (1 shl v)
                // dist lookup: u+1 and v+1 because index 0 is Start
                val cost = dp[mask][u] + dist[u + 1][v + 1]
                dp[nextMask][v] = min(dp[nextMask][v], cost)
            }
        }
    }

    // Calculate final cost to Destination
    var finalDist = 1000000000
    val fullMask = (1 shl K) - 1

    for (i in 0 until K) {
        // dist lookup: i+1 (house) to K+1 (destination)
        val cost = dp[fullMask][i] + dist[i + 1][K + 1]
        finalDist = min(finalDist, cost)
    }

    println(finalDist)
}
