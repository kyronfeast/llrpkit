"""Reader policy: decide which tags a reader should keep and which to ignore.

The problem this solves, in warehouse terms: *line 4 should only see pails —
it should ignore pickles-fresh, ingredients, and everything else.* That is an
**allow-list per antenna, keyed by item category**, and it is enforced
host-side (in llrpkit, next to the readers) so ignored tags are dropped
before they ever reach MQTT, a webhook, or an ERP — keeping downstream
systems light.

Two pieces compose the policy:

* An :class:`ItemCatalog` answers "what category is this tag?" It matches a
  tag by (most specific first) exact EPC, GS1 GTIN, GS1 company prefix, or
  EPC hex prefix — the GTIN and company-prefix forms come straight from
  :func:`llrpkit.epc.decode_epc`, so a catalog can be written in business
  terms (a GTIN per product) rather than raw hex. Unmatched tags get the
  category ``"unknown"``.
* An :class:`AntennaPolicy` per port says ``allow`` or ``deny`` a set of
  categories, with an optional per-antenna minimum RSSI. Ports with no
  policy pass everything (a reader is not silenced by omission).

:class:`ReaderPolicy` ties them together, adds global rules (a floor RSSI,
whether to ignore ``unknown`` everywhere), evaluates a
:class:`~llrpkit.inventory.TagReport` into a :class:`Decision`, and counts
every drop by antenna, category, and reason so nothing is filtered silently.

Everything is JSON-serializable (:meth:`ReaderPolicy.to_dict` /
:meth:`ReaderPolicy.from_dict`), which is the format the dashboard editor and
the ``--policy FILE`` CLI option read and write, and the shape an exported
product catalog drops into.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llrpkit.epc import decode_epc
from llrpkit.inventory import TagReport

__all__ = [
    "UNKNOWN_CATEGORY",
    "AntennaPolicy",
    "CatalogEntry",
    "Decision",
    "ItemCatalog",
    "ReaderPolicy",
]

#: Category assigned to any tag the catalog does not recognize.
UNKNOWN_CATEGORY = "unknown"

# Match kinds, most specific first — this order is the resolution priority.
_MATCH_ORDER = ("epc", "gtin", "company_prefix", "epc_prefix")


@dataclass(frozen=True)
class CatalogEntry:
    """One catalog rule: a tag matching ``value`` by ``match`` is ``category``.

    ``match`` is one of ``"epc"`` (exact EPC hex), ``"gtin"`` (the GTIN-14
    from a GS1 decode), ``"company_prefix"`` (GS1 company prefix), or
    ``"epc_prefix"`` (a leading EPC hex substring — the fallback for
    non-GS1 tags). ``label`` is an optional human name for the specific item.
    """

    match: str
    value: str
    category: str
    label: str = ""

    def __post_init__(self) -> None:
        if self.match not in _MATCH_ORDER:
            raise ValueError(f"match must be one of {_MATCH_ORDER}, got {self.match!r}")
        if not self.value:
            raise ValueError("catalog entry value must not be empty")
        if not self.category:
            raise ValueError("catalog entry category must not be empty")

    def to_dict(self) -> dict[str, str]:
        out = {"match": self.match, "value": self.value, "category": self.category}
        if self.label:
            out["label"] = self.label
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CatalogEntry:
        return cls(
            match=str(data["match"]),
            value=str(data["value"]),
            category=str(data["category"]),
            label=str(data.get("label", "")),
        )


@dataclass
class ItemCatalog:
    """Resolve a tag to a business category from a list of catalog rules.

    Build it directly from :class:`CatalogEntry` objects, or load an exported
    product list with :meth:`from_rows`. Resolution tries exact EPC first,
    then GTIN, then company prefix, then EPC prefix; within a kind the
    longest (most specific) matching value wins.
    """

    entries: list[CatalogEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        # Index by match kind; values normalized to lowercase hex/text.
        self._exact: dict[str, CatalogEntry] = {}
        self._gtin: dict[str, CatalogEntry] = {}
        self._company: dict[str, CatalogEntry] = {}
        self._prefix: list[CatalogEntry] = []
        for entry in self.entries:
            value = entry.value.lower()
            if entry.match == "epc":
                self._exact[value] = entry
            elif entry.match == "gtin":
                self._gtin[value] = entry
            elif entry.match == "company_prefix":
                self._company[value] = entry
            else:  # epc_prefix
                self._prefix.append(entry)
        # Longest prefix first so the most specific rule wins.
        self._prefix.sort(key=lambda e: len(e.value), reverse=True)

    @property
    def categories(self) -> set[str]:
        """Every category named by the catalog."""
        return {entry.category for entry in self.entries}

    def classify(self, tag: TagReport) -> tuple[str, str]:
        """Return ``(category, item_label)`` for a tag.

        ``category`` is :data:`UNKNOWN_CATEGORY` when nothing matches;
        ``item_label`` is the matched entry's label (or ``""``).
        """
        epc_hex = tag.epc_hex.lower()
        if epc_hex in self._exact:
            e = self._exact[epc_hex]
            return e.category, e.label
        decoded = decode_epc(tag.epc)
        if decoded is not None:
            gtin = decoded.fields.get("gtin")
            if gtin and gtin.lower() in self._gtin:
                e = self._gtin[gtin.lower()]
                return e.category, e.label
            company = decoded.fields.get("company_prefix")
            if company and company.lower() in self._company:
                e = self._company[company.lower()]
                return e.category, e.label
        for entry in self._prefix:
            if epc_hex.startswith(entry.value.lower()):
                return entry.category, entry.label
        return UNKNOWN_CATEGORY, ""

    def to_list(self) -> list[dict[str, str]]:
        return [entry.to_dict() for entry in self.entries]

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]]) -> ItemCatalog:
        """Build a catalog from dict rows (an exported product list).

        Each row needs ``match``, ``value``, ``category`` (and optional
        ``label``); rows missing a category are skipped.
        """
        entries = [CatalogEntry.from_dict(r) for r in rows if r.get("category")]
        return cls(entries=entries)


@dataclass(frozen=True)
class Decision:
    """The outcome of evaluating one tag against a policy."""

    keep: bool
    category: str
    #: Why a tag was ignored (``"category-not-allowed"``, ``"weak-rssi"``,
    #: ``"category-denied"``, ``"unknown-ignored"``); empty when kept.
    reason: str = ""
    item_label: str = ""

    @property
    def ignored(self) -> bool:
        return not self.keep


@dataclass
class AntennaPolicy:
    """Per-antenna rule: allow or deny a set of categories, with an RSSI floor.

    ``mode`` is ``"allow"`` (keep only the listed categories) or ``"deny"``
    (keep everything except the listed categories). ``min_rssi_dbm`` drops
    reads fainter than the threshold on this antenna (``None`` = no floor).
    """

    mode: str = "allow"
    categories: set[str] = field(default_factory=set)
    min_rssi_dbm: float | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("allow", "deny"):
            raise ValueError(f"mode must be 'allow' or 'deny', got {self.mode!r}")
        self.categories = {str(c) for c in self.categories}

    def allows(self, category: str) -> bool:
        if self.mode == "allow":
            return category in self.categories
        return category not in self.categories

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "categories": sorted(self.categories)}
        if self.min_rssi_dbm is not None:
            out["min_rssi_dbm"] = self.min_rssi_dbm
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AntennaPolicy:
        return cls(
            mode=str(data.get("mode", "allow")),
            categories=set(data.get("categories", [])),
            min_rssi_dbm=data.get("min_rssi_dbm"),
        )


@dataclass
class ReaderPolicy:
    """A complete reader policy: catalog + per-antenna rules + global floors.

    Evaluate a tag with :meth:`evaluate`; the returned :class:`Decision`
    says keep or ignore and why. Every ignore is tallied in :attr:`drops`
    (by antenna, category, and reason) so filtering is always accountable.
    """

    catalog: ItemCatalog = field(default_factory=ItemCatalog)
    antennas: dict[int, AntennaPolicy] = field(default_factory=dict)
    #: Global RSSI floor applied on every antenna (in addition to per-antenna).
    min_rssi_dbm: float | None = None
    #: Ignore tags the catalog cannot classify, on every antenna.
    ignore_unknown: bool = False
    #: Master switch; when False the policy keeps everything (still classifies).
    enabled: bool = True

    def __post_init__(self) -> None:
        self.antennas = {int(k): v for k, v in self.antennas.items()}
        self.drops: Counter[str] = Counter()
        self.kept = 0

    # -- evaluation --------------------------------------------------------

    def evaluate(self, tag: TagReport) -> Decision:
        """Decide keep/ignore for one tag and record the outcome."""
        category, label = self.catalog.classify(tag)
        if not self.enabled:
            self.kept += 1
            return Decision(keep=True, category=category, item_label=label)

        reason = self._reject_reason(tag, category)
        if reason is None:
            self.kept += 1
            return Decision(keep=True, category=category, item_label=label)

        self.drops[f"antenna:{tag.antenna}"] += 1
        self.drops[f"category:{category}"] += 1
        self.drops[f"reason:{reason}"] += 1
        self.drops["total"] += 1
        return Decision(keep=False, category=category, reason=reason, item_label=label)

    def _reject_reason(self, tag: TagReport, category: str) -> str | None:
        rssi = tag.rssi_dbm
        if self.min_rssi_dbm is not None and rssi is not None and rssi < self.min_rssi_dbm:
            return "weak-rssi"
        if self.ignore_unknown and category == UNKNOWN_CATEGORY:
            return "unknown-ignored"
        ap = self.antennas.get(tag.antenna) if tag.antenna is not None else None
        if ap is not None:
            if ap.min_rssi_dbm is not None and rssi is not None and rssi < ap.min_rssi_dbm:
                return "weak-rssi"
            if not ap.allows(category):
                return "category-denied" if ap.mode == "deny" else "category-not-allowed"
        return None

    def reset_counters(self) -> None:
        self.drops.clear()
        self.kept = 0

    def counters(self) -> dict[str, Any]:
        """A JSON-ready snapshot of kept/dropped tallies for the dashboard."""
        by_antenna = {
            key.split(":", 1)[1]: n for key, n in self.drops.items() if key.startswith("antenna:")
        }
        by_category = {
            key.split(":", 1)[1]: n for key, n in self.drops.items() if key.startswith("category:")
        }
        by_reason = {
            key.split(":", 1)[1]: n for key, n in self.drops.items() if key.startswith("reason:")
        }
        return {
            "kept": self.kept,
            "dropped": self.drops.get("total", 0),
            "by_antenna": by_antenna,
            "by_category": by_category,
            "by_reason": by_reason,
        }

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "min_rssi_dbm": self.min_rssi_dbm,
            "ignore_unknown": self.ignore_unknown,
            "catalog": self.catalog.to_list(),
            "antennas": {str(port): ap.to_dict() for port, ap in sorted(self.antennas.items())},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReaderPolicy:
        return cls(
            catalog=ItemCatalog.from_rows(data.get("catalog", [])),
            antennas={
                int(port): AntennaPolicy.from_dict(ap)
                for port, ap in dict(data.get("antennas", {})).items()
            },
            min_rssi_dbm=data.get("min_rssi_dbm"),
            ignore_unknown=bool(data.get("ignore_unknown", False)),
            enabled=bool(data.get("enabled", True)),
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> ReaderPolicy:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
