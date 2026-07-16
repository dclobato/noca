#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#define MAXK 10
#define MAX_MASK (1 << MAXK)

typedef struct {
    int x, y;
} Ponto;

int manhattan(Ponto a, Ponto b) {
    return abs(a.x - b.x) + abs(a.y - b.y);
}

int min(int a, int b) {
    return a < b ? a : b;
}

int dist[MAXK + 2][MAXK + 2];
int dp[MAX_MASK][MAXK];
int parent[MAX_MASK][MAXK];

void calcula_distancias(Ponto *pontos, int total) {
    for (int i = 0; i < total; i++) {
        for (int j = 0; j < total; j++) {
            dist[i][j] = manhattan(pontos[i], pontos[j]);
        }
    }
}

int calcula_menor_rota(Ponto estacao, Ponto *casas, int K, Ponto destino, int *melhor_caminho) {
    int total = K + 2;
    Ponto pontos[MAXK + 2];
    pontos[0] = estacao;
    if (K == 0) {
        pontos[1] = destino;
        int custo = manhattan(estacao, destino);
        return custo;
    }

    for (int i = 0; i < K; i++) pontos[i + 1] = casas[i];
    pontos[K + 1] = destino;

    calcula_distancias(pontos, total);

    for (int m = 0; m < (1 << K); m++) {
        for (int i = 0; i < K; i++) {
            dp[m][i] = INT_MAX;
            parent[m][i] = -1;
        }
    }

    for (int i = 0; i < K; i++) {
        dp[1 << i][i] = dist[0][i + 1];
    }

    for (int mask = 0; mask < (1 << K); mask++) {
        for (int u = 0; u < K; u++) {
            if (!(mask & (1 << u))) continue;
            for (int v = 0; v < K; v++) {
                if (mask & (1 << v)) continue;
                int new_mask = mask | (1 << v);
                int new_cost = dp[mask][u] + dist[u + 1][v + 1];
                if (new_cost < dp[new_mask][v]) {
                    dp[new_mask][v] = new_cost;
                    parent[new_mask][v] = u;
                }
            }
        }
    }

    int final_mask = (1 << K) - 1;
    int best = INT_MAX;
    int last = -1;
    for (int i = 0; i < K; i++) {
        int cost = dp[final_mask][i] + dist[i + 1][K + 1];
        if (cost < best) {
            best = cost;
            last = i;
        }
    }

    // Reconstrução do caminho
    int mask = final_mask;
    int caminho[K + 2];
    int len = 0;
    caminho[len++] = 0; // estação

    int path_indices[K];
    while (last != -1) {
        path_indices[len - 1] = last + 1;
        int prev = parent[mask][last];
        mask ^= (1 << last);
        last = prev;
        len++;
    }

    for (int i = len - 2; i >= 0; i--) {
        caminho[len - i - 1] = path_indices[i];
    }
    caminho[len++] = K + 1; // destino

    for (int i = 0; i < len; i++) {
        melhor_caminho[i] = caminho[i];
    }

    return best;
}

int main() {
    int L, C, K;
    scanf("%d %d %d", &L, &C, &K);

    Ponto estacao, destino, casas[MAXK];
    scanf("%d %d", &estacao.x, &estacao.y);
    scanf("%d %d", &destino.x, &destino.y);

    for (int i = 0; i < K; i++) {
        scanf("%d %d", &casas[i].x, &casas[i].y);
    }

    int caminho[MAXK + 2];
    int custo = calcula_menor_rota(estacao, casas, K, destino, caminho);

    printf("%d\n", custo);

    return 0;
}
