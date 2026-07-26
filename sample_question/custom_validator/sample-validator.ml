(* Sample interactive validator for the "guess the secret number" problem.

   Reads three secret values from stdin (max_value, max_tries, secret), reports the
   judge-supplied limits on stderr, then plays the guessing game with the contestant
   program. The exit code is the verdict. *)

let ac = 0
let wa = 1
let tle = 2
let re = 3
let _pe = 4

let finish exit_code message =
  if message <> "" then prerr_endline message;
  exit exit_code

let read_trimmed_line () =
  match input_line stdin with
  | line ->
      let n = ref (String.length line) in
      while
        !n > 0
        &&
        let c = line.[!n - 1] in
        c = '\r' || c = ' ' || c = '\t'
      do
        decr n
      done;
      Some (String.sub line 0 !n)
  | exception End_of_file -> None

(* int_of_string_opt would also accept "0x1f" and "1_000", so the digits are
   checked explicitly to keep the accepted syntax strict. *)
let parse_long text =
  let n = String.length text in
  let start = if n > 0 && (text.[0] = '-' || text.[0] = '+') then 1 else 0 in
  if n = 0 || start >= n then None
  else begin
    let all_digits = ref true in
    for i = start to n - 1 do
      match text.[i] with '0' .. '9' -> () | _ -> all_digits := false
    done;
    if !all_digits then int_of_string_opt text else None
  end

let env_number name = match Sys.getenv_opt name with Some value -> value | None -> "null"

let print_limits () =
  let user_language =
    match Sys.getenv_opt "USER_LANGUAGE" with Some value -> "\"" ^ value ^ "\"" | None -> "null"
  in
  let per_language_limits =
    match Sys.getenv_opt "PER_LANGUAGE_LIMITS" with Some value -> value | None -> "null"
  in
  Printf.eprintf
    "{\"problem_time_limit\": %s, \"problem_output_limit\": %s, \"problem_memory_limit\": %s, \
     \"problem_pid_limit\": %s, \"user_language\": %s, \"per_language_limits\": %s}\n%!"
    (env_number "PROBLEM_TIME_LIMIT")
    (env_number "PROBLEM_OUTPUT_LIMIT")
    (env_number "PROBLEM_MEMORY_LIMIT")
    (env_number "PROBLEM_PID_LIMIT")
    user_language per_language_limits

let read_secret_value () =
  match read_trimmed_line () with
  | None -> finish re "Invalid secret data"
  | Some line -> (
      match parse_long line with Some value -> value | None -> finish re "Invalid secret data")

let () =
  let max_value = read_secret_value () in
  let max_tries = read_secret_value () in
  let secret = read_secret_value () in

  print_limits ();

  Printf.printf "%d\n%!" max_value;

  let iteration = ref 0 in
  let running = ref true in

  while !running do
    match read_trimmed_line () with
    | None -> running := false
    | Some line ->
        if String.length line > 0 && line.[0] = '!' then begin
          let rest = String.sub line 1 (String.length line - 1) in
          match parse_long rest with
          | None -> finish re "Invalid data"
          | Some answer ->
              if answer = secret then finish ac "" else finish wa ("!" ^ string_of_int secret)
        end
        else begin
          if !iteration > max_tries then finish tle "Exceeded number of allowed iterations";
          match parse_long line with
          | None -> finish re "Invalid data"
          | Some value ->
              Printf.printf "%s\n%!" (if secret < value then "<" else ">=");
              incr iteration
        end
  done;

  finish re "Unexpected flow"
