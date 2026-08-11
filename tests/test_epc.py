"""GS1 EPC decoding, anchored on the Tag Data Standard's canonical example."""

from __future__ import annotations

import pytest

from llrpkit.epc import _PARTITIONS, decode_epc, gs1_check_digit


def test_partition_tables_are_internally_consistent() -> None:
    """Company+field bit widths must fill each scheme's fixed layout."""
    totals = {"sgtin-96": 44, "sscc-96": 58, "sgln-96": 41, "grai-96": 44, "giai-96": 82}
    for scheme, table in _PARTITIONS.items():
        assert set(table) == set(range(7)), scheme
        for _, (company_bits, _company_digits, field_bits, _) in table.items():
            assert company_bits + field_bits == totals[scheme], scheme
        digits = [table[p][1] for p in range(7)]
        assert digits == [12, 11, 10, 9, 8, 7, 6], scheme  # GS1 company prefix sizes


def test_canonical_sgtin96_vector() -> None:
    """The TDS's own example: sgtin-96:3.0614141.812345.6789."""
    decoded = decode_epc("3074257bf7194e4000001a85")
    assert decoded is not None
    assert decoded.scheme == "sgtin-96"
    assert decoded.tag_uri == "urn:epc:tag:sgtin-96:3.0614141.812345.6789"
    assert decoded.pure_identity_uri == "urn:epc:id:sgtin:0614141.812345.6789"
    assert decoded.fields["company_prefix"] == "0614141"
    assert decoded.fields["item_reference"] == "812345"
    assert decoded.fields["serial"] == "6789"
    assert decoded.fields["gtin"] == "80614141123458"
    assert decoded.gs1 == "(01) 80614141123458 (21) 6789"


def test_gs1_check_digit_known_values() -> None:
    assert gs1_check_digit("062914140021") == 2  # GTIN-13 body -> 0629141400212
    assert gs1_check_digit("8061414112345") == 8  # the canonical GTIN-14 body
    assert gs1_check_digit("00000000000000000") == 0


def _encode_company_scheme(
    scheme_header: int,
    filter_value: int,
    partition: int,
    company: int,
    field: int,
    tail_bits: list[tuple[int, int]],
) -> bytes:
    """Independent bit packer used only by tests (MSB-first)."""
    from llrpkit.epc import _PARTITIONS

    scheme = {0x30: "sgtin-96", 0x31: "sscc-96", 0x32: "sgln-96", 0x33: "grai-96", 0x34: "giai-96"}[
        scheme_header
    ]
    company_bits, _, field_bits, _ = _PARTITIONS[scheme][partition]
    value = scheme_header
    value = (value << 3) | filter_value
    value = (value << 3) | partition
    value = (value << company_bits) | company
    value = (value << field_bits) | field
    for width, tail in tail_bits:
        value = (value << width) | tail
    return value.to_bytes(12, "big")


def test_sscc96_roundtrip() -> None:
    epc = _encode_company_scheme(0x31, 0, 5, 614141, 1234567890, [(24, 0)])
    decoded = decode_epc(epc)
    assert decoded is not None
    assert decoded.tag_uri == "urn:epc:tag:sscc-96:0.0614141.1234567890"
    body = "1" + "0614141" + "234567890"
    assert decoded.fields["sscc"] == body + str(gs1_check_digit(body))
    assert decoded.gs1 is not None
    assert decoded.gs1.startswith("(00) ")


def test_sgln96_roundtrip() -> None:
    epc = _encode_company_scheme(0x32, 1, 6, 123456, 654321, [(41, 42)])
    decoded = decode_epc(epc)
    assert decoded is not None
    assert decoded.tag_uri == "urn:epc:tag:sgln-96:1.123456.654321.42"
    assert decoded.fields["gln"] == "123456654321" + str(gs1_check_digit("123456654321"))


def test_grai96_and_giai96_roundtrip() -> None:
    grai = decode_epc(_encode_company_scheme(0x33, 2, 6, 950110, 153000, [(38, 7)]))
    assert grai is not None
    assert grai.pure_identity_uri == "urn:epc:id:grai:950110.153000.7"
    giai = decode_epc(_encode_company_scheme(0x34, 0, 6, 950110, 271828182845, []))
    assert giai is not None
    assert giai.pure_identity_uri == "urn:epc:id:giai:950110.271828182845"


def test_gid96_roundtrip() -> None:
    value = (0x35 << 88) | (900100 << 60) | (12345 << 36) | 400
    decoded = decode_epc(value.to_bytes(12, "big"))
    assert decoded is not None
    assert decoded.tag_uri == "urn:epc:tag:gid-96:900100.12345.400"


def test_non_gs1_epcs_return_none() -> None:
    assert decode_epc(b"\xe2" + b"\x00" * 11) is None  # common raw-tag prefix
    assert decode_epc(b"\x30\x00") is None  # wrong length
    assert decode_epc("00" * 12) is None  # header 0x00 is unprogrammed


def test_invalid_partition_returns_none() -> None:
    # header sgtin-96, filter 0, partition 7 (undefined)
    bad = (0x30 << 88) | (7 << 82)
    assert decode_epc(bad.to_bytes(12, "big")) is None


@pytest.mark.parametrize("hexstr", ["3074257bf7194e4000001a85"])
def test_decode_accepts_hex_and_bytes(hexstr: str) -> None:
    assert decode_epc(hexstr) == decode_epc(bytes.fromhex(hexstr))


def test_cli_decode_command() -> None:
    from typer.testing import CliRunner

    from llrpkit.cli import app

    runner = CliRunner()
    good = runner.invoke(app, ["decode", "3074257bf7194e4000001a85"])
    assert good.exit_code == 0, good.output
    assert "urn:epc:tag:sgtin-96:3.0614141.812345.6789" in good.output
    assert "(01) 80614141123458 (21) 6789" in good.output
    bad = runner.invoke(app, ["decode", "e20000000000000000000000"])
    assert bad.exit_code == 1
    assert "not a GS1" in bad.output
