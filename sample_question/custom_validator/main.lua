local upper = tonumber(io.read("l"))
local lower = 1

while lower < upper do
    local number = lower + math.floor((upper - lower) / 2) + 1
    io.write(number .. "\n")
    io.flush()

    local answer = io.read("l")
    if answer then
        answer = answer:match("^%s*(.-)%s*$")
    end

    if answer == "<" then
        upper = number - 1
    elseif answer == ">=" then
        lower = number
    else
        io.stderr:write("Invalid answer from validator: " .. tostring(answer) .. "\n")
        os.exit(1)
    end
end

io.write("!" .. lower .. "\n")
io.flush()
os.exit(0)
