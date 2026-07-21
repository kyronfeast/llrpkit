"""Smoke tests for package metadata and the stable core modules."""

from __future__ import annotations

import re

import llrpkit


def test_version_is_pep440ish() -> None:
    assert re.match(r"^\d+\.\d+\.\d+", llrpkit.__version__)


def test_protocol_constants() -> None:
    assert llrpkit.LLRP_PORT == 5084
    assert llrpkit.LLRP_TLS_PORT == 5085
    assert llrpkit.MESSAGE_HEADER_LEN == 10
    assert llrpkit.IMPINJ_PEN == 25882


def test_llrp_versions() -> None:
    assert llrpkit.LLRPVersion.V1_0_1.value == 1
    assert llrpkit.LLRPVersion.V1_1.value == 2


def test_exceptions_share_base() -> None:
    for exc_type in (
        llrpkit.LLRPConnectionError,
        llrpkit.LLRPTimeoutError,
        llrpkit.MessageDecodeError,
        llrpkit.MessageEncodeError,
        llrpkit.LLRPStatusError,
        llrpkit.CapabilityError,
    ):
        assert issubclass(exc_type, llrpkit.LLRPError)


def test_status_error_carries_reader_detail() -> None:
    err = llrpkit.LLRPStatusError(101, "M_UnsupportedMessage")
    assert err.status_code == 101
    assert "M_UnsupportedMessage" in str(err)


def test_status_error_without_description() -> None:
    err = llrpkit.LLRPStatusError(1)
    assert str(err) == "reader returned status 1"
