package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

const (
	exitAC  = 0
	exitWA  = 1
	exitTLE = 2
	exitRE  = 3
	exitPE  = 4
)

var reader *bufio.Reader

func finish(exitcode int, message string) {
	if message != "" {
		fmt.Fprintln(os.Stderr, message)
	}
	os.Exit(exitcode)
}

func readLine() (string, bool) {
	line, err := reader.ReadString('\n')
	if err != nil && line == "" {
		return "", false
	}
	return strings.TrimSpace(line), true
}

func parseInteger(text string) (int64, bool) {
	value, err := strconv.ParseInt(strings.TrimSpace(text), 10, 64)
	return value, err == nil
}

func envNumber(name string) string {
	value, ok := os.LookupEnv(name)
	if !ok {
		return "null"
	}
	return value
}

func printLimits() {
	userLanguage := "null"
	if value, ok := os.LookupEnv("USER_LANGUAGE"); ok {
		userLanguage = strconv.Quote(value)
	}
	perLanguageLimits := "null"
	if value, ok := os.LookupEnv("PER_LANGUAGE_LIMITS"); ok {
		perLanguageLimits = value
	}

	fmt.Fprintf(os.Stderr,
		"{\"problem_time_limit\": %s, \"problem_output_limit\": %s, \"problem_memory_limit\": %s, "+
			"\"problem_pid_limit\": %s, \"user_language\": %s, \"per_language_limits\": %s}\n",
		envNumber("PROBLEM_TIME_LIMIT"),
		envNumber("PROBLEM_OUTPUT_LIMIT"),
		envNumber("PROBLEM_MEMORY_LIMIT"),
		envNumber("PROBLEM_PID_LIMIT"),
		userLanguage,
		perLanguageLimits,
	)
}

func readSecretValue() int64 {
	line, ok := readLine()
	if !ok {
		finish(exitRE, "Invalid secret data")
	}
	value, ok := parseInteger(line)
	if !ok {
		finish(exitRE, "Invalid secret data")
	}
	return value
}

func main() {
	reader = bufio.NewReader(os.Stdin)

	maxValue := readSecretValue()
	maxTries := readSecretValue()
	secret := readSecretValue()

	printLimits()

	fmt.Println(maxValue)

	for iteration := int64(0); ; iteration++ {
		user, ok := readLine()
		if !ok {
			break
		}

		if strings.HasPrefix(user, "!") {
			answer, ok := parseInteger(user[1:])
			if !ok {
				finish(exitRE, "Invalid data")
			}
			if answer == secret {
				finish(exitAC, "")
			}
			finish(exitWA, fmt.Sprintf("!%d", secret))
		}

		if iteration > maxTries {
			finish(exitTLE, "Exceeded number of allowed iterations")
		}
		value, ok := parseInteger(user)
		if !ok {
			finish(exitRE, "Invalid data")
		}
		if secret < value {
			fmt.Println("<")
		} else {
			fmt.Println(">=")
		}
	}

	finish(exitRE, "Unexpected flow")
}
