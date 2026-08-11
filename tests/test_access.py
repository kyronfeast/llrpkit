"""Tag memory access: Gen2 READ/WRITE/kill via the AccessSpec lifecycle."""

from __future__ import annotations

import pytest

from llrpkit.emulator import EmulatedTag
from llrpkit.exceptions import LLRPTimeoutError
from llrpkit.protocol import messages
from llrpkit.reader import MEMORY_BANKS, Reader, _resolve_bank
from tests.test_hardening import make_emulator

TAG_A = bytes([0xE2, 0x33, 0x0A] + [0] * 9)
TAG_B = bytes([0xE2, 0x33, 0x0B] + [0] * 9)


def two_tags() -> list[EmulatedTag]:
    return [EmulatedTag(epc=TAG_A, antennas=(1,)), EmulatedTag(epc=TAG_B, antennas=(2,))]


def test_bank_names_resolve() -> None:
    assert _resolve_bank("user") == 3
    assert _resolve_bank("EPC") == 1
    assert _resolve_bank(2) == 2
    assert MEMORY_BANKS["reserved"] == 0
    with pytest.raises(ValueError, match="unknown memory bank"):
        _resolve_bank("cellar")
    with pytest.raises(ValueError, match="0-3"):
        _resolve_bank(7)


async def test_read_user_memory_defaults_to_zeroes() -> None:
    async with make_emulator(tags=two_tags()) as emu, Reader("127.0.0.1", emu.port) as reader:
        result = await reader.read_memory(bank="user", word_count=4, target_epc=TAG_A)
        assert result.ok, result.status
        assert result.epc == TAG_A
        assert result.data == b"\x00" * 8


async def test_write_then_read_roundtrip_targets_the_right_tag() -> None:
    async with make_emulator(tags=two_tags()) as emu, Reader("127.0.0.1", emu.port) as reader:
        wrote = await reader.write_memory(
            bank="user", word_pointer=2, data="cafebabe", target_epc=TAG_B
        )
        assert wrote.ok
        assert wrote.epc == TAG_B
        assert wrote.words_written == 2
        back = await reader.read_memory(bank="user", word_pointer=2, word_count=2, target_epc=TAG_B)
        assert back.ok
        assert back.data == bytes.fromhex("cafebabe")
        other = await reader.read_memory(
            bank="user", word_pointer=2, word_count=2, target_epc=TAG_A
        )
        assert other.ok
        assert other.data == b"\x00" * 4  # untouched neighbor


async def test_read_tid_matches_reported_serialized_tid() -> None:
    async with make_emulator(tags=two_tags()) as emu, Reader("127.0.0.1", emu.port) as reader:
        result = await reader.read_memory(bank="tid", target_epc=TAG_A)
        assert result.ok
        assert result.data is not None
        assert result.data.startswith(b"\xe2\x80\x11\x05")  # Impinj-style TID header


async def test_write_epc_relabels_the_tag() -> None:
    new_epc = bytes([0xE2, 0x99] + [0x11] * 10)
    async with make_emulator(tags=two_tags()) as emu, Reader("127.0.0.1", emu.port) as reader:
        result = await reader.write_epc(new_epc, target_epc=TAG_A)
        assert result.ok, result.status
        epcs = {t.epc for t in emu.tags}
        assert new_epc in epcs
        assert TAG_A not in epcs
        # and the re-labeled tag is now addressable by its new identity
        back = await reader.read_memory(bank="user", word_count=1, target_epc=new_epc)
        assert back.ok
        assert back.epc == new_epc


async def test_wrong_access_password_is_a_clean_failure() -> None:
    async with make_emulator(tags=two_tags()) as emu:
        emu.set_tag_passwords(TAG_A, access=0xDEADBEEF)
        async with Reader("127.0.0.1", emu.port) as reader:
            denied = await reader.read_memory(bank="user", target_epc=TAG_A)
            assert not denied.ok
            assert denied.status == "Nonspecific_Tag_Error"
            allowed = await reader.read_memory(
                bank="user", target_epc=TAG_A, access_password=0xDEADBEEF
            )
            assert allowed.ok


async def test_out_of_range_write_reports_memory_overrun() -> None:
    async with make_emulator(tags=two_tags()) as emu, Reader("127.0.0.1", emu.port) as reader:
        result = await reader.write_memory(
            bank="user", word_pointer=31, data="aaaabbbbcccc", target_epc=TAG_A
        )
        assert result.status == "Tag_Memory_Overrun_Error"


async def test_kill_removes_the_tag_for_good() -> None:
    async with make_emulator(tags=two_tags()) as emu:
        emu.set_tag_passwords(TAG_B, kill=0x0BADF00D)
        async with Reader("127.0.0.1", emu.port) as reader:
            with pytest.raises(ValueError, match="kill password"):
                await reader.kill_tag(kill_password=0, target_epc=TAG_B)
            result = await reader.kill_tag(kill_password=0x0BADF00D, target_epc=TAG_B)
            assert result.ok
            assert all(t.epc != TAG_B for t in emu.tags)


async def test_no_matching_tag_times_out_cleanly_and_cleans_up() -> None:
    async with make_emulator(tags=two_tags()) as emu, Reader("127.0.0.1", emu.port) as reader:
        with pytest.raises(LLRPTimeoutError, match="no tag answered"):
            await reader.read_memory(bank="user", target_epc=b"\x00\x11\x22", timeout=1.2)
        # the managed AccessSpec must not be left behind on the reader
        from llrpkit.client import check_status

        response = check_status(await reader.client.transact(messages.GET_ACCESSSPECS()))
        assert isinstance(response, messages.GET_ACCESSSPECS_RESPONSE)
        assert response.access_specs == []


async def test_untargeted_read_answers_from_some_tag() -> None:
    async with make_emulator(tags=two_tags()) as emu, Reader("127.0.0.1", emu.port) as reader:
        result = await reader.read_memory(bank="epc", word_pointer=2, word_count=6)
        assert result.ok
        assert result.data in (TAG_A, TAG_B)  # EPC bank read returns the EPC itself


def test_cli_write_then_read_roundtrip() -> None:
    from typer.testing import CliRunner

    from llrpkit.cli import app
    from tests.test_cli_e2e import EmulatorThread

    runner = CliRunner()
    target = "e2000017010b016210000003"  # one tag of the default population
    with EmulatorThread() as emu:
        wrote = runner.invoke(
            app,
            [
                "write",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--data",
                "beef0042",
                "--bank",
                "user",
                "--word-pointer",
                "0",
                "--epc",
                target,
            ],
        )
        assert wrote.exit_code == 0, wrote.output
        assert "2 word(s) written" in wrote.output
        read_back = runner.invoke(
            app,
            [
                "read",
                "127.0.0.1",
                "--port",
                str(emu.port),
                "--bank",
                "user",
                "--words",
                "2",
                "--epc",
                target,
            ],
        )
        assert read_back.exit_code == 0, read_back.output
        assert "beef0042" in read_back.output
        assert target in read_back.output
