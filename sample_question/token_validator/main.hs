--  NOCA -- Next Online Contest Administrator
--  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
--  This program is distributed in the hope that it will be useful,
--  but WITHOUT ANY WARRANTY; without even the implied warranty of
--  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import Data.Array (Array, (!), array, bounds)
import Data.Bits ((.&.), complement, shiftL)

type Point = (Int, Int)

type Distances = Array (Int, Int) Int

type Dp = Array (Int, Int) Int

inf :: Int
inf = 1000000000

manhattan :: Point -> Point -> Int
manhattan (x1, y1) (x2, y2) = abs (x1 - x2) + abs (y1 - y2)

buildDistances :: [Point] -> Distances
buildDistances points =
    array ((0, 0), (n - 1, n - 1))
        [((i, j), manhattan (points !! i) (points !! j)) | i <- [0 .. n - 1], j <- [0 .. n - 1]]
  where
    n = length points

bit :: Int -> Int
bit index = 1 `shiftL` index

isSet :: Int -> Int -> Bool
isSet mask index = (mask .&. bit index) /= 0

minimumOrInf :: [Int] -> Int
minimumOrInf [] = inf
minimumOrInf values = minimum values

dpValue :: Distances -> Dp -> Int -> Int -> Int
dpValue dist dp mask lastPoint
    | mask == bit lastPoint = dist ! (0, lastPoint + 1)
    | otherwise = minimumOrInf previousCosts
  where
    previousMask = mask .&. complement (bit lastPoint)
    previousCosts =
        [ dp ! (previousMask, previousPoint) + dist ! (previousPoint + 1, lastPoint + 1)
        | previousPoint <- [0 .. snd (snd (bounds dp))]
        , isSet previousMask previousPoint
        ]

buildDp :: Int -> Distances -> Dp
buildDp k dist = dp
  where
    limit = bit k
    dp =
        array
            ((1, 0), (limit - 1, k - 1))
            [ ((mask, lastPoint), dpValue dist dp mask lastPoint)
            | mask <- [1 .. limit - 1]
            , lastPoint <- [0 .. k - 1]
            , isSet mask lastPoint
            ]

solve :: [Int] -> Int
solve values =
    if k == 0
        then manhattan startPoint endPoint
        else minimum finalCosts
  where
    k = values !! 2
    startPoint = (values !! 3, values !! 4)
    endPoint = (values !! 5, values !! 6)
    houseValues = take (2 * k) (drop 7 values)
    houses = toPoints houseValues
    points = startPoint : houses ++ [endPoint]
    dist = buildDistances points
    dp = buildDp k dist
    fullMask = bit k - 1
    finalCosts = [dp ! (fullMask, i) + dist ! (i + 1, k + 1) | i <- [0 .. k - 1]]

toPoints :: [Int] -> [Point]
toPoints [] = []
toPoints (x : y : rest) = (x, y) : toPoints rest
toPoints _ = []

main :: IO ()
main = do
    contents <- getContents
    print (solve (map read (words contents)))
