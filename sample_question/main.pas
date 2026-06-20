program RouteOptimizer;

uses Math;

const
  MAX_K = 20; { Supporting slightly more than 10 just in case }
  INF = 1000000000;

type
  TPoint = record
    x, y: LongInt;
  end;

var
  L, C, K: LongInt;
  startPt, endPt: TPoint;
  houses: array[0..MAX_K] of TPoint;
  allPoints: array[0..MAX_K + 2] of TPoint;
  dist: array[0..MAX_K + 2, 0..MAX_K + 2] of LongInt;
  dp: array[0..(1 shl 16), 0..MAX_K] of LongInt;
  i, j, u, v: LongInt;
  mask, nextMask: LongInt;
  limit, fullMask: LongInt;
  cost, finalAns: LongInt;

function Manhattan(p1, p2: TPoint): LongInt;
begin
  Manhattan := Abs(p1.x - p2.x) + Abs(p1.y - p2.y);
end;

begin
  ReadLn(L, C, K);
  ReadLn(startPt.x, startPt.y);
  ReadLn(endPt.x, endPt.y);

  for i := 0 to K - 1 do
    ReadLn(houses[i].x, houses[i].y);

  if K = 0 then
  begin
    WriteLn(Manhattan(startPt, endPt));
    Exit;
  end;

  { Construct all points array }
  allPoints[0] := startPt;
  for i := 0 to K - 1 do
    allPoints[i + 1] := houses[i];
  allPoints[K + 1] := endPt;

  { Calculate distances }
  for i := 0 to K + 1 do
    for j := 0 to K + 1 do
      dist[i][j] := Manhattan(allPoints[i], allPoints[j]);

  limit := 1 shl K;
  
  { Initialize DP table with Infinity }
  for mask := 0 to limit - 1 do
    for i := 0 to K - 1 do
      dp[mask][i] := INF;

  { Base case: Start (0) to House (i+1) }
  for i := 0 to K - 1 do
    dp[1 shl i][i] := dist[0][i + 1];

  { Main DP Loop }
  for mask := 0 to limit - 1 do
  begin
    for u := 0 to K - 1 do
    begin
      { If bit u is not set in mask, continue }
      if (mask and (1 shl u)) = 0 then Continue;
      
      { If this state is unreachable, continue }
      if dp[mask][u] = INF then Continue;

      for v := 0 to K - 1 do
      begin
        { If bit v is already set, continue }
        if (mask and (1 shl v)) <> 0 then Continue;

        nextMask := mask or (1 shl v);
        cost := dp[mask][u] + dist[u + 1][v + 1];

        if cost < dp[nextMask][v] then
          dp[nextMask][v] := cost;
      end;
    end;
  end;

  { Final calculation to destination }
  fullMask := limit - 1;
  finalAns := INF;

  for i := 0 to K - 1 do
  begin
    cost := dp[fullMask][i] + dist[i + 1][K + 1];
    if cost < finalAns then
      finalAns := cost;
  end;

  WriteLn(finalAns);
end.
