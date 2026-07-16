use strict;
use warnings;
use IO::Handle;

use constant {
    AC  => 0,
    WA  => 1,
    TLE => 2,
    RE  => 3,
    PE  => 4,
};

STDOUT->autoflush(1);
STDERR->autoflush(1);

sub finish {
    my ($exitcode, $message) = @_;
    if (defined $message) {
        print STDERR "$message\n";
    }
    exit $exitcode;
}

sub read_line {
    my $line = <STDIN>;
    return undef unless defined $line;
    $line =~ s/^\s+|\s+$//g;
    return $line;
}

sub parse_integer {
    my ($text) = @_;
    return undef unless defined $text;
    $text =~ s/^\s+|\s+$//g;
    return undef unless $text =~ /^[+-]?\d+$/;
    return $text + 0;
}

sub env_number {
    my ($name) = @_;
    return defined $ENV{$name} ? $ENV{$name} : 'null';
}

sub print_limits {
    my $user_language =
      defined $ENV{USER_LANGUAGE} ? '"' . $ENV{USER_LANGUAGE} . '"' : 'null';
    my $per_language_limits =
      defined $ENV{PER_LANGUAGE_LIMITS} ? $ENV{PER_LANGUAGE_LIMITS} : 'null';

    print STDERR '{'
      . '"problem_time_limit": ' . env_number('PROBLEM_TIME_LIMIT') . ', '
      . '"problem_output_limit": ' . env_number('PROBLEM_OUTPUT_LIMIT') . ', '
      . '"problem_memory_limit": ' . env_number('PROBLEM_MEMORY_LIMIT') . ', '
      . '"problem_pid_limit": ' . env_number('PROBLEM_PID_LIMIT') . ', '
      . '"user_language": ' . $user_language . ', '
      . '"per_language_limits": ' . $per_language_limits . "}\n";
}

sub read_secret_value {
    my $value = parse_integer(read_line());
    finish(RE, 'Invalid secret data') unless defined $value;
    return $value;
}

my $max_value = read_secret_value();
my $max_tries = read_secret_value();
my $secret    = read_secret_value();

print_limits();

print "$max_value\n";

my $iteration = 0;
while (defined(my $user = read_line())) {
    if (substr($user, 0, 1) eq '!') {
        my $answer = parse_integer(substr($user, 1));
        finish(RE, 'Invalid data') unless defined $answer;
        finish(AC) if $answer == $secret;
        finish(WA, "!$secret");
    }

    finish(TLE, 'Exceeded number of allowed iterations') if $iteration > $max_tries;

    my $value = parse_integer($user);
    finish(RE, 'Invalid data') unless defined $value;

    print $secret < $value ? "<\n" : ">=\n";
    $iteration++;
}

finish(RE, 'Unexpected flow');
