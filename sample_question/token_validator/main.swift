import Foundation

// MARK: - Modelo

struct Ponto {
    var x: Int
    var y: Int
}

@inline(__always)
func manhattan(_ a: Ponto, _ b: Ponto) -> Int {
    return abs(a.x - b.x) + abs(a.y - b.y)
}

// MARK: - Leitura de inteiros separados por espaço (semântica equivalente a scanf("%d"))

/// Lê toda a entrada padrão de uma vez e a tokeniza por espaços em branco,
/// reproduzindo o comportamento do scanf, que ignora quebras de linha e espaços
/// indistintamente ao consumir inteiros sucessivos.
final class LeitorInteiros {
    private let tokens: [Substring]
    private var indice = 0

    init() {
        let dados = FileHandle.standardInput.readDataToEndOfFile()
        let texto = String(data: dados, encoding: .utf8) ?? ""
        tokens = texto.split(whereSeparator: { $0 == " " || $0 == "\n" || $0 == "\t" || $0 == "\r" })
    }

    func proximoInt() -> Int {
        defer { indice += 1 }
        return Int(tokens[indice]) ?? 0
    }
}

// MARK: - Núcleo: Held-Karp (TSP por máscara de bits) sobre distância de Manhattan

/// Calcula a menor rota que parte da `estacao`, visita todas as `casas`
/// exatamente uma vez e termina no `destino`, sob métrica de Manhattan.
/// Retorna o custo mínimo e o caminho reconstruído como índices no vetor
/// `[estacao, casas..., destino]` (0 = estação, K+1 = destino).
func calculaMenorRota(estacao: Ponto, casas: [Ponto], K: Int, destino: Ponto) -> (custo: Int, caminho: [Int]) {
    // Caso degenerado: nenhuma casa intermediária.
    if K == 0 {
        return (manhattan(estacao, destino), [0, 1])
    }

    // pontos = [estacao, casa_0, ..., casa_{K-1}, destino]
    var pontos: [Ponto] = [estacao]
    pontos.append(contentsOf: casas)
    pontos.append(destino)
    let total = K + 2

    // Matriz de distâncias completa.
    var dist = [[Int]](repeating: [Int](repeating: 0, count: total), count: total)
    for i in 0..<total {
        for j in 0..<total {
            dist[i][j] = manhattan(pontos[i], pontos[j])
        }
    }

    // dp[mask][i]  -> menor custo de um caminho que parte da estação, cobre
    //                 exatamente o conjunto de casas em `mask` e termina na casa i.
    // parent[mask][i] -> predecessora de i, para reconstrução.
    let qtdMascaras = 1 << K
    var dp = [[Int]](repeating: [Int](repeating: Int.max, count: K), count: qtdMascaras)
    var parent = [[Int]](repeating: [Int](repeating: -1, count: K), count: qtdMascaras)

    // Inicialização: ir direto da estação (índice 0) para cada casa i.
    for i in 0..<K {
        dp[1 << i][i] = dist[0][i + 1]
    }

    // Transições: estender cada estado alcançável adicionando uma casa ainda não visitada.
    for mask in 0..<qtdMascaras {
        for u in 0..<K {
            guard mask & (1 << u) != 0 else { continue }
            let custoAtual = dp[mask][u]
            guard custoAtual != Int.max else { continue } // evita overflow em estados inalcançáveis
            for v in 0..<K where mask & (1 << v) == 0 {
                let novaMask = mask | (1 << v)
                let novoCusto = custoAtual + dist[u + 1][v + 1]
                if novoCusto < dp[novaMask][v] {
                    dp[novaMask][v] = novoCusto
                    parent[novaMask][v] = u
                }
            }
        }
    }

    // Fechamento: da última casa visitada seguir até o destino (índice K+1).
    let mascaraFinal = qtdMascaras - 1
    var melhor = Int.max
    var ultima = -1
    for i in 0..<K where dp[mascaraFinal][i] != Int.max {
        let custo = dp[mascaraFinal][i] + dist[i + 1][K + 1]
        if custo < melhor {
            melhor = custo
            ultima = i
        }
    }

    // Reconstrução do caminho percorrendo os ponteiros de predecessor.
    var mask = mascaraFinal
    var ordem: [Int] = []
    var atual = ultima
    while atual != -1 {
        ordem.append(atual + 1) // índice no vetor `pontos`
        let anterior = parent[mask][atual]
        mask ^= (1 << atual)
        atual = anterior
    }
    ordem.reverse()

    var caminho: [Int] = [0]            // estação
    caminho.append(contentsOf: ordem)   // casas na ordem ótima
    caminho.append(K + 1)               // destino

    return (melhor, caminho)
}

// MARK: - Programa principal

let leitor = LeitorInteiros()

let L = leitor.proximoInt()
let C = leitor.proximoInt()
let K = leitor.proximoInt()
_ = (L, C) // dimensões do grid lidas, porém não utilizadas (como no original)

let estacao = Ponto(x: leitor.proximoInt(), y: leitor.proximoInt())
let destino = Ponto(x: leitor.proximoInt(), y: leitor.proximoInt())

var casas: [Ponto] = []
casas.reserveCapacity(K)
for _ in 0..<K {
    casas.append(Ponto(x: leitor.proximoInt(), y: leitor.proximoInt()))
}

let resultado = calculaMenorRota(estacao: estacao, casas: casas, K: K, destino: destino)
print(resultado.custo)

