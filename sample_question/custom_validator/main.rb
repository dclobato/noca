upper = gets.to_i
lower = 1

while lower < upper
    number = lower + (upper - lower) / 2 + 1
    puts number
    $stdout.flush

    answer = gets.strip

    if answer == "<"
        upper = number - 1
    elsif answer == ">="
        lower = number
    else
        raise "Invalid answer from validator: #{answer.inspect}"
    end
end

puts "!#{lower}"
$stdout.flush
exit 0
