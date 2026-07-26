<?php

// Minimum-cost route: start -> all K mandatory houses (any order) -> end,
// using Manhattan distance. Solved with a Held-Karp bitmask DP.

$tokens = preg_split('/\s+/', trim(stream_get_contents(STDIN)), -1, PREG_SPLIT_NO_EMPTY);
$pos = 0;

function nextInt(): int
{
    global $tokens, $pos;
    return (int) $tokens[$pos++];
}

function manhattan(array $a, array $b): int
{
    return abs($a[0] - $b[0]) + abs($a[1] - $b[1]);
}

$L = nextInt();
$C = nextInt();
$K = nextInt();

$start = [nextInt(), nextInt()];
$end = [nextInt(), nextInt()];

$houses = [];
for ($i = 0; $i < $K; $i++) {
    $houses[] = [nextInt(), nextInt()];
}

if ($K === 0) {
    echo manhattan($start, $end), "\n";
    exit(0);
}

// pts: [0: start, 1..K: houses, K+1: end]
$pts = array_merge([$start], $houses, [$end]);
$n = count($pts);

$dist = [];
for ($i = 0; $i < $n; $i++) {
    for ($j = 0; $j < $n; $j++) {
        $dist[$i][$j] = manhattan($pts[$i], $pts[$j]);
    }
}

$INF = 1000000000;
$limit = 1 << $K;
$dp = array_fill(0, $limit, array_fill(0, $K, $INF));

for ($i = 0; $i < $K; $i++) {
    $dp[1 << $i][$i] = $dist[0][$i + 1];
}

for ($mask = 0; $mask < $limit; $mask++) {
    for ($u = 0; $u < $K; $u++) {
        if (!($mask & (1 << $u))) {
            continue;
        }
        if ($dp[$mask][$u] === $INF) {
            continue;
        }
        for ($v = 0; $v < $K; $v++) {
            if ($mask & (1 << $v)) {
                continue;
            }
            $nextMask = $mask | (1 << $v);
            $newCost = $dp[$mask][$u] + $dist[$u + 1][$v + 1];
            if ($newCost < $dp[$nextMask][$v]) {
                $dp[$nextMask][$v] = $newCost;
            }
        }
    }
}

$fullMask = $limit - 1;
$best = $INF;
for ($i = 0; $i < $K; $i++) {
    $best = min($best, $dp[$fullMask][$i] + $dist[$i + 1][$K + 1]);
}

echo $best, "\n";
