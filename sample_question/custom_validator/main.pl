:- initialization(main, main).
:- use_module(library(readutil)).

main :-
    read_line_to_string(current_input, UpperStr),
    number_string(Upper, UpperStr),
    binary_search(1, Upper).

binary_search(Lower, Upper) :-
    Lower >= Upper, !,
    format("!~w~n", [Lower]),
    flush_output.
binary_search(Lower, Upper) :-
    Lower < Upper,
    Number is Lower + (Upper - Lower) // 2 + 1,
    format("~w~n", [Number]),
    flush_output,
    read_line_to_string(current_input, Answer),
    ( Answer = "<" ->
        Upper1 is Number - 1,
        binary_search(Lower, Upper1)
    ; Answer = ">=" ->
        binary_search(Number, Upper)
    ;
        format(user_error, "Invalid answer from validator: ~q~n", [Answer]),
        halt(1)
    ).
