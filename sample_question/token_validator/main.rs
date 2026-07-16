//  NOCA -- Next Online Contest Administrator
//  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
//  This program is distributed in the hope that it will be useful,
//  but WITHOUT ANY WARRANTY; without even the implied warranty of
//  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

use std::io::{self, Read};

#[derive(Clone, Copy)]
struct Point {
    x: i32,
    y: i32,
}

fn manhattan(a: Point, b: Point) -> i32 {
    (a.x - b.x).abs() + (a.y - b.y).abs()
}

fn min_route_cost(start: Point, houses: &[Point], end: Point) -> i32 {
    let house_count = houses.len();
    if house_count == 0 {
        return manhattan(start, end);
    }

    let mut points = Vec::with_capacity(house_count + 2);
    points.push(start);
    points.extend_from_slice(houses);
    points.push(end);

    let mut dist = vec![vec![0; points.len()]; points.len()];
    for i in 0..points.len() {
        for j in 0..points.len() {
            dist[i][j] = manhattan(points[i], points[j]);
        }
    }

    let limit = 1_usize << house_count;
    let inf = i32::MAX / 4;
    let mut dp = vec![vec![inf; house_count]; limit];

    for i in 0..house_count {
        dp[1_usize << i][i] = dist[0][i + 1];
    }

    for mask in 0..limit {
        for u in 0..house_count {
            if mask & (1_usize << u) == 0 || dp[mask][u] == inf {
                continue;
            }
            for v in 0..house_count {
                if mask & (1_usize << v) != 0 {
                    continue;
                }
                let next_mask = mask | (1_usize << v);
                let new_cost = dp[mask][u] + dist[u + 1][v + 1];
                if new_cost < dp[next_mask][v] {
                    dp[next_mask][v] = new_cost;
                }
            }
        }
    }

    let full_mask = limit - 1;
    let mut best = inf;
    for i in 0..house_count {
        best = best.min(dp[full_mask][i] + dist[i + 1][house_count + 1]);
    }

    best
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut values = input
        .split_whitespace()
        .map(|value| value.parse::<i32>().unwrap());

    let _rows = match values.next() {
        Some(value) => value,
        None => return,
    };
    let _columns = values.next().unwrap();
    let house_count = values.next().unwrap() as usize;

    let start = Point {
        x: values.next().unwrap(),
        y: values.next().unwrap(),
    };
    let end = Point {
        x: values.next().unwrap(),
        y: values.next().unwrap(),
    };

    let mut houses = Vec::with_capacity(house_count);
    for _ in 0..house_count {
        houses.push(Point {
            x: values.next().unwrap(),
            y: values.next().unwrap(),
        });
    }

    println!("{}", min_route_cost(start, &houses, end));
}
