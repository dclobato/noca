#!/usr/bin/env bash
# =============================================================================
# menor_rota.sh — Versão Bash do programa C de menor rota por Held-Karp.
#
# Implementa o algoritmo de programação dinâmica sobre máscaras de bits (TSP)
# com extremos fixos, usando exclusivamente recursos nativos do Bash:
#   - Arrays associativos para dp[], parent[] e dist[]
#   - Aritmética inteira com $(( ))
#   - Sem subshells em funções críticas (estado compartilhado via variáveis globais)
#
# Complexidade: O(2^K * K^2) em tempo, O(2^K * K) em memória.
# Requisito mínimo: Bash 4.0 (arrays associativos).
# =============================================================================

set -euo pipefail

# -----------------------------------------------------------------------------
# Constantes
# -----------------------------------------------------------------------------
readonly INF=2147483647

# -----------------------------------------------------------------------------
# Leitura da entrada completa em tokens (equivalente ao scanf)
# -----------------------------------------------------------------------------
_all_input=$(cat)
tokens=( $_all_input )
_idx=0

# next_int: escreve o próximo token inteiro em _val (sem subshell)
next_int() {
    _val="${tokens[$_idx]}"
    (( _idx++ )) || true
}

# -----------------------------------------------------------------------------
# manhattan: escreve resultado em _mh (sem subshell, sem echo)
# Uso: manhattan ax ay bx by
# -----------------------------------------------------------------------------
manhattan() {
    local dx=$(( $1 - $3 ))
    local dy=$(( $2 - $4 ))
    (( dx < 0 )) && (( dx = -dx )) || true
    (( dy < 0 )) && (( dy = -dy )) || true
    _mh=$(( dx + dy ))
}

# -----------------------------------------------------------------------------
# calcula_menor_rota
# Lê das variáveis globais: K, estacao_x/y, destino_x/y, casa_x[], casa_y[]
# Imprime o custo mínimo em stdout.
# -----------------------------------------------------------------------------
calcula_menor_rota() {

    # ------------------------------------------------------------------
    # Caso degenerado: K=0
    # ------------------------------------------------------------------
    if (( K == 0 )); then
        manhattan "$estacao_x" "$estacao_y" "$destino_x" "$destino_y"
        echo "$_mh"
        return
    fi

    # ------------------------------------------------------------------
    # Vetor de pontos:
    #   0      → estação
    #   1..K   → casas
    #   K+1    → destino
    # ------------------------------------------------------------------
    local total=$(( K + 2 ))
    local -a px py
    px[0]=$estacao_x;   py[0]=$estacao_y
    local i
    for (( i = 0; i < K; i++ )); do
        px[$(( i+1 ))]=${casa_x[$i]}
        py[$(( i+1 ))]=${casa_y[$i]}
    done
    px[$(( K+1 ))]=$destino_x
    py[$(( K+1 ))]=$destino_y

    # ------------------------------------------------------------------
    # Matriz de distâncias (array associativo, chave "i,j")
    # ------------------------------------------------------------------
    local -A dist
    local j
    for (( i = 0; i < total; i++ )); do
        for (( j = 0; j < total; j++ )); do
            manhattan "${px[$i]}" "${py[$i]}" "${px[$j]}" "${py[$j]}"
            dist[$i,$j]=$_mh
        done
    done

    # ------------------------------------------------------------------
    # Tabelas de programação dinâmica (arrays associativos, chave "mask,i")
    # ------------------------------------------------------------------
    local -A dp parent
    local qtd_mascaras=$(( 1 << K ))
    local mask u v new_mask cost custo_atual

    for (( mask = 0; mask < qtd_mascaras; mask++ )); do
        for (( u = 0; u < K; u++ )); do
            dp[$mask,$u]=$INF
            parent[$mask,$u]=-1
        done
    done

    # Inicialização: da estação direto para cada casa i
    for (( i = 0; i < K; i++ )); do
        dp[$(( 1<<i )),$i]=${dist[0,$(( i+1 ))]}
    done

    # ------------------------------------------------------------------
    # Transições
    # ------------------------------------------------------------------
    for (( mask = 0; mask < qtd_mascaras; mask++ )); do
        for (( u = 0; u < K; u++ )); do
            (( mask & (1 << u) )) || continue
            custo_atual=${dp[$mask,$u]}
            (( custo_atual == INF )) && continue

            for (( v = 0; v < K; v++ )); do
                (( mask & (1 << v) )) && continue
                new_mask=$(( mask | (1 << v) ))
                cost=$(( custo_atual + dist[$(( u+1 )),$(( v+1 ))] ))
                if (( cost < dp[$new_mask,$v] )); then
                    dp[$new_mask,$v]=$cost
                    parent[$new_mask,$v]=$u
                fi
            done
        done
    done

    # ------------------------------------------------------------------
    # Fechamento: última casa → destino
    # ------------------------------------------------------------------
    local final_mask=$(( qtd_mascaras - 1 ))
    local best=$INF ultima=-1 c

    for (( i = 0; i < K; i++ )); do
        (( dp[$final_mask,$i] == INF )) && continue
        c=$(( dp[$final_mask,$i] + dist[$(( i+1 )),$(( K+1 ))] ))
        if (( c < best )); then
            best=$c
            ultima=$i
        fi
    done

    echo "$best"
}

# =============================================================================
# Programa principal
# =============================================================================
next_int; L=$_val
next_int; C=$_val
next_int; K=$_val

next_int; estacao_x=$_val
next_int; estacao_y=$_val
next_int; destino_x=$_val
next_int; destino_y=$_val

declare -a casa_x casa_y
for (( i = 0; i < K; i++ )); do
    next_int; casa_x[$i]=$_val
    next_int; casa_y[$i]=$_val
done

calcula_menor_rota
