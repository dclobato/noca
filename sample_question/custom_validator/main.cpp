#include <iostream>
#include <string>
#include <stdexcept>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int upper, lower = 1;
    std::cin >> upper;

    while (lower < upper) {
        int number = lower + (upper - lower) / 2 + 1;
        std::cout << number << '\n';
        std::cout.flush();

        std::string answer;
        std::cin >> answer;

        if (answer == "<") {
            upper = number - 1;
        } else if (answer == ">=") {
            lower = number;
        } else {
            throw std::runtime_error("Invalid answer from validator: " + answer);
        }
    }

    std::cout << '!' << lower << '\n';
    std::cout.flush();
    return 0;
}
