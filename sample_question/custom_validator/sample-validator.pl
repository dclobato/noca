:- initialization(main, main).
:- use_module(library(readutil)).

exit_code(ac, 0).
exit_code(wa, 1).
exit_code(tle, 2).
exit_code(re, 3).
exit_code(pe, 4).

finish(Verdict) :-
    exit_code(Verdict, Code),
    halt(Code).

finish(Verdict, Message) :-
    format(user_error, "~w~n", [Message]),
    flush_output(user_error),
    exit_code(Verdict, Code),
    halt(Code).

read_trimmed_line(Line) :-
    read_line_to_string(current_input, Raw),
    (   Raw == end_of_file
    ->  Line = end_of_file
    ;   normalize_space(string(Line), Raw)
    ).

parse_integer(Text, Value) :-
    catch(number_string(Value, Text), _, fail),
    integer(Value).

env_number(Name, Value) :-
    (   getenv(Name, Raw)
    ->  Value = Raw
    ;   Value = 'null'
    ).

print_limits :-
    env_number('PROBLEM_TIME_LIMIT', TimeLimit),
    env_number('PROBLEM_OUTPUT_LIMIT', OutputLimit),
    env_number('PROBLEM_MEMORY_LIMIT', MemoryLimit),
    env_number('PROBLEM_PID_LIMIT', PidLimit),
    (   getenv('USER_LANGUAGE', Language)
    ->  format(atom(UserLanguage), "\"~w\"", [Language])
    ;   UserLanguage = 'null'
    ),
    env_number('PER_LANGUAGE_LIMITS', PerLanguageLimits),
    format(user_error,
           '{"problem_time_limit": ~w, "problem_output_limit": ~w, "problem_memory_limit": ~w, \c
            "problem_pid_limit": ~w, "user_language": ~w, "per_language_limits": ~w}~n',
           [TimeLimit, OutputLimit, MemoryLimit, PidLimit, UserLanguage, PerLanguageLimits]),
    flush_output(user_error).

read_secret_value(Value) :-
    read_trimmed_line(Line),
    (   Line \== end_of_file,
        parse_integer(Line, Parsed)
    ->  Value = Parsed
    ;   finish(re, 'Invalid secret data')
    ).

main :-
    read_secret_value(MaxValue),
    read_secret_value(MaxTries),
    read_secret_value(Secret),
    print_limits,
    format("~d~n", [MaxValue]),
    flush_output,
    validate(MaxTries, Secret, 0).

validate(MaxTries, Secret, Iteration) :-
    read_trimmed_line(Line),
    (   Line == end_of_file
    ->  finish(re, 'Unexpected flow')
    ;   handle_answer(Line, Secret),
        check_budget(Iteration, MaxTries),
        respond(Line, Secret),
        Next is Iteration + 1,
        validate(MaxTries, Secret, Next)
    ).

handle_answer(Line, Secret) :-
    (   string_concat("!", AnswerText, Line)
    ->  (   parse_integer(AnswerText, Answer)
        ->  (   Answer =:= Secret
            ->  finish(ac)
            ;   format(atom(Message), "!~d", [Secret]),
                finish(wa, Message)
            )
        ;   finish(re, 'Invalid data')
        )
    ;   true
    ).

check_budget(Iteration, MaxTries) :-
    (   Iteration > MaxTries
    ->  finish(tle, 'Exceeded number of allowed iterations')
    ;   true
    ).

respond(Line, Secret) :-
    (   parse_integer(Line, Value)
    ->  (   Secret < Value
        ->  format("<~n")
        ;   format(">=~n")
        ),
        flush_output
    ;   finish(re, 'Invalid data')
    ).
