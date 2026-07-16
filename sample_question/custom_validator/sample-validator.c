#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { EXIT_AC = 0, EXIT_WA = 1, EXIT_TLE = 2, EXIT_RE = 3, EXIT_PE = 4 };

static void finish(int exitcode, const char *message) {
    if (message != NULL) {
        fprintf(stderr, "%s\n", message);
        fflush(stderr);
    }
    exit(exitcode);
}

static int read_line(char *buffer, size_t size) {
    if (fgets(buffer, (int)size, stdin) == NULL) {
        return 0;
    }
    buffer[strcspn(buffer, "\r\n")] = '\0';
    return 1;
}

static int parse_long(const char *text, long *value) {
    char *end = NULL;
    if (*text == '\0') {
        return 0;
    }
    *value = strtol(text, &end, 10);
    while (*end == ' ' || *end == '\t') {
        end++;
    }
    return *end == '\0';
}

static void print_limits(void) {
    static const char *names[] = {
        "PROBLEM_TIME_LIMIT",
        "PROBLEM_OUTPUT_LIMIT",
        "PROBLEM_MEMORY_LIMIT",
        "PROBLEM_PID_LIMIT",
    };
    static const char *keys[] = {
        "problem_time_limit",
        "problem_output_limit",
        "problem_memory_limit",
        "problem_pid_limit",
    };
    const char *user_language = getenv("USER_LANGUAGE");
    const char *per_language_limits = getenv("PER_LANGUAGE_LIMITS");

    fputc('{', stderr);
    for (int index = 0; index < 4; index++) {
        const char *value = getenv(names[index]);
        fprintf(stderr, "\"%s\": %s, ", keys[index], value != NULL ? value : "null");
    }
    if (user_language != NULL) {
        fprintf(stderr, "\"user_language\": \"%s\", ", user_language);
    } else {
        fprintf(stderr, "\"user_language\": null, ");
    }
    fprintf(stderr, "\"per_language_limits\": %s}\n",
            per_language_limits != NULL ? per_language_limits : "null");
    fflush(stderr);
}

int main(void) {
    char line[4096];
    long max_value = 0;
    long max_tries = 0;
    long secret = 0;

    if (!read_line(line, sizeof line) || !parse_long(line, &max_value)) {
        finish(EXIT_RE, "Invalid secret data");
    }
    if (!read_line(line, sizeof line) || !parse_long(line, &max_tries)) {
        finish(EXIT_RE, "Invalid secret data");
    }
    if (!read_line(line, sizeof line) || !parse_long(line, &secret)) {
        finish(EXIT_RE, "Invalid secret data");
    }

    print_limits();

    printf("%ld\n", max_value);
    fflush(stdout);

    for (long iteration = 0; read_line(line, sizeof line); iteration++) {
        long value = 0;
        if (line[0] == '!') {
            if (!parse_long(line + 1, &value)) {
                finish(EXIT_RE, "Invalid data");
            }
            if (value == secret) {
                finish(EXIT_AC, NULL);
            }
            char message[64];
            snprintf(message, sizeof message, "!%ld", secret);
            finish(EXIT_WA, message);
        }

        if (iteration > max_tries) {
            finish(EXIT_TLE, "Exceeded number of allowed iterations");
        }
        if (!parse_long(line, &value)) {
            finish(EXIT_RE, "Invalid data");
        }
        printf("%s\n", secret < value ? "<" : ">=");
        fflush(stdout);
    }

    finish(EXIT_RE, "Unexpected flow");
    return EXIT_RE;
}
