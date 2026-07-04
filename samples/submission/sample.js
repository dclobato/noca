function manhattan(x1, y1, x2, y2) {
  return Math.abs(x1 - x2) + Math.abs(y1 - y2);
}

function solve(input) {
  const trimmed = input.trim();
  if (!trimmed) {
    return "";
  }

  const tokens = trimmed.split(/\s+/).map(Number);
  let index = 0;

  const L = tokens[index++];
  const C = tokens[index++];
  const K = tokens[index++];
  void L;
  void C;

  const xe = tokens[index++];
  const ye = tokens[index++];
  const xc = tokens[index++];
  const yc = tokens[index++];

  const mandatory = [];
  for (let i = 0; i < K; i += 1) {
    mandatory.push([tokens[index++], tokens[index++]]);
  }

  if (K === 0) {
    return `${manhattan(xe, ye, xc, yc)}\n`;
  }

  const points = [[xe, ye], ...mandatory, [xc, yc]];
  const pointCount = points.length;
  const dist = Array.from({ length: pointCount }, () => Array(pointCount).fill(0));

  for (let i = 0; i < pointCount; i += 1) {
    for (let j = 0; j < pointCount; j += 1) {
      dist[i][j] = manhattan(points[i][0], points[i][1], points[j][0], points[j][1]);
    }
  }

  const fullMask = 1 << K;
  const dp = Array.from({ length: fullMask }, () => Array(K).fill(Number.POSITIVE_INFINITY));

  for (let i = 0; i < K; i += 1) {
    dp[1 << i][i] = dist[0][i + 1];
  }

  for (let mask = 0; mask < fullMask; mask += 1) {
    for (let u = 0; u < K; u += 1) {
      if ((mask & (1 << u)) === 0) {
        continue;
      }
      for (let v = 0; v < K; v += 1) {
        if ((mask & (1 << v)) !== 0) {
          continue;
        }
        const nextMask = mask | (1 << v);
        dp[nextMask][v] = Math.min(dp[nextMask][v], dp[mask][u] + dist[u + 1][v + 1]);
      }
    }
  }

  let answer = Number.POSITIVE_INFINITY;
  const lastMask = fullMask - 1;
  for (let i = 0; i < K; i += 1) {
    answer = Math.min(answer, dp[lastMask][i] + dist[i + 1][K + 1]);
  }

  return `${answer}\n`;
}

const fs = require("node:fs");
const input = fs.readFileSync(0, "utf8");
process.stdout.write(solve(input));
