import System.IO (hFlush, hPutStrLn, stdout, stderr)
import System.Exit (exitWith, ExitCode(..))

binarySearch :: Int -> Int -> IO ()
binarySearch lower upper
    | lower >= upper = do
        putStrLn $ '!' : show lower
        hFlush stdout
    | otherwise = do
        let number = lower + (upper - lower) `div` 2 + 1
        putStrLn $ show number
        hFlush stdout
        raw <- getLine
        let answer = filter (\c -> c /= '\r') raw
        case answer of
            "<"  -> binarySearch lower (number - 1)
            ">=" -> binarySearch number upper
            _    -> do
                hPutStrLn stderr $ "Invalid answer from validator: " ++ show answer
                exitWith (ExitFailure 1)

main :: IO ()
main = do
    line <- getLine
    let upper = read line :: Int
    binarySearch 1 upper
