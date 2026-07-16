def manhattan(p1, p2):
    return abs(p1[0] - p2[0]) + abs(p1[1] - p2[1])


def solve():
    # Leitura da entrada
    L, C, K = map(int, input().split())
    Xe, Ye = map(int, input().split())
    Xc, Yc = map(int, input().split())
    obrigatorias = [tuple(map(int, input().split())) for _ in range(K)]

    if K == 0:
        final_dist = manhattan((Xe, Ye), (Xc, Yc))
        print(final_dist)
        return

    # Pontos de interesse: estação, casas obrigatórias, casinha final
    pontos = [(Xe, Ye)] + obrigatorias + [(Xc, Yc)]
    n = len(pontos)

    # Matriz de distâncias de Manhattan entre todos os pontos
    dist = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            dist[i][j] = manhattan(pontos[i], pontos[j])

    # DP com bitmask: dp[mascara][i] = custo mínimo para visitar os pontos da máscara,
    # terminando no ponto i (i de 1 até K)
    dp = [[float("inf")] * (K + 1) for _ in range(1 << K)]

    # Inicialização: partindo da estação (índice 0), indo para uma casa obrigatória i
    for i in range(K):
        dp[1 << i][i] = dist[0][i + 1]  # ponto 0 -> ponto i+1

    # Preenchimento da DP
    for mask in range(1 << K):
        for u in range(K):
            if not (mask & (1 << u)):
                continue
            for v in range(K):
                if mask & (1 << v):
                    continue
                next_mask = mask | (1 << v)
                dp[next_mask][v] = min(dp[next_mask][v], dp[mask][u] + dist[u + 1][v + 1])

    # Agora, calcular o mínimo custo de um caminho que termina na casinha final (ponto K+1)
    final_dist = float("inf")
    for i in range(K):
        final_dist = min(final_dist, dp[(1 << K) - 1][i] + dist[i + 1][K + 1])

    print(final_dist)


# Para uso local com arquivos de teste ou input manual:
if __name__ == "__main__":
    solve()
