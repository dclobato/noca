import json
import os
import sys
from typing import Any

EXIT_CODES = {
    "AC": 0,
    "WA": 1,
    "TLE": 2,
    "RE": 3,
    "PE": 4,
}


def _EXIT(exitcode: int = None, message: str = None) -> None:
    if message:
        print(message, file=sys.stderr, flush=True)
    exit(exitcode)


def AC(message: str = None) -> None:
    return _EXIT(EXIT_CODES["AC"], message)


def WA(message: str = None) -> None:
    return _EXIT(EXIT_CODES["WA"], message)


def TLE(message: str = None) -> None:
    return _EXIT(EXIT_CODES["TLE"], message)


def RE(message: str = None) -> None:
    return _EXIT(EXIT_CODES["RE"], message)


def PE(message: str = None) -> None:
    return _EXIT(EXIT_CODES["PE"], message)


def optional_int_env(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return int(value)


def optional_json_env(name: str) -> Any | None:
    value = os.environ.get(name)
    if value is None:
        return None
    return json.loads(value)


def read_limits() -> dict[str, Any]:
    return {
        "problem_time_limit": optional_int_env("PROBLEM_TIME_LIMIT"),
        "problem_output_limit": optional_int_env("PROBLEM_OUTPUT_LIMIT"),
        "problem_memory_limit": optional_int_env("PROBLEM_MEMORY_LIMIT"),
        "problem_pid_limit": optional_int_env("PROBLEM_PID_LIMIT"),
        "user_language": os.environ.get("USER_LANGUAGE"),
        "per_language_limits": optional_json_env("PER_LANGUAGE_LIMITS"),
    }


def read_line() -> str | None:
    """Return the next line from the contestant, or None once its output ends."""
    try:
        return input().strip()
    except EOFError:
        return None


def read_secret_value() -> int:
    line = read_line()
    if line is None:
        RE("Invalid secret data")
    try:
        return int(line)
    except ValueError:
        RE("Invalid secret data")


def read_secret_data() -> dict[str, Any]:
    return {
        "max_value": read_secret_value(),
        "max_tries": read_secret_value(),
        "secret": read_secret_value(),
    }


def validate(secret_data: dict[str, Any]) -> None:
    max_value = secret_data["max_value"]
    max_tries = secret_data["max_tries"]
    secret = secret_data["secret"]

    limits = read_limits()
    print(json.dumps(limits), file=sys.stderr, flush=True)

    print(max_value, flush=True)
    # input() signals the end of the contestant's output by raising EOFError, never by
    # returning None, so the loop has to test for it explicitly.
    iteration = 0
    while (user := read_line()) is not None:
        if user.startswith("!"):
            try:
                answer = int(user[1:])
            except ValueError:
                RE("Invalid data")
            if answer == secret:
                AC()
            else:
                WA(f"!{secret}")

        if iteration > max_tries:
            TLE("Exceeded number of allowed iterations")
        try:
            user_value = int(user)
        except ValueError:
            RE("Invalid data")
        if secret < user_value:
            print("<", flush=True)
        else:
            print(">=", flush=True)
        iteration += 1

    RE("Unexpected flow")


if __name__ == "__main__":
    secret_data = read_secret_data()
    validate(secret_data)
