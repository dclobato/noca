<?php

// Interactive binary search: guess the secret number the validator is holding.
// Every message must be flushed immediately, or the two sides deadlock.

$upper = (int) trim(fgets(STDIN));
$lower = 1;

while ($lower < $upper) {
    $number = $lower + intdiv($upper - $lower, 2) + 1;

    echo $number, "\n";
    flush();

    $answer = trim(fgets(STDIN));

    if ($answer === "<") {
        $upper = $number - 1;
    } elseif ($answer === ">=") {
        $lower = $number;
    } else {
        fwrite(STDERR, "Invalid answer from validator: $answer\n");
        exit(1);
    }
}

echo "!$lower\n";
flush();
exit(0);
