(* Minimum-cost route: start -> all K mandatory houses (any order) -> end,
   using Manhattan distance. Solved with a Held-Karp bitmask DP. *)

let read_int_token () = Scanf.scanf " %d" (fun x -> x)

let manhattan (ax, ay) (bx, by) = abs (ax - bx) + abs (ay - by)

let () =
  let _l = read_int_token () in
  let _c = read_int_token () in
  let k = read_int_token () in

  let sx = read_int_token () in
  let sy = read_int_token () in
  let ex = read_int_token () in
  let ey = read_int_token () in
  let start = (sx, sy) in
  let finish = (ex, ey) in

  (* Array.init applies its function in an unspecified order, so the houses are
     filled with an explicit loop to keep the token reads sequential. *)
  let houses = Array.make (max k 1) (0, 0) in
  for i = 0 to k - 1 do
    let x = read_int_token () in
    let y = read_int_token () in
    houses.(i) <- (x, y)
  done;

  if k = 0 then print_endline (string_of_int (manhattan start finish))
  else begin
    (* pts: [0: start; 1..k: houses; k+1: end] *)
    let n = k + 2 in
    let pts = Array.make n (0, 0) in
    pts.(0) <- start;
    for i = 0 to k - 1 do
      pts.(i + 1) <- houses.(i)
    done;
    pts.(k + 1) <- finish;

    let dist = Array.make_matrix n n 0 in
    for i = 0 to n - 1 do
      for j = 0 to n - 1 do
        dist.(i).(j) <- manhattan pts.(i) pts.(j)
      done
    done;

    let inf = 1000000000 in
    let limit = 1 lsl k in
    let dp = Array.make_matrix limit k inf in

    for i = 0 to k - 1 do
      dp.(1 lsl i).(i) <- dist.(0).(i + 1)
    done;

    for mask = 0 to limit - 1 do
      for u = 0 to k - 1 do
        if mask land (1 lsl u) <> 0 && dp.(mask).(u) < inf then
          for v = 0 to k - 1 do
            if mask land (1 lsl v) = 0 then begin
              let next_mask = mask lor (1 lsl v) in
              let new_cost = dp.(mask).(u) + dist.(u + 1).(v + 1) in
              if new_cost < dp.(next_mask).(v) then dp.(next_mask).(v) <- new_cost
            end
          done
      done
    done;

    let full_mask = limit - 1 in
    let best = ref inf in
    for i = 0 to k - 1 do
      let cost = dp.(full_mask).(i) + dist.(i + 1).(k + 1) in
      if cost < !best then best := cost
    done;

    print_endline (string_of_int !best)
  end
