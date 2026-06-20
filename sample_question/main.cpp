#include <algorithm>
#include <climits>
#include <cmath>
#include <iostream>
#include <vector>

using namespace std;

struct Point {
    int x, y;
};

int manhattan(const Point& a, const Point& b) {
    return abs(a.x - b.x) + abs(a.y - b.y);
}

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);

    int L, C, K;
    cin >> L >> C >> K;

    Point start, end_pt;
    cin >> start.x >> start.y;
    cin >> end_pt.x >> end_pt.y;

    vector<Point> houses(K);
    for (int i = 0; i < K; i++) {
        cin >> houses[i].x >> houses[i].y;
    }

    if (K == 0) {
        cout << manhattan(start, end_pt) << "\n";
        return 0;
    }

    // allPoints: [0: start, 1..K: houses, K+1: end]
    vector<Point> pts;
    pts.push_back(start);
    for (auto& h : houses) pts.push_back(h);
    pts.push_back(end_pt);

    int n = static_cast<int>(pts.size());
    vector<vector<int>> dist(n, vector<int>(n));
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            dist[i][j] = manhattan(pts[i], pts[j]);

    const int INF = 1000000000;
    int limit = 1 << K;
    vector<vector<int>> dp(limit, vector<int>(K, INF));

    for (int i = 0; i < K; i++) {
        dp[1 << i][i] = dist[0][i + 1];
    }

    for (int mask = 0; mask < limit; mask++) {
        for (int u = 0; u < K; u++) {
            if (!(mask & (1 << u))) continue;
            if (dp[mask][u] == INF) continue;
            for (int v = 0; v < K; v++) {
                if (mask & (1 << v)) continue;
                int next_mask = mask | (1 << v);
                int new_cost = dp[mask][u] + dist[u + 1][v + 1];
                dp[next_mask][v] = min(dp[next_mask][v], new_cost);
            }
        }
    }

    int full_mask = limit - 1;
    int best = INF;
    for (int i = 0; i < K; i++) {
        best = min(best, dp[full_mask][i] + dist[i + 1][K + 1]);
    }

    cout << best << "\n";
    return 0;
}
