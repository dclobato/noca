package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

var scanner *bufio.Scanner
var writer *bufio.Writer

func readLine() string {
	scanner.Scan()
	return strings.TrimSpace(scanner.Text())
}

func main() {
	scanner = bufio.NewScanner(os.Stdin)
	writer = bufio.NewWriter(os.Stdout)
	defer writer.Flush()

	upper, _ := strconv.Atoi(readLine())
	lower := 1

	for lower < upper {
		number := lower + (upper-lower)/2 + 1
		fmt.Fprintf(writer, "%d\n", number)
		writer.Flush()

		answer := readLine()

		if answer == "<" {
			upper = number - 1
		} else if answer == ">=" {
			lower = number
		} else {
			fmt.Fprintf(os.Stderr, "Invalid answer from validator: %q\n", answer)
			os.Exit(1)
		}
	}

	fmt.Fprintf(writer, "!%d\n", lower)
	writer.Flush()
}
