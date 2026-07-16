EXIT_CODES = {
  "AC" => 0,
  "WA" => 1,
  "TLE" => 2,
  "RE" => 3,
  "PE" => 4,
}.freeze

def finish(exitcode, message = nil)
  if message
    $stderr.puts(message)
    $stderr.flush
  end
  exit(exitcode)
end

def read_line
  line = $stdin.gets
  line&.strip
end

def parse_integer(text)
  return nil if text.nil?

  Integer(text.strip, 10)
rescue ArgumentError
  nil
end

def env_number(name)
  ENV.fetch(name, nil) || "null"
end

def print_limits
  user_language = ENV.fetch("USER_LANGUAGE", nil)
  per_language_limits = ENV.fetch("PER_LANGUAGE_LIMITS", nil)

  $stderr.puts(
    "{" \
    "\"problem_time_limit\": #{env_number('PROBLEM_TIME_LIMIT')}, " \
    "\"problem_output_limit\": #{env_number('PROBLEM_OUTPUT_LIMIT')}, " \
    "\"problem_memory_limit\": #{env_number('PROBLEM_MEMORY_LIMIT')}, " \
    "\"problem_pid_limit\": #{env_number('PROBLEM_PID_LIMIT')}, " \
    "\"user_language\": #{user_language ? "\"#{user_language}\"" : 'null'}, " \
    "\"per_language_limits\": #{per_language_limits || 'null'}" \
    "}"
  )
  $stderr.flush
end

def read_secret_value
  value = parse_integer(read_line)
  finish(EXIT_CODES["RE"], "Invalid secret data") if value.nil?
  value
end

max_value = read_secret_value
max_tries = read_secret_value
secret = read_secret_value

print_limits

$stdout.puts(max_value)
$stdout.flush

iteration = 0
while (user = read_line)
  if user.start_with?("!")
    answer = parse_integer(user[1..])
    finish(EXIT_CODES["RE"], "Invalid data") if answer.nil?
    finish(EXIT_CODES["AC"]) if answer == secret

    finish(EXIT_CODES["WA"], "!#{secret}")
  end

  finish(EXIT_CODES["TLE"], "Exceeded number of allowed iterations") if iteration > max_tries

  value = parse_integer(user)
  finish(EXIT_CODES["RE"], "Invalid data") if value.nil?

  $stdout.puts(secret < value ? "<" : ">=")
  $stdout.flush
  iteration += 1
end

finish(EXIT_CODES["RE"], "Unexpected flow")
