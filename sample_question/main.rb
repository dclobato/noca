#!/usr/bin/env ruby
# frozen_string_literal: true

# Tradução em Ruby do programa original em C: menor rota que parte da estação,
# visita todas as casas exatamente uma vez e termina no destino, sob métrica de
# Manhattan. Resolvido por Held-Karp (TSP com programação dinâmica por máscara
# de bits), em tempo O(2^K * K^2) e memória O(2^K * K).

Ponto = Struct.new(:x, :y)

def manhattan(a, b)
  (a.x - b.x).abs + (a.y - b.y).abs
end

# Calcula a menor rota e o caminho ótimo. O caminho é devolvido como índices
# no vetor [estacao, casas..., destino] (0 = estação, K+1 = destino).
def calcula_menor_rota(estacao, casas, k, destino)
  return [manhattan(estacao, destino), [0, 1]] if k.zero?

  pontos = [estacao, *casas, destino]
  total = k + 2

  # Matriz de distâncias completa.
  dist = Array.new(total) { |i| Array.new(total) { |j| manhattan(pontos[i], pontos[j]) } }

  # dp[mask][i]     -> menor custo de um caminho que parte da estação, cobre
  #                    exatamente as casas em `mask` e termina na casa i.
  # parent[mask][i] -> casa predecessora de i, usada na reconstrução.
  qtd_mascaras = 1 << k
  dp = Array.new(qtd_mascaras) { Array.new(k, Float::INFINITY) }
  parent = Array.new(qtd_mascaras) { Array.new(k, -1) }

  # Inicialização: ir direto da estação (índice 0) para cada casa i.
  (0...k).each { |i| dp[1 << i][i] = dist[0][i + 1] }

  # Transições: estender cada estado alcançável com uma casa ainda não visitada.
  (0...qtd_mascaras).each do |mask|
    (0...k).each do |u|
      next if mask[u].zero?              # bit u presente em mask?
      custo_atual = dp[mask][u]
      next if custo_atual.infinite?

      (0...k).each do |v|
        next unless mask[v].zero?        # v ainda não visitada
        nova_mask = mask | (1 << v)
        novo_custo = custo_atual + dist[u + 1][v + 1]
        if novo_custo < dp[nova_mask][v]
          dp[nova_mask][v] = novo_custo
          parent[nova_mask][v] = u
        end
      end
    end
  end

  # Fechamento: da última casa visitada seguir até o destino (índice K+1).
  mascara_final = qtd_mascaras - 1
  melhor = Float::INFINITY
  ultima = -1
  (0...k).each do |i|
    next if dp[mascara_final][i].infinite?
    custo = dp[mascara_final][i] + dist[i + 1][k + 1]
    if custo < melhor
      melhor = custo
      ultima = i
    end
  end

  # Reconstrução do caminho seguindo os ponteiros de predecessor.
  mask = mascara_final
  ordem = []
  atual = ultima
  while atual != -1
    ordem << (atual + 1)               # índice no vetor `pontos`
    anterior = parent[mask][atual]
    mask ^= (1 << atual)
    atual = anterior
  end
  ordem.reverse!

  caminho = [0, *ordem, k + 1]         # estação, casas ótimas, destino
  [melhor, caminho]
end

# --- Programa principal ---------------------------------------------------

# Lê todos os inteiros da entrada padrão de uma vez, reproduzindo a semântica
# do scanf, que consome números separados por qualquer espaçamento.
numeros = $stdin.read.split.map(&:to_i).each

_l = numeros.next
_c = numeros.next
k  = numeros.next

estacao = Ponto.new(numeros.next, numeros.next)
destino = Ponto.new(numeros.next, numeros.next)
casas = Array.new(k) { Ponto.new(numeros.next, numeros.next) }

custo, _caminho = calcula_menor_rota(estacao, casas, k, destino)
puts custo