#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>

namespace {

constexpr int kAC = 0;
constexpr int kWA = 1;
constexpr int kTLE = 2;
constexpr int kRE = 3;
constexpr int kPE = 4;

[[noreturn]] void finish(int exitcode, const std::string &message = "") {
    if (!message.empty()) {
        std::cerr << message << std::endl;
    }
    std::exit(exitcode);
}

std::optional<std::string> read_line() {
    std::string line;
    if (!std::getline(std::cin, line)) {
        return std::nullopt;
    }
    while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t')) {
        line.pop_back();
    }
    return line;
}

std::optional<long long> parse_long(const std::string &text) {
    try {
        std::size_t consumed = 0;
        const long long value = std::stoll(text, &consumed);
        return consumed == text.size() ? std::optional<long long>(value) : std::nullopt;
    } catch (...) {
        return std::nullopt;
    }
}

std::string env_number(const char *name) {
    const char *value = std::getenv(name);
    return value != nullptr ? std::string(value) : std::string("null");
}

void print_limits() {
    const char *user_language = std::getenv("USER_LANGUAGE");
    const char *per_language_limits = std::getenv("PER_LANGUAGE_LIMITS");

    std::cerr << "{"
              << "\"problem_time_limit\": " << env_number("PROBLEM_TIME_LIMIT") << ", "
              << "\"problem_output_limit\": " << env_number("PROBLEM_OUTPUT_LIMIT") << ", "
              << "\"problem_memory_limit\": " << env_number("PROBLEM_MEMORY_LIMIT") << ", "
              << "\"problem_pid_limit\": " << env_number("PROBLEM_PID_LIMIT") << ", "
              << "\"user_language\": "
              << (user_language != nullptr ? "\"" + std::string(user_language) + "\"" : "null") << ", "
              << "\"per_language_limits\": "
              << (per_language_limits != nullptr ? std::string(per_language_limits) : "null") << "}"
              << std::endl;
}

long long read_secret_value() {
    const auto line = read_line();
    if (!line.has_value()) {
        finish(kRE, "Invalid secret data");
    }
    const auto value = parse_long(*line);
    if (!value.has_value()) {
        finish(kRE, "Invalid secret data");
    }
    return *value;
}

}  // namespace

int main() {
    const long long max_value = read_secret_value();
    const long long max_tries = read_secret_value();
    const long long secret = read_secret_value();

    print_limits();

    std::cout << max_value << std::endl;

    for (long long iteration = 0;; iteration++) {
        const auto line = read_line();
        if (!line.has_value()) {
            break;
        }

        if (!line->empty() && line->front() == '!') {
            const auto answer = parse_long(line->substr(1));
            if (!answer.has_value()) {
                finish(kRE, "Invalid data");
            }
            if (*answer == secret) {
                finish(kAC);
            }
            finish(kWA, "!" + std::to_string(secret));
        }

        if (iteration > max_tries) {
            finish(kTLE, "Exceeded number of allowed iterations");
        }
        const auto value = parse_long(*line);
        if (!value.has_value()) {
            finish(kRE, "Invalid data");
        }
        std::cout << (secret < *value ? "<" : ">=") << std::endl;
    }

    finish(kRE, "Unexpected flow");
}
