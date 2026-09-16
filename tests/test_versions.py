"""Tests for mcp_server/versions.py — the single version comparator."""

import pytest

from mcp_server.versions import is_newer, vkey


class TestVKey:
    def test_release_ranks_above_prerelease(self):
        assert vkey("0.5.3rc1") < vkey("0.5.3")

    def test_release_ranks_below_postrelease(self):
        assert vkey("0.5.3") < vkey("0.5.3.post1")

    def test_numeric_not_lexical(self):
        assert vkey("0.5.10") > vkey("0.5.9")
        assert vkey("0.10.0") > vkey("0.9.0")

    def test_padding(self):
        assert vkey("1") == vkey("1.0.0")

    def test_leading_v_and_whitespace(self):
        assert vkey(" v1.2.3 ") == vkey("1.2.3")

    @pytest.mark.parametrize("bad", ["", "not-a-version", None, "   "])
    def test_never_raises(self, bad):
        assert isinstance(vkey(bad), tuple)


class TestIsNewer:
    def test_strictly_newer(self):
        assert is_newer("0.5.3", "0.5.2") is True

    def test_equal_is_not_newer(self):
        assert is_newer("0.5.2", "0.5.2") is False

    def test_older_is_not_newer(self):
        assert is_newer("0.5.1", "0.5.2") is False

    def test_prerelease_of_same_version_is_not_an_upgrade(self):
        """A published rc must not be offered as an update over the release."""
        assert is_newer("0.5.3rc1", "0.5.3") is False

    def test_garbage_never_claims_an_upgrade(self):
        assert is_newer("garbage", "0.5.2") is False
