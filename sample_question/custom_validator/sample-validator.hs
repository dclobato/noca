import Control.Monad (when)
import Data.Char (isDigit, isSpace)
import Data.Maybe (fromMaybe)
import System.Environment (lookupEnv)
import System.Exit (ExitCode (..), exitWith)
import System.IO

acCode, waCode, tleCode, reCode, peCode :: Int
acCode = 0
waCode = 1
tleCode = 2
reCode = 3
peCode = 4

finish :: Int -> Maybe String -> IO a
finish exitcode message = do
  case message of
    Just text -> hPutStrLn stderr text >> hFlush stderr
    Nothing -> return ()
  if exitcode == 0 then exitWith ExitSuccess else exitWith (ExitFailure exitcode)

readLineMaybe :: IO (Maybe String)
readLineMaybe = do
  eof <- isEOF
  if eof then return Nothing else Just . trim <$> getLine

trim :: String -> String
trim = dropWhile isSpace . reverse . dropWhile isSpace . reverse

parseInteger :: String -> Maybe Integer
parseInteger text =
  case trim text of
    ('-' : rest) | not (null rest) && all isDigit rest -> Just (negate (read rest))
    ('+' : rest) | not (null rest) && all isDigit rest -> Just (read rest)
    digits | not (null digits) && all isDigit digits -> Just (read digits)
    _ -> Nothing

envNumber :: String -> IO String
envNumber name = fromMaybe "null" <$> lookupEnv name

printLimits :: IO ()
printLimits = do
  timeLimit <- envNumber "PROBLEM_TIME_LIMIT"
  outputLimit <- envNumber "PROBLEM_OUTPUT_LIMIT"
  memoryLimit <- envNumber "PROBLEM_MEMORY_LIMIT"
  pidLimit <- envNumber "PROBLEM_PID_LIMIT"
  userLanguage <- maybe "null" (\value -> "\"" ++ value ++ "\"") <$> lookupEnv "USER_LANGUAGE"
  perLanguageLimits <- fromMaybe "null" <$> lookupEnv "PER_LANGUAGE_LIMITS"
  hPutStrLn stderr $
    "{\"problem_time_limit\": " ++ timeLimit
      ++ ", \"problem_output_limit\": " ++ outputLimit
      ++ ", \"problem_memory_limit\": " ++ memoryLimit
      ++ ", \"problem_pid_limit\": " ++ pidLimit
      ++ ", \"user_language\": " ++ userLanguage
      ++ ", \"per_language_limits\": " ++ perLanguageLimits
      ++ "}"
  hFlush stderr

readSecretValue :: IO Integer
readSecretValue = do
  line <- readLineMaybe
  case line >>= parseInteger of
    Just value -> return value
    Nothing -> finish reCode (Just "Invalid secret data")

validate :: Integer -> Integer -> Integer -> IO ()
validate maxTries secret iteration = do
  line <- readLineMaybe
  case line of
    Nothing -> finish reCode (Just "Unexpected flow")
    Just user -> do
      case user of
        ('!' : answerText) ->
          case parseInteger answerText of
            Nothing -> finish reCode (Just "Invalid data")
            Just answer ->
              if answer == secret
                then finish acCode Nothing
                else finish waCode (Just ("!" ++ show secret))
        _ -> return ()

      when (iteration > maxTries) $
        finish tleCode (Just "Exceeded number of allowed iterations")

      case parseInteger user of
        Nothing -> finish reCode (Just "Invalid data")
        Just value -> do
          putStrLn (if secret < value then "<" else ">=")
          hFlush stdout
          validate maxTries secret (iteration + 1)

main :: IO ()
main = do
  hSetBuffering stdout LineBuffering
  maxValue <- readSecretValue
  maxTries <- readSecretValue
  secret <- readSecretValue

  printLimits

  print maxValue
  hFlush stdout

  validate maxTries secret 0
