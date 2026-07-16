local EXIT_CODES = {
    AC = 0,
    WA = 1,
    TLE = 2,
    RE = 3,
    PE = 4,
}

local function finish(exitcode, message)
    if message then
        io.stderr:write(message, "\n")
        io.stderr:flush()
    end
    os.exit(exitcode)
end

local function read_line()
    local line = io.read("l")
    if line == nil then
        return nil
    end
    return (line:gsub("^%s*(.-)%s*$", "%1"))
end

local function parse_integer(text)
    if text == nil then
        return nil
    end
    local trimmed = text:gsub("^%s*(.-)%s*$", "%1")
    if not trimmed:match("^[+-]?%d+$") then
        return nil
    end
    return math.tointeger(tonumber(trimmed))
end

local function env_number(name)
    return os.getenv(name) or "null"
end

local function print_limits()
    local user_language = os.getenv("USER_LANGUAGE")
    local per_language_limits = os.getenv("PER_LANGUAGE_LIMITS")

    io.stderr:write(
        "{",
        '"problem_time_limit": ', env_number("PROBLEM_TIME_LIMIT"), ", ",
        '"problem_output_limit": ', env_number("PROBLEM_OUTPUT_LIMIT"), ", ",
        '"problem_memory_limit": ', env_number("PROBLEM_MEMORY_LIMIT"), ", ",
        '"problem_pid_limit": ', env_number("PROBLEM_PID_LIMIT"), ", ",
        '"user_language": ', user_language and ('"' .. user_language .. '"') or "null", ", ",
        '"per_language_limits": ', per_language_limits or "null",
        "}\n"
    )
    io.stderr:flush()
end

local function read_secret_value()
    local value = parse_integer(read_line())
    if value == nil then
        finish(EXIT_CODES.RE, "Invalid secret data")
    end
    return value
end

local max_value = read_secret_value()
local max_tries = read_secret_value()
local secret = read_secret_value()

print_limits()

io.write(max_value, "\n")
io.stdout:flush()

local iteration = 0
while true do
    local user = read_line()
    if user == nil then
        break
    end

    if user:sub(1, 1) == "!" then
        local answer = parse_integer(user:sub(2))
        if answer == nil then
            finish(EXIT_CODES.RE, "Invalid data")
        end
        if answer == secret then
            finish(EXIT_CODES.AC)
        end
        finish(EXIT_CODES.WA, "!" .. secret)
    end

    if iteration > max_tries then
        finish(EXIT_CODES.TLE, "Exceeded number of allowed iterations")
    end
    local value = parse_integer(user)
    if value == nil then
        finish(EXIT_CODES.RE, "Invalid data")
    end
    io.write(secret < value and "<" or ">=", "\n")
    io.stdout:flush()
    iteration = iteration + 1
end

finish(EXIT_CODES.RE, "Unexpected flow")
