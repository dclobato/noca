import * as readline from 'readline';

const rl = readline.createInterface({ input: process.stdin });
const lines: string[] = [];

rl.on('line', (line: string) => lines.push(line.trim()));
rl.on('close', () => {
    const tokens: string[] = lines.join(' ').split(/\s+/).filter((t) => t.length > 0);
    let pos = 0;
    const nextInt = (): number => parseInt(tokens[pos++]);

    const L: number = nextInt();
    const C: number = nextInt();
    const K: number = nextInt();

    const startX: number = nextInt();
    const startY: number = nextInt();
    const endX: number = nextInt();
    const endY: number = nextInt();

    const houses: [number, number][] = [];
    for (let i = 0; i < K; i++) {
        houses.push([nextInt(), nextInt()]);
    }

    const manhattan = (a: [number, number], b: [number, number]): number =>
        Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);

    const start: [number, number] = [startX, startY];
    const end: [number, number] = [endX, endY];

    if (K === 0) {
        console.log(manhattan(start, end));
        return;
    }

    // pts: [0: start, 1..K: houses, K+1: end]
    const pts: [number, number][] = [start, ...houses, end];
    const n: number = pts.length;

    const dist: number[][] = Array.from({ length: n }, (_, i) =>
        Array.from({ length: n }, (_, j) => manhattan(pts[i], pts[j]))
    );

    const INF = 1000000000;
    const limit: number = 1 << K;
    const dp: number[][] = Array.from({ length: limit }, () => new Array(K).fill(INF));

    for (let i = 0; i < K; i++) {
        dp[1 << i][i] = dist[0][i + 1];
    }

    for (let mask = 0; mask < limit; mask++) {
        for (let u = 0; u < K; u++) {
            if (!(mask & (1 << u))) continue;
            if (dp[mask][u] === INF) continue;
            for (let v = 0; v < K; v++) {
                if (mask & (1 << v)) continue;
                const nextMask: number = mask | (1 << v);
                const newCost: number = dp[mask][u] + dist[u + 1][v + 1];
                if (newCost < dp[nextMask][v]) {
                    dp[nextMask][v] = newCost;
                }
            }
        }
    }

    const fullMask: number = limit - 1;
    let best: number = INF;
    for (let i = 0; i < K; i++) {
        const cost: number = dp[fullMask][i] + dist[i + 1][K + 1];
        if (cost < best) best = cost;
    }

    console.log(best);
});
