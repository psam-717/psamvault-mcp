"""Password generation: class guarantees, bounds, and the safe symbol set.

No vault and no network. The generator is the secrets module plus a shuffle.
"""

import string

import pytest

from mcp_server.password_gen import (
    DEFAULT_LENGTH,
    MAX_LENGTH,
    SAFE_SYMBOLS,
    FORBIDDEN_SITE_CHARS,
    generate_password,
    length_problem,
    site_name_problem,
)

_FORBIDDEN_SYMBOLS = set("\"'\\`$% ")
_SAMPLES = 80


def test_default_length_matches_the_cli_generate_default():
    assert DEFAULT_LENGTH == 20
    assert MAX_LENGTH == 128


def test_safe_symbols_are_punctuation_minus_the_excluded_set():
    assert SAFE_SYMBOLS == "".join(ch for ch in string.punctuation if ch not in _FORBIDDEN_SYMBOLS)
    assert not (set(SAFE_SYMBOLS) & _FORBIDDEN_SYMBOLS)
    assert " " not in SAFE_SYMBOLS
    assert set("\"'\\`$%") <= _FORBIDDEN_SYMBOLS


def test_site_name_rules_match_the_cli_forbidden_set():
    assert site_name_problem("github.com") is None
    assert site_name_problem("my-site_1") is None
    assert site_name_problem("email@gmail.com") is None
    assert site_name_problem("") is not None
    assert site_name_problem("   ") is not None
    assert site_name_problem(None) is not None
    for char in sorted(FORBIDDEN_SITE_CHARS):
        assert site_name_problem(f"ok{char}name") is not None, repr(char)


def test_length_bounds():
    assert length_problem(4, enabled=4) is None
    assert length_problem(128, enabled=1) is None
    assert length_problem(3, enabled=4) is not None
    assert length_problem(0, enabled=1) is not None
    assert length_problem(-1, enabled=1) is not None
    assert length_problem(129, enabled=1) is not None
    assert length_problem(True, enabled=1) is not None
    assert length_problem("20", enabled=1) is not None
    assert length_problem(8, enabled=0) is not None


def test_every_enabled_class_appears_across_the_minimum_length():
    for _ in range(_SAMPLES):
        password = generate_password(4, upper=True, lower=True, digits=True, symbols=True)
        assert len(password) == 4
        assert any(ch in string.ascii_uppercase for ch in password)
        assert any(ch in string.ascii_lowercase for ch in password)
        assert any(ch in string.digits for ch in password)
        assert any(ch in SAFE_SYMBOLS for ch in password)


def test_disabled_classes_are_absent():
    for _ in range(_SAMPLES):
        password = generate_password(24, upper=False, lower=True, digits=False, symbols=False)
        assert password == password.lower()
        assert not any(ch in string.digits for ch in password)
        assert not any(ch in SAFE_SYMBOLS for ch in password)


def test_default_symbols_stay_inside_the_safe_set():
    seen = set()
    for _ in range(_SAMPLES):
        password = generate_password(32, upper=False, lower=False, digits=False, symbols=True)
        seen.update(password)
        assert set(password) <= set(SAFE_SYMBOLS)
        assert not (set(password) & _FORBIDDEN_SYMBOLS)
    assert seen == set(SAFE_SYMBOLS)


def test_all_classes_off_is_refused():
    with pytest.raises(ValueError, match="At least one"):
        generate_password(20, upper=False, lower=False, digits=False, symbols=False)


def test_two_generations_are_not_identical():
    passwords = {
        generate_password(20)
        for _ in range(5)
    }
    assert len(passwords) > 1
