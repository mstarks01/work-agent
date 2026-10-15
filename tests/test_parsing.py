"""Reading a value out of a string or a file this service does not control.

``ascii_int`` has three callers, and the two shapes ``str.isdigit`` gets wrong.
Each caller's own seam is asserted beside the rule, because what the rule
returns and what the caller does about it are different claims. The other
readers are asserted here once, and each caller's tests assert its refusal.
"""

from __future__ import annotations

import pytest

from analysis_service.parsing import ascii_int, is_version, signer, strings

#: The shapes ``str.isdigit`` passes and ``int`` does not accept as written.
#: ``"²"`` raises a ``ValueError``; a fullwidth digit is a *second spelling* of
#: a number ``int`` does accept, so a guard reading ``isdigit`` let one value
#: through under two names; and ``int`` refuses a string past 4300 digits.
ISDIGIT_TRAPS = ["²", "１００", "٤٢", "9" * 5000]


class TestAsciiInt:
    @pytest.mark.parametrize("value", ISDIGIT_TRAPS)
    def test_every_shape_isdigit_gets_wrong_is_refused(self, value):
        assert value.isdigit(), "this case only means something while isdigit passes it"
        assert ascii_int(value, max_digits=19) is None

    @pytest.mark.parametrize(("value", "expected"), [("0", 0), ("7", 7), ("100", 100)])
    def test_an_ascii_number_reads_as_itself(self, value, expected):
        assert ascii_int(value, max_digits=19) == expected

    def test_the_bound_is_the_callers(self):
        assert ascii_int("999", max_digits=3) == 999
        assert ascii_int("9999", max_digits=3) is None

    @pytest.mark.parametrize("value", ["", " 1", "1 ", "+1", "-1", "1.0", "1e3", "abc"])
    def test_what_does_not_spell_a_number_reads_as_none(self, value):
        assert ascii_int(value, max_digits=19) is None


@pytest.mark.parametrize("value", [7.0, True, "7", None, 8])
def test_a_version_is_the_expected_integer_and_nothing_equal_to_it(value):
    assert is_version(7, 7)
    assert not is_version(value, 7)
    assert not is_version(True, 1)


@pytest.mark.parametrize("value", ["ab", ("a",), ["a", 1], None])
def test_strings_is_a_list_of_strings_only(value):
    assert strings([])
    assert strings(["a", "b"])
    assert not strings(value)


class TestSigner:
    @pytest.mark.parametrize("value", [None, ""])
    def test_none_and_the_empty_string_are_unsigned(self, value):
        assert signer(value) is None

    def test_a_name_is_the_signer(self):
        assert signer("mstarks01") == "mstarks01"

    @pytest.mark.parametrize("value", [" ", "\t", True, ["mstarks01"]])
    def test_a_value_that_names_nobody_raises(self, value):
        with pytest.raises(ValueError, match="names nobody"):
            signer(value)
