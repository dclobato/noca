#!/usr/bin/perl
use strict;
use warnings;

$| = 1;

my $upper = do { my $line = <STDIN>; chomp $line; int($line) };
my $lower = 1;

while ($lower < $upper) {
    my $number = $lower + int(($upper - $lower) / 2) + 1;
    print "$number\n";

    my $answer = <STDIN>;
    chomp $answer;
    $answer =~ s/^\s+|\s+$//g;

    if ($answer eq '<') {
        $upper = $number - 1;
    } elsif ($answer eq '>=') {
        $lower = $number;
    } else {
        die "Invalid answer from validator: '$answer'\n";
    }
}

print "!$lower\n";
exit 0;
