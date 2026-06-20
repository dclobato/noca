--  NOCA -- Next Online Contest Administrator
--  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
--  This program is distributed in the hope that it will be useful,
--  but WITHOUT ANY WARRANTY; without even the implied warranty of
--  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

local function read_numbers()
    local values = {}
    for value in io.read("*line"):gmatch("%S+") do
        values[#values + 1] = tonumber(value)
    end
    return values
end

local function manhattan(a, b)
    return math.abs(a.x - b.x) + math.abs(a.y - b.y)
end

local function has_bit(mask, bit_value)
    return math.floor(mask / bit_value) % 2 == 1
end

local first_line = read_numbers()
local k = first_line[3]
local start_line = read_numbers()
local end_line = read_numbers()

local start_point = { x = start_line[1], y = start_line[2] }
local end_point = { x = end_line[1], y = end_line[2] }
local points = { start_point }

for _ = 1, k do
    local house_line = read_numbers()
    points[#points + 1] = { x = house_line[1], y = house_line[2] }
end

points[#points + 1] = end_point

if k == 0 then
    print(manhattan(start_point, end_point))
    os.exit(0)
end

local dist = {}
for i = 1, #points do
    dist[i] = {}
    for j = 1, #points do
        dist[i][j] = manhattan(points[i], points[j])
    end
end

local inf = 1000000000
local limit = 2 ^ k
local dp = {}

for mask = 0, limit - 1 do
    dp[mask] = {}
    for i = 1, k do
        dp[mask][i] = inf
    end
end

for i = 1, k do
    dp[2 ^ (i - 1)][i] = dist[1][i + 1]
end

for mask = 0, limit - 1 do
    for u = 1, k do
        local u_bit = 2 ^ (u - 1)
        if has_bit(mask, u_bit) and dp[mask][u] ~= inf then
            for v = 1, k do
                local v_bit = 2 ^ (v - 1)
                if not has_bit(mask, v_bit) then
                    local next_mask = mask + v_bit
                    local cost = dp[mask][u] + dist[u + 1][v + 1]
                    if cost < dp[next_mask][v] then
                        dp[next_mask][v] = cost
                    end
                end
            end
        end
    end
end

local full_mask = limit - 1
local answer = inf

for i = 1, k do
    local cost = dp[full_mask][i] + dist[i + 1][k + 2]
    if cost < answer then
        answer = cost
    end
end

print(answer)
