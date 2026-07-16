using System;

class Main {
    static void Main(string[] args) {
        int upper = int.Parse(Console.ReadLine()!.Trim());
        int lower = 1;

        while (lower < upper) {
            int number = lower + (upper - lower) / 2 + 1;
            Console.WriteLine(number);

            string answer = Console.ReadLine()!.Trim();

            if (answer == "<") {
                upper = number - 1;
            } else if (answer == ">=") {
                lower = number;
            } else {
                throw new Exception($"Invalid answer from validator: {answer}");
            }
        }

        Console.WriteLine($"!{lower}");
    }
}
