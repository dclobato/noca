const readline = require('readline');

const rl = readline.createInterface({ input: process.stdin });
const lines = [];

rl.on('line', (line) => lines.push(line.trim()));
rl.on('close', () => {
    const tokens = lines.join(' ').split(/\s+/).filter((t) => t.length > 0);
    let pos = 0;
    const nextInt = () => parseInt(tokens[pos++]);

    const L = nextInt(), C = nextInt(), K = nextInt();
    const startX = nextInt(), startY = nextInt();
    const endX = nextInt(), endY = nextInt();

    const houses = [];
    for (let i = 0; i < K; i++) {
        houses.push([nextInt(), nextInt()]);
    }

    const manhattan = (a, b) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);

    const start = [startX, startY];
    const end = [endX, endY];

    if (K === 0) {
        console.log(manhattan(start, end));
        return;
    }

    // pts: [0: start, 1..K: houses, K+1: end]
    const pts = [start, ...houses, end];
    const n = pts.length;

    const dist = Array.from({ length: n }, (_, i) =>
        Array.from({ length: n }, (_, j) => manhattan(pts[i], pts[j]))
    );

    const INF = 1000000000;
    const limit = 1 << K;
    const dp = Array.from({ length: limit }, () => new Array(K).fill(INF));

    for (let i = 0; i < K; i++) {
        dp[1 << i][i] = dist[0][i + 1];
    }

    for (let mask = 0; mask < limit; mask++) {
        for (let u = 0; u < K; u++) {
            if (!(mask & (1 << u))) continue;
            if (dp[mask][u] === INF) continue;
            for (let v = 0; v < K; v++) {
                if (mask & (1 << v)) continue;
                const nextMask = mask | (1 << v);
                const newCost = dp[mask][u] + dist[u + 1][v + 1];
                if (newCost < dp[nextMask][v]) {
                    dp[nextMask][v] = newCost;
                }
            }
        }
    }

    const fullMask = limit - 1;
    let best = INF;
    for (let i = 0; i < K; i++) {
        const cost = dp[fullMask][i] + dist[i + 1][K + 1];
        if (cost < best) best = cost;
    }

    console.log(best);
});
