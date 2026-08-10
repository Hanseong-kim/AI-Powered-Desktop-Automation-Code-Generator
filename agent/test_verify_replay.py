"""Small non-GUI checks for verify_replay's type-value verdict policy."""
import sys

sys.path.insert(0, "agent")
from verify_replay import classify_value_result  # noqa: E402


def check(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print("PASS ", label)


def main():
    check("readable matching value", classify_value_result(False, "asdf", "asdf"), "match")
    check("readable mismatch", classify_value_result(False, "asdf", ""), "mismatch")
    check("password is opaque", classify_value_result(True, "asdf", ""), "opaque")


if __name__ == "__main__":
    main()
