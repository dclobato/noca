//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
)

type Point struct {
	x int
	y int
}

func abs(value int) int {
	if value < 0 {
		return -value
	}
	return value
}

func manhattan(a Point, b Point) int {
	return abs(a.x-b.x) + abs(a.y-b.y)
}

func minRouteCost(start Point, houses []Point, end Point) int {
	houseCount := len(houses)
	if houseCount == 0 {
		return manhattan(start, end)
	}

	points := make([]Point, 0, houseCount+2)
	points = append(points, start)
	points = append(points, houses...)
	points = append(points, end)

	dist := make([][]int, len(points))
	for i := range points {
		dist[i] = make([]int, len(points))
		for j := range points {
			dist[i][j] = manhattan(points[i], points[j])
		}
	}

	limit := 1 << houseCount
	dp := make([][]int, limit)
	for mask := range dp {
		dp[mask] = make([]int, houseCount)
		for i := range dp[mask] {
			dp[mask][i] = math.MaxInt / 4
		}
	}

	for i := range houseCount {
		dp[1<<i][i] = dist[0][i+1]
	}

	for mask := range limit {
		for u := range houseCount {
			if mask&(1<<u) == 0 || dp[mask][u] == math.MaxInt/4 {
				continue
			}
			for v := range houseCount {
				if mask&(1<<v) != 0 {
					continue
				}
				nextMask := mask | (1 << v)
				newCost := dp[mask][u] + dist[u+1][v+1]
				if newCost < dp[nextMask][v] {
					dp[nextMask][v] = newCost
				}
			}
		}
	}

	fullMask := limit - 1
	best := math.MaxInt / 4
	for i := range houseCount {
		cost := dp[fullMask][i] + dist[i+1][houseCount+1]
		if cost < best {
			best = cost
		}
	}

	return best
}

func main() {
	reader := bufio.NewReader(os.Stdin)
	var rows, columns, houseCount int
	if _, err := fmt.Fscan(reader, &rows, &columns, &houseCount); err != nil {
		return
	}

	var start, end Point
	fmt.Fscan(reader, &start.x, &start.y)
	fmt.Fscan(reader, &end.x, &end.y)

	houses := make([]Point, houseCount)
	for i := range houseCount {
		fmt.Fscan(reader, &houses[i].x, &houses[i].y)
	}

	fmt.Println(minRouteCost(start, houses, end))
	_ = rows
	_ = columns
}
