"""GS1 EPC binary decoding: turn raw EPC bits back into business identifiers.

RFID hardware hands you 96 opaque bits; what the warehouse actually wants is
"which GTIN, which serial". This module decodes the common GS1 EPC Tag Data
Standard 96-bit schemes:

======== ======= ==============================================
header   scheme  identifies
======== ======= ==============================================
``0x30`` SGTIN-96 a serialized trade item (GTIN + serial)
``0x31`` SSCC-96  a logistics unit (pallet/carton license plate)
``0x32`` SGLN-96  a physical location (GLN + extension)
``0x33`` GRAI-96  a returnable asset (crate, keg, roll cage)
``0x34`` GIAI-96  an individual asset
``0x35`` GID-96   a general identifier (no GS1 company prefix)
======== ======= ==============================================

Usage::

    from llrpkit.epc import decode_epc

    decoded = decode_epc("3074257bf7194e4000001a85")
    decoded.scheme            # "sgtin-96"
    decoded.tag_uri           # urn:epc:tag:sgtin-96:3.0614141.812345.6789
    decoded.fields["gtin"]    # 80614141123458 (GTIN-14 with check digit)
    decoded.gs1               # (01) 80614141123458 (21) 6789

``decode_epc`` returns ``None`` for EPCs that are not GS1 tag encodings
(many sites program free-form EPCs — a leading ``0xE2`` byte, for example,
is a common raw-tag default, not a GS1 header).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DecodedEPC", "decode_epc", "gs1_check_digit"]

#: Partition table for schemes whose first field is a GS1 company prefix:
#: partition -> (company bits, company digits, second-field bits, second digits).
#: The second field is the item reference (SGTIN), serial reference (SSCC),
#: asset type (GRAI), or location reference (SGLN).
_PARTITIONS = {
    "sgtin-96": {
        0: (40, 12, 4, 1),
        1: (37, 11, 7, 2),
        2: (34, 10, 10, 3),
        3: (30, 9, 14, 4),
        4: (27, 8, 17, 5),
        5: (24, 7, 20, 6),
        6: (20, 6, 24, 7),
    },
    "sscc-96": {
        0: (40, 12, 18, 5),
        1: (37, 11, 21, 6),
        2: (34, 10, 24, 7),
        3: (30, 9, 28, 8),
        4: (27, 8, 31, 9),
        5: (24, 7, 34, 10),
        6: (20, 6, 38, 11),
    },
    "sgln-96": {
        0: (40, 12, 1, 0),
        1: (37, 11, 4, 1),
        2: (34, 10, 7, 2),
        3: (30, 9, 11, 3),
        4: (27, 8, 14, 4),
        5: (24, 7, 17, 5),
        6: (20, 6, 21, 6),
    },
    "grai-96": {
        0: (40, 12, 4, 0),
        1: (37, 11, 7, 1),
        2: (34, 10, 10, 2),
        3: (30, 9, 14, 3),
        4: (27, 8, 17, 4),
        5: (24, 7, 20, 5),
        6: (20, 6, 24, 6),
    },
    "giai-96": {
        0: (40, 12, 42, 13),
        1: (37, 11, 45, 14),
        2: (34, 10, 48, 15),
        3: (30, 9, 52, 16),
        4: (27, 8, 55, 17),
        5: (24, 7, 58, 18),
        6: (20, 6, 62, 19),
    },
}

_HEADERS = {
    0x30: "sgtin-96",
    0x31: "sscc-96",
    0x32: "sgln-96",
    0x33: "grai-96",
    0x34: "giai-96",
    0x35: "gid-96",
}


@dataclass(frozen=True)
class DecodedEPC:
    """A decoded GS1 EPC: URIs, named fields, and a GS1 element string."""

    scheme: str
    tag_uri: str
    pure_identity_uri: str
    fields: dict[str, str]
    #: GS1 element string like ``(01) 80614141123458 (21) 6789`` where the
    #: scheme has a standard one; ``None`` otherwise.
    gs1: str | None = None


def gs1_check_digit(digits: str) -> int:
    """The GS1 mod-10 check digit for a numeric string (GTIN, SSCC, GLN...)."""
    total = 0
    for position, ch in enumerate(reversed(digits)):
        weight = 3 if position % 2 == 0 else 1
        total += int(ch) * weight
    return (10 - total % 10) % 10


class _Bits:
    """MSB-first bit reader over an EPC."""

    def __init__(self, data: bytes) -> None:
        self.value = int.from_bytes(data, "big")
        self.total = len(data) * 8
        self.pos = 0

    def take(self, count: int) -> int:
        if self.pos + count > self.total:
            raise ValueError("EPC too short for its declared scheme")
        shift = self.total - self.pos - count
        self.pos += count
        return (self.value >> shift) & ((1 << count) - 1)


def _company_and_field(bits: _Bits, scheme: str) -> tuple[int, str, str, int, int]:
    """Read filter+partition+company+second-field; returns
    (filter, company_str, field_str, field_value, field_digits)."""
    filter_value = bits.take(3)
    partition = bits.take(3)
    table = _PARTITIONS[scheme]
    if partition not in table:
        raise ValueError(f"invalid {scheme} partition {partition}")
    company_bits, company_digits, field_bits, field_digits = table[partition]
    company = bits.take(company_bits)
    field_value = bits.take(field_bits)
    if company >= 10**company_digits:
        raise ValueError(f"company prefix overflows its {company_digits} digits")
    company_str = str(company).zfill(company_digits)
    field_str = str(field_value).zfill(field_digits) if field_digits else ""
    return filter_value, company_str, field_str, field_value, field_digits


def decode_epc(epc: bytes | str) -> DecodedEPC | None:
    """Decode a GS1 96-bit EPC; ``None`` when it is not a GS1 tag encoding."""
    if isinstance(epc, str):
        epc = bytes.fromhex(epc)
    if len(epc) != 12:
        return None
    scheme = _HEADERS.get(epc[0])
    if scheme is None:
        return None
    bits = _Bits(epc)
    bits.take(8)  # header
    try:
        if scheme == "gid-96":
            manager = bits.take(28)
            object_class = bits.take(24)
            serial = bits.take(36)
            tag_uri = f"urn:epc:tag:gid-96:{manager}.{object_class}.{serial}"
            pure = f"urn:epc:id:gid:{manager}.{object_class}.{serial}"
            return DecodedEPC(
                scheme=scheme,
                tag_uri=tag_uri,
                pure_identity_uri=pure,
                fields={
                    "manager_number": str(manager),
                    "object_class": str(object_class),
                    "serial": str(serial),
                },
            )
        filter_value, company, field_str, field_value, _ = _company_and_field(bits, scheme)
        if scheme == "sgtin-96":
            serial = bits.take(38)
            gtin13 = field_str[0] + company + field_str[1:]  # indicator + prefix + item
            gtin14 = gtin13 + str(gs1_check_digit(gtin13))
            return DecodedEPC(
                scheme=scheme,
                tag_uri=f"urn:epc:tag:sgtin-96:{filter_value}.{company}.{field_str}.{serial}",
                pure_identity_uri=f"urn:epc:id:sgtin:{company}.{field_str}.{serial}",
                fields={
                    "filter": str(filter_value),
                    "company_prefix": company,
                    "item_reference": field_str,
                    "gtin": gtin14,
                    "serial": str(serial),
                },
                gs1=f"(01) {gtin14} (21) {serial}",
            )
        if scheme == "sscc-96":
            bits.take(24)  # reserved
            sscc17 = field_str[0] + company + field_str[1:]  # extension + prefix + serial ref
            sscc18 = sscc17 + str(gs1_check_digit(sscc17))
            return DecodedEPC(
                scheme=scheme,
                tag_uri=f"urn:epc:tag:sscc-96:{filter_value}.{company}.{field_str}",
                pure_identity_uri=f"urn:epc:id:sscc:{company}.{field_str}",
                fields={
                    "filter": str(filter_value),
                    "company_prefix": company,
                    "serial_reference": field_str,
                    "sscc": sscc18,
                },
                gs1=f"(00) {sscc18}",
            )
        if scheme == "sgln-96":
            extension = bits.take(41)
            gln12 = company + field_str
            gln13 = gln12 + str(gs1_check_digit(gln12))
            return DecodedEPC(
                scheme=scheme,
                tag_uri=f"urn:epc:tag:sgln-96:{filter_value}.{company}.{field_str}.{extension}",
                pure_identity_uri=f"urn:epc:id:sgln:{company}.{field_str}.{extension}",
                fields={
                    "filter": str(filter_value),
                    "company_prefix": company,
                    "location_reference": field_str,
                    "gln": gln13,
                    "extension": str(extension),
                },
                gs1=f"(414) {gln13} (254) {extension}",
            )
        if scheme == "grai-96":
            serial = bits.take(38)
            return DecodedEPC(
                scheme=scheme,
                tag_uri=f"urn:epc:tag:grai-96:{filter_value}.{company}.{field_str}.{serial}",
                pure_identity_uri=f"urn:epc:id:grai:{company}.{field_str}.{serial}",
                fields={
                    "filter": str(filter_value),
                    "company_prefix": company,
                    "asset_type": field_str,
                    "serial": str(serial),
                },
            )
        # giai-96: the asset reference is variable-length numeric (unpadded)
        return DecodedEPC(
            scheme=scheme,
            tag_uri=f"urn:epc:tag:giai-96:{filter_value}.{company}.{field_value}",
            pure_identity_uri=f"urn:epc:id:giai:{company}.{field_value}",
            fields={
                "filter": str(filter_value),
                "company_prefix": company,
                "asset_reference": str(field_value),
            },
        )
    except ValueError:
        return None
