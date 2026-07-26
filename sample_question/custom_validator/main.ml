(* Interactive binary search: guess the secret number the validator is holding.
   Every message must be flushed immediately, or the two sides deadlock. *)

let () =
  let upper = ref (int_of_string (String.trim (input_line stdin))) in
  let lower = ref 1 in

  while !lower < !upper do
    let number = !lower + ((!upper - !lower) / 2) + 1 in

    Printf.printf "%d\n%!" number;

    let answer = String.trim (input_line stdin) in

    if answer = "<" then upper := number - 1
    else if answer = ">=" then lower := number
    else begin
      Printf.eprintf "Invalid answer from validator: %s\n%!" answer;
      exit 1
    end
  done;

  Printf.printf "!%d\n%!" !lower;
  exit 0
