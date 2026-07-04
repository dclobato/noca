using System;
using System.Collections.Generic;

public class Program
{
    // Simple struct to hold coordinates
    struct Point
    {
        public int x, y;
        public Point(int x, int y) { this.x = x; this.y = y; }
    }

    // Calculate Manhattan distance
    static int GetManhattanDistance(Point a, Point b)
    {
        return Math.Abs(a.x - b.x) + Math.Abs(a.y - b.y);
    }

    // A safe infinity value (int.MaxValue can overflow when adding)
    const int INF = 1_000_000_000;

    public static void Main(string[] args)
    {
        // 1. Read Inputs
        int L = TokenReader.NextInt();
        int C = TokenReader.NextInt();
        int K = TokenReader.NextInt();

        Point start = new Point(TokenReader.NextInt(), TokenReader.NextInt());
        Point end = new Point(TokenReader.NextInt(), TokenReader.NextInt());

        Point[] houses = new Point[K];
        for (int i = 0; i < K; i++)
        {
            houses[i] = new Point(TokenReader.NextInt(), TokenReader.NextInt());
        }

        // 2. Edge Case: No houses to visit
        if (K == 0)
        {
            Console.WriteLine(GetManhattanDistance(start, end));
            return;
        }

        // 3. Setup all points: [0:Start, 1..K:Houses, K+1:End]
        Point[] allPoints = new Point[K + 2];
        allPoints[0] = start;
        for (int i = 0; i < K; i++)
        {
            allPoints[i + 1] = houses[i];
        }
        allPoints[K + 1] = end;

        // 4. Precalculate Distance Matrix
        int totalPoints = K + 2;
        int[,] dist = new int[totalPoints, totalPoints];

        for (int i = 0; i < totalPoints; i++)
        {
            for (int j = 0; j < totalPoints; j++)
            {
                dist[i, j] = GetManhattanDistance(allPoints[i], allPoints[j]);
            }
        }

        // 5. Initialize DP
        // dp[mask, i] = min cost to visit the set of houses in 'mask', ending at house 'i'
        // 'i' ranges from 0 to K-1 (representing houses 1 to K in allPoints)
        int numStates = 1 << K;
        int[,] dp = new int[numStates, K];

        for (int mask = 0; mask < numStates; mask++)
        {
            for (int i = 0; i < K; i++)
            {
                dp[mask, i] = INF;
            }
        }

        // Base case: From Start (index 0) to first house i
        for (int i = 0; i < K; i++)
        {
            // Mask 1<<i means only house i is visited
            dp[1 << i, i] = dist[0, i + 1];
        }

        // 6. DP Loop
        for (int mask = 0; mask < numStates; mask++)
        {
            for (int u = 0; u < K; u++)
            {
                // If house u is not in the current mask, skip
                if ((mask & (1 << u)) == 0) continue;
                
                // If this state is unreachable, skip
                if (dp[mask, u] == INF) continue;

                for (int v = 0; v < K; v++)
                {
                    // If house v is ALREADY in mask, skip
                    if ((mask & (1 << v)) != 0) continue;

                    int nextMask = mask | (1 << v);
                    // dist lookup: u+1 and v+1 because index 0 is Start in allPoints
                    int newCost = dp[mask, u] + dist[u + 1, v + 1];

                    if (newCost < dp[nextMask, v])
                    {
                        dp[nextMask, v] = newCost;
                    }
                }
            }
        }

        // 7. Calculate Final Cost (Last visited house -> Destination)
        int finalMask = (1 << K) - 1;
        int bestCost = INF;

        for (int i = 0; i < K; i++)
        {
            // dist lookup: house i+1 to Destination (K+1)
            int currentCost = dp[finalMask, i] + dist[i + 1, K + 1];
            if (currentCost < bestCost)
            {
                bestCost = currentCost;
            }
        }

        Console.WriteLine(bestCost);
    }

    // Helper class to mimic scanf behavior
    static class TokenReader
    {
        private static string[] _tokens;
        private static int _position;

        public static int NextInt()
        {
            while (_tokens == null || _position >= _tokens.Length)
            {
                string line = Console.ReadLine();
                if (line == null) return 0; // End of input
                _tokens = line.Split(new char[] { ' ', '\t', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries);
                _position = 0;
            }
            return int.Parse(_tokens[_position++]);
        }
    }
}
