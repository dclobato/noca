%  NOCA -- Next Online Contest Administrator
%  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
%  This program is distributed in the hope that it will be useful,
%  but WITHOUT ANY WARRANTY; without even the implied warranty of
%  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

:- initialization(main, main).

read_numbers(Numbers) :-
    read_string(user_input, _, Input),
    split_string(Input, " \n\t\r", " \n\t\r", Parts),
    maplist(number_string, Numbers, Parts).

point_distance(point(X1, Y1), point(X2, Y2), Distance) :-
    Distance is abs(X1 - X2) + abs(Y1 - Y2).

take_points(0, Rest, [], Rest).
take_points(K, [X, Y | Values], [point(X, Y) | Points], Rest) :-
    K > 0,
    NextK is K - 1,
    take_points(NextK, Values, Points, Rest).

nth_point(Index, Points, Point) :-
    nth0(Index, Points, Point).

distance_between(Points, I, J, Distance) :-
    nth_point(I, Points, A),
    nth_point(J, Points, B),
    point_distance(A, B, Distance).

bit(Index, Value) :-
    Value is 1 << Index.

mask_has(Mask, Index) :-
    bit(Index, Bit),
    Mask /\ Bit =\= 0.

candidate_cost(Points, Memo, PreviousMask, Last, Previous, Cost) :-
    member(dp(PreviousMask, Previous, PreviousCost), Memo),
    mask_has(PreviousMask, Previous),
    PreviousIndex is Previous + 1,
    LastIndex is Last + 1,
    distance_between(Points, PreviousIndex, LastIndex, Distance),
    Cost is PreviousCost + Distance.

state_cost(Points, _Memo, Mask, Last, Cost) :-
    bit(Last, Mask),
    LastIndex is Last + 1,
    distance_between(Points, 0, LastIndex, Cost),
    !.
state_cost(Points, Memo, Mask, Last, Cost) :-
    bit(Last, LastBit),
    PreviousMask is Mask - LastBit,
    findall(
        Candidate,
        candidate_cost(Points, Memo, PreviousMask, Last, _Previous, Candidate),
        Candidates
    ),
    min_list(Candidates, Cost).

build_dp(_Points, K, Limit, Mask, Memo, Memo) :-
    Mask >= Limit,
    !,
    K >= 0.
build_dp(Points, K, Limit, Mask, Memo, Result) :-
    MaxLast is K - 1,
    findall(
        dp(Mask, Last, Cost),
        (
            between(0, MaxLast, Last),
            mask_has(Mask, Last),
            state_cost(Points, Memo, Mask, Last, Cost)
        ),
        States
    ),
    append(Memo, States, NextMemo),
    NextMask is Mask + 1,
    build_dp(Points, K, Limit, NextMask, NextMemo, Result).

final_cost(Points, Memo, K, FullMask, Last, Cost) :-
    member(dp(FullMask, Last, PathCost), Memo),
    LastIndex is Last + 1,
    EndIndex is K + 1,
    distance_between(Points, LastIndex, EndIndex, EndDistance),
    Cost is PathCost + EndDistance.

solve([_L, _C, K, Xe, Ye, Xc, Yc | Rest], Answer) :-
    Start = point(Xe, Ye),
    End = point(Xc, Yc),
    take_points(K, Rest, Houses, _Unused),
    (
        K =:= 0
    ->
        point_distance(Start, End, Answer)
    ;
        Points = [Start | Houses],
        append(Points, [End], AllPoints),
        Limit is 1 << K,
        build_dp(AllPoints, K, Limit, 1, [], Memo),
        FullMask is Limit - 1,
        MaxLast is K - 1,
        findall(
            Cost,
            (
                between(0, MaxLast, Last),
                final_cost(AllPoints, Memo, K, FullMask, Last, Cost)
            ),
            Costs
        ),
        min_list(Costs, Answer)
    ).

main(_) :-
    read_numbers(Numbers),
    solve(Numbers, Answer),
    format("~w~n", [Answer]).
