import java.util.Scanner;
import java.lang.Math;

public class Main {
    static class Point {
        int x, y;
        Point(int x, int y) {
            this.x = x;
            this.y = y;
        }
    }

    static int manhattan(Point a, Point b) {
        return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
    }

    // Using a large value for infinity, but safe from immediate overflow
    static final int INF = 1000000000;

    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);
        
        if (!scanner.hasNext()) return;

        int L = scanner.nextInt();
        int C = scanner.nextInt();
        int K = scanner.nextInt();

        Point start = new Point(scanner.nextInt(), scanner.nextInt());
        Point end = new Point(scanner.nextInt(), scanner.nextInt());

        Point[] houses = new Point[K];
        for (int i = 0; i < K; i++) {
            houses[i] = new Point(scanner.nextInt(), scanner.nextInt());
        }

        if (K == 0) {
            System.out.println(manhattan(start, end));
            return;
        }

        // Setup all points array: 0=Start, 1..K=Houses, K+1=End
        Point[] allPoints = new Point[K + 2];
        allPoints[0] = start;
        for (int i = 0; i < K; i++) {
            allPoints[i + 1] = houses[i];
        }
        allPoints[K + 1] = end;

        // Calculate distance matrix
        int totalPoints = K + 2;
        int[][] dist = new int[totalPoints][totalPoints];
        for (int i = 0; i < totalPoints; i++) {
            for (int j = 0; j < totalPoints; j++) {
                dist[i][j] = manhattan(allPoints[i], allPoints[j]);
            }
        }

        // DP initialization
        int limit = 1 << K;
        int[][] dp = new int[limit][K];
        
        for (int i = 0; i < limit; i++) {
            for (int j = 0; j < K; j++) {
                dp[i][j] = INF;
            }
        }

        // Base case: Start -> House i
        for (int i = 0; i < K; i++) {
            dp[1 << i][i] = dist[0][i + 1];
        }

        // Iterate masks
        for (int mask = 0; mask < limit; mask++) {
            for (int u = 0; u < K; u++) {
                // Check if u is in mask
                if ((mask & (1 << u)) == 0) continue;

                for (int v = 0; v < K; v++) {
                    // Check if v is NOT in mask
                    if ((mask & (1 << v)) != 0) continue;

                    int nextMask = mask | (1 << v);
                    int newCost = dp[mask][u] + dist[u + 1][v + 1];
                    
                    if (newCost < dp[nextMask][v]) {
                        dp[nextMask][v] = newCost;
                    }
                }
            }
        }

        // Find min cost to connect last house to destination
        int finalMask = (1 << K) - 1;
        int minTotal = INF;

        for (int i = 0; i < K; i++) {
            int currentTotal = dp[finalMask][i] + dist[i + 1][K + 1];
            if (currentTotal < minTotal) {
                minTotal = currentTotal;
            }
        }

        System.out.println(minTotal);
        scanner.close();
    }
}
