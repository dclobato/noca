import Foundation

guard let upperStr = readLine(),
      let upper = Int(upperStr.trimmingCharacters(in: .whitespaces)) else {
    fatalError("Failed to read upper bound")
}

var lower = 1
var upperBound = upper

while lower < upperBound {
    let number = lower + (upperBound - lower) / 2 + 1
    print(number)

    guard let answer = readLine()?.trimmingCharacters(in: .whitespaces) else {
        fatalError("Failed to read answer")
    }

    if answer == "<" {
        upperBound = number - 1
    } else if answer == ">=" {
        lower = number
    } else {
        fputs("Invalid answer from validator: \(answer)\n", stderr)
        exit(1)
    }
}

print("!\(lower)")
exit(0)
