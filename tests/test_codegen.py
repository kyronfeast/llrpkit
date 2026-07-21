"""Codegen reproducibility and registry consistency checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from llrpkit.constants import IMPINJ_PEN
from llrpkit.protocol import codec

REPO = Path(__file__).resolve().parents[1]
GENERATOR = REPO / "codegen" / "generate.py"


@pytest.mark.skipif(not GENERATOR.exists(), reason="codegen sources not present")
def test_generated_modules_match_definitions() -> None:
    """`generate.py --check` proves committed code is exactly what the defs produce."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_parameter_registry_sanity() -> None:
    for type_num, cls in codec.PARAMETER_REGISTRY.items():
        assert 0 <= type_num < 1024
        assert type_num != codec.TYPE_CUSTOM
        assert type_num == cls.PARAM_TYPE
        assert (type_num < 128) == cls.IS_TV, f"{cls.__name__} TV flag inconsistent"


def test_custom_registries_are_impinj_only() -> None:
    for vendor, _subtype in codec.CUSTOM_PARAMETER_REGISTRY:
        assert vendor == IMPINJ_PEN
    for vendor, _subtype in codec.CUSTOM_MESSAGE_REGISTRY:
        assert vendor == IMPINJ_PEN


def test_expected_coverage_present() -> None:
    # Core LLRP 1.0.1: 39 messages, 107 parameters.
    assert len(codec.MESSAGE_REGISTRY) >= 39
    assert len(codec.PARAMETER_REGISTRY) >= 107
    # Octane essentials.
    assert (IMPINJ_PEN, 21) in codec.CUSTOM_MESSAGE_REGISTRY  # ENABLE_EXTENSIONS
    assert (IMPINJ_PEN, 23) in codec.CUSTOM_PARAMETER_REGISTRY  # InventorySearchMode
    assert (IMPINJ_PEN, 50) in codec.CUSTOM_PARAMETER_REGISTRY  # TagReportContentSelector
    assert (IMPINJ_PEN, 57) in codec.CUSTOM_PARAMETER_REGISTRY  # ImpinjPeakRSSI
