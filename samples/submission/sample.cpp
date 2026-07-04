#include <algorithm>
#include <climits>
#include <cstdlib>
#include <iostream>
#include <vector>

using namespace std;

struct Ponto {
    int x;
    int y;
};

static int manhattan(const Ponto& a, const Ponto& b) {
    return abs(a.x - b.x) + abs(a.y - b.y);
}

static void calcula_distancias(const vector<Ponto>& pontos, vector<vector<int>>& dist) {
    const int total = static_cast<int>(pontos.size());
    for (int i = 0; i < total; ++i) {
        for (int j = 0; j < total; ++j) {
            dist[i][j] = manhattan(pontos[i], pontos[j]);
        }
    }
}

static int calcula_menor_rota(const Ponto& estacao, const vector<Ponto>& casas, const Ponto& destino) {
    const int k = static_cast<int>(casas.size());
    if (k == 0) {
        return manhattan(estacao, destino);
    }

    vector<Ponto> pontos;
    pontos.reserve(k + 2);
    pontos.push_back(estacao);
    for (const Ponto& casa : casas) {
        pontos.push_back(casa);
    }
    pontos.push_back(destino);

    vector<vector<int>> dist(k + 2, vector<int>(k + 2, 0));
    calcula_distancias(pontos, dist);

    const int full_mask = 1 << k;
    vector<vector<int>> dp(full_mask, vector<int>(k, INT_MAX));

    for (int i = 0; i < k; ++i) {
        dp[1 << i][i] = dist[0][i + 1];
    }

    for (int mask = 0; mask < full_mask; ++mask) {
        for (int u = 0; u < k; ++u) {
            if ((mask & (1 << u)) == 0 || dp[mask][u] == INT_MAX) {
                continue;
            }
            for (int v = 0; v < k; ++v) {
                if ((mask & (1 << v)) != 0) {
                    continue;
                }
                const int next_mask = mask | (1 << v);
                const int next_cost = dp[mask][u] + dist[u + 1][v + 1];
                dp[next_mask][v] = min(dp[next_mask][v], next_cost);
            }
        }
    }

    int best = INT_MAX;
    const int final_mask = full_mask - 1;
    for (int i = 0; i < k; ++i) {
        best = min(best, dp[final_mask][i] + dist[i + 1][k + 1]);
    }

    return best;
}

int main() {
    int l = 0;
    int c = 0;
    int k = 0;
    cin >> l >> c >> k;

    Ponto estacao{};
    Ponto destino{};
    cin >> estacao.x >> estacao.y;
    cin >> destino.x >> destino.y;

    vector<Ponto> casas(k);
    for (int i = 0; i < k; ++i) {
        cin >> casas[i].x >> casas[i].y;
    }

    cout << calcula_menor_rota(estacao, casas, destino) << '\n';
    return 0;
}
