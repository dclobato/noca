#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# Perl translation of the sample TSP problem: shortest route that starts at the
# station, visits every house exactly once, and ends at the destination, under
# the Manhattan metric. Solved with Held-Karp (bitmask dynamic programming).

use strict;
use warnings;

sub manhattan {
    my ($ax, $ay, $bx, $by) = @_;
    return abs($ax - $bx) + abs($ay - $by);
}

# Read every integer from stdin at once, mirroring scanf semantics.
my @nums = split ' ', do { local $/; <STDIN> };
my $idx  = 0;

my $l = $nums[$idx++];
my $c = $nums[$idx++];
my $k = $nums[$idx++];

my @station = ($nums[$idx++], $nums[$idx++]);
my @dest    = ($nums[$idx++], $nums[$idx++]);

my @houses;
for (1 .. $k) {
    push @houses, [$nums[$idx++], $nums[$idx++]];
}

if ($k == 0) {
    print manhattan(@station, @dest), "\n";
    exit 0;
}

# points: [station, houses..., destination]; indices 0 and K+1 are the endpoints.
my @points = (\@station, @houses, \@dest);
my $total  = $k + 2;

# Full distance matrix.
my @dist;
for my $i (0 .. $total - 1) {
    for my $j (0 .. $total - 1) {
        $dist[$i][$j] = manhattan(@{ $points[$i] }, @{ $points[$j] });
    }
}

my $masks = 1 << $k;
my $INF   = ~0;

# dp[mask][i] -> cheapest path from the station covering exactly the houses in
# `mask` and ending at house i.
my @dp;
for my $mask (0 .. $masks - 1) {
    $dp[$mask] = [($INF) x $k];
}
$dp[1 << $_][$_] = $dist[0][$_ + 1] for 0 .. $k - 1;

for my $mask (0 .. $masks - 1) {
    for my $u (0 .. $k - 1) {
        next unless $mask & (1 << $u);
        my $cur = $dp[$mask][$u];
        next if $cur == $INF;
        for my $v (0 .. $k - 1) {
            next if $mask & (1 << $v);
            my $next = $mask | (1 << $v);
            my $cost = $cur + $dist[$u + 1][$v + 1];
            $dp[$next][$v] = $cost if $cost < $dp[$next][$v];
        }
    }
}

my $full = $masks - 1;
my $best = $INF;
for my $i (0 .. $k - 1) {
    next if $dp[$full][$i] == $INF;
    my $cost = $dp[$full][$i] + $dist[$i + 1][$k + 1];
    $best = $cost if $cost < $best;
}

print $best, "\n";
