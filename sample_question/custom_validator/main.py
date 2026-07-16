lower = 1
upper = int(input())

while lower < upper:
    number = lower + (upper - lower) // 2 + 1

    print(number, flush=True)
    answer = input().strip()

    if answer == "<":
        upper = number - 1
    elif answer == ">=":
        lower = number
    else:
        raise RuntimeError(f"Invalid answer from validator: {answer!r}")

print(f"!{lower}", flush=True)
exit(0)
