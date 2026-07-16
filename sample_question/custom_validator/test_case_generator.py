from math import ceil, log2
from pathlib import Path
from random import randint

MAX_VALUE = 10**8
NUMBER_TESTCASES = 20

# Save the Path object to a variable so we can use it later
out_dir = Path("in")
out_dir.mkdir(parents=True, exist_ok=True)

for case in range(NUMBER_TESTCASES):
    max_value = randint(1, MAX_VALUE)
    secret = randint(1, max_value)
    max_tries = int(ceil(log2(max_value)))

    # Use the / operator to safely join the directory and the filename
    file_path = out_dir / f"{case + 1:03d}.in"

    with open(file_path, "w", encoding="utf-8") as tc:
        print(f"{max_value}", file=tc)
        print(f"{max_tries}", file=tc)
        print(f"{secret}", file=tc)
    print(f"Test case {case + 1} with secret {secret} between 1 and {max_value}")
print(f"{NUMBER_TESTCASES} test cases created")
