"""Tests for m4bmaker.utils — ffmpeg/ffprobe detection and logging."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from m4bmaker.errors import M4BError
from m4bmaker.utils import (
    _parse_pid_from_temp_dir_name,
    _process_is_alive,
    _sweep_stale_temp_roots,
    find_binary,
    find_ffmpeg,
    find_ffprobe,
    get_temp_root,
    log,
    safe_input,
    sanitize_filename_component,
)


class TestFindFfmpeg:
    def test_returns_path_when_found(self) -> None:
        with patch("m4bmaker.utils._which", return_value="/usr/bin/ffmpeg"):
            result = find_ffmpeg()
        assert result == "/usr/bin/ffmpeg"

    def test_exits_when_not_found(self) -> None:
        with patch("m4bmaker.utils._which", return_value=None):
            with pytest.raises(SystemExit, match="ffmpeg not found"):
                find_ffmpeg()

    def test_exit_message_contains_install_hints(self) -> None:
        with patch("m4bmaker.utils._which", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                find_ffmpeg()
        msg = str(exc_info.value)
        assert "brew install ffmpeg" in msg or "apt install ffmpeg" in msg


class TestFindFfprobe:
    def test_returns_path_when_found(self) -> None:
        with patch("m4bmaker.utils._which", return_value="/usr/bin/ffprobe"):
            result = find_ffprobe()
        assert result == "/usr/bin/ffprobe"

    def test_exits_when_not_found(self) -> None:
        with patch("m4bmaker.utils._which", return_value=None):
            with pytest.raises(SystemExit, match="ffprobe not found"):
                find_ffprobe()

    def test_exit_message_mentions_ffmpeg(self) -> None:
        with patch("m4bmaker.utils._which", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                find_ffprobe()
        assert "ffmpeg" in str(exc_info.value)


class TestFindBinary:
    def test_returns_path_when_found(self) -> None:
        with patch("m4bmaker.utils._which", return_value="/usr/local/bin/whisper-cli"):
            assert find_binary("whisper-cli") == "/usr/local/bin/whisper-cli"

    def test_returns_none_when_not_found_instead_of_exiting(self) -> None:
        with patch("m4bmaker.utils._which", return_value=None):
            assert find_binary("whisper-cli") is None

    def test_delegates_to_which_with_the_given_name(self) -> None:
        with patch("m4bmaker.utils._which", return_value=None) as mock_which:
            find_binary("some-tool")
        mock_which.assert_called_once_with("some-tool")


class TestLog:
    def test_prints_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        log("hello world")
        captured = capsys.readouterr()
        assert "hello world" in captured.out


# ---------------------------------------------------------------------------
# sanitize_filename_component
# ---------------------------------------------------------------------------


class TestSanitizeFilenameComponent:
    def test_plain_string_unchanged(self) -> None:
        assert sanitize_filename_component("My Book Title") == "My Book Title"

    def test_strips_nul_byte(self) -> None:
        assert sanitize_filename_component("A\x00B") == "AB"

    def test_strips_control_characters(self) -> None:
        assert sanitize_filename_component("A\x01\x02B") == "AB"

    def test_strips_del_character(self) -> None:
        assert sanitize_filename_component("A\x7fB") == "AB"

    @pytest.mark.parametrize("char", ["/", "\\", ":", "*", "?", '"', "<", ">", "|"])
    def test_reserved_chars_replaced_with_dash(self, char: str) -> None:
        result = sanitize_filename_component(f"A{char}B")
        assert result == "A-B"
        assert char not in result

    def test_multiple_reserved_chars(self) -> None:
        assert sanitize_filename_component("A/B:C*D") == "A-B-C-D"

    def test_collapses_whitespace_runs(self) -> None:
        assert sanitize_filename_component("A    B") == "A B"

    def test_tabs_and_newlines_stripped_as_control_chars(self) -> None:
        # Tab/newline are control characters (\\x00-\\x1f) and are stripped
        # entirely, same as other control chars — not collapsed to a space.
        assert sanitize_filename_component("A\t\nB") == "AB"

    def test_strips_leading_trailing_dots(self) -> None:
        assert sanitize_filename_component("...Title...") == "Title"

    def test_strips_leading_trailing_spaces(self) -> None:
        assert sanitize_filename_component("  Title  ") == "Title"

    def test_strips_leading_trailing_dots_and_spaces_combined(self) -> None:
        assert sanitize_filename_component(" . Title . ") == "Title"

    def test_empty_string_becomes_untitled(self) -> None:
        assert sanitize_filename_component("") == "Untitled"

    def test_single_dot_becomes_untitled(self) -> None:
        assert sanitize_filename_component(".") == "Untitled"

    def test_double_dot_becomes_untitled(self) -> None:
        assert sanitize_filename_component("..") == "Untitled"

    def test_only_dots_and_spaces_becomes_untitled(self) -> None:
        assert sanitize_filename_component(" ... ") == "Untitled"

    def test_only_reserved_chars_does_not_crash(self) -> None:
        # "////" -> "----" after replacement, which is a valid (if odd)
        # filename component — no crash, no exception.
        result = sanitize_filename_component("////")
        assert result == "----"

    def test_truncates_to_120_chars(self) -> None:
        long_name = "A" * 200
        result = sanitize_filename_component(long_name)
        assert len(result) == 120

    def test_truncation_happens_after_cleanup(self) -> None:
        # 130 reserved chars -> 130 dashes -> truncated to 120.
        long_name = "/" * 130
        result = sanitize_filename_component(long_name)
        assert len(result) == 120
        assert result == "-" * 120

    def test_unicode_preserved(self) -> None:
        assert sanitize_filename_component("Café — Ëxämple") == "Café — Ëxämple"

    def test_windows_reserved_path_chars_all_covered(self) -> None:
        raw = 'a/b\\c:d*e?f"g<h>i|j'
        result = sanitize_filename_component(raw)
        for char in '/\\:*?"<>|':
            assert char not in result


# ---------------------------------------------------------------------------
# get_temp_root
# ---------------------------------------------------------------------------


class TestGetTempRoot:
    def test_returns_a_path(self) -> None:
        root = get_temp_root()
        assert isinstance(root, Path)

    def test_directory_exists(self) -> None:
        root = get_temp_root()
        assert root.is_dir()

    def test_same_root_returned_on_repeated_calls(self) -> None:
        first = get_temp_root()
        second = get_temp_root()
        assert first == second

    def test_root_created_lazily_via_mkdtemp(self) -> None:
        """Reset the module-level cache and verify mkdtemp is invoked once."""
        import m4bmaker.utils as utils_module

        original = utils_module._temp_root
        utils_module._temp_root = None
        try:
            with (
                patch(
                    "m4bmaker.utils.tempfile.mkdtemp",
                    return_value="/tmp/fake_m4bmaker_root",
                ) as mock_mkdtemp,
                patch("m4bmaker.utils.atexit.register") as mock_register,
                # Isolates this test from real filesystem state -- the
                # sweep itself has its own dedicated tests below.
                patch("m4bmaker.utils._sweep_stale_temp_roots") as mock_sweep,
            ):
                root1 = get_temp_root()
                root2 = get_temp_root()
            mock_sweep.assert_called_once()
            mock_mkdtemp.assert_called_once()
            mock_register.assert_called_once()
            assert root1 == root2 == Path("/tmp/fake_m4bmaker_root")
        finally:
            utils_module._temp_root = original

    def test_new_directory_name_embeds_this_process_pid(self) -> None:
        """The mkdtemp prefix now carries os.getpid() so a later launch's
        sweep can tell a dead process's leftover directory from a live
        one's -- a bare "m4bmaker_" prefix alone can't distinguish them."""
        import m4bmaker.utils as utils_module

        original = utils_module._temp_root
        utils_module._temp_root = None
        try:
            with (
                patch(
                    "m4bmaker.utils.tempfile.mkdtemp",
                    return_value="/tmp/fake_m4bmaker_root",
                ) as mock_mkdtemp,
                patch("m4bmaker.utils.atexit.register"),
                patch("m4bmaker.utils._sweep_stale_temp_roots"),
            ):
                get_temp_root()
            _, kwargs = mock_mkdtemp.call_args
            assert kwargs["prefix"] == f"m4bmaker_{os.getpid()}_"
        finally:
            utils_module._temp_root = original


class TestParsePidFromTempDirName:
    def test_extracts_embedded_pid(self) -> None:
        assert _parse_pid_from_temp_dir_name("m4bmaker_12345_ab1cd2") == 12345

    def test_non_numeric_pid_segment_returns_none(self) -> None:
        # The pre-fix naming scheme (bare "m4bmaker_<random>", no pid) --
        # left alone rather than guessed at.
        assert _parse_pid_from_temp_dir_name("m4bmaker_ab1cd2ef") is None

    def test_unrelated_prefix_returns_none(self) -> None:
        assert _parse_pid_from_temp_dir_name("something_else_12345") is None


class TestProcessIsAlive:
    def test_returns_true_for_this_process(self) -> None:
        assert _process_is_alive(os.getpid()) is True

    def test_returns_false_when_process_lookup_error(self) -> None:
        with patch("m4bmaker.utils.os.kill", side_effect=ProcessLookupError):
            assert _process_is_alive(999999) is False

    def test_returns_true_when_permission_denied(self) -> None:
        # Exists, just not signalable by us -- treated as alive so its
        # directory is never touched.
        with patch("m4bmaker.utils.os.kill", side_effect=PermissionError):
            assert _process_is_alive(1) is True

    def test_always_true_on_windows_regardless_of_pid(self) -> None:
        # os.kill(pid, 0) on Windows calls TerminateProcess() under the
        # hood -- never actually probed there; see the function's own
        # docstring.
        with (
            patch("m4bmaker.utils.sys.platform", "win32"),
            patch("m4bmaker.utils.os.kill", side_effect=ProcessLookupError),
        ):
            assert _process_is_alive(999999) is True


class TestSweepStaleTempRoots:
    def test_removes_directory_of_a_dead_process(self, tmp_path: Path) -> None:
        stale = tmp_path / "m4bmaker_424242_ab1cd2"
        stale.mkdir()
        with (
            patch("m4bmaker.utils.tempfile.gettempdir", return_value=str(tmp_path)),
            patch("m4bmaker.utils._process_is_alive", return_value=False),
        ):
            _sweep_stale_temp_roots()
        assert not stale.exists()

    def test_leaves_directory_of_a_live_process(self, tmp_path: Path) -> None:
        live = tmp_path / "m4bmaker_424242_ab1cd2"
        live.mkdir()
        with (
            patch("m4bmaker.utils.tempfile.gettempdir", return_value=str(tmp_path)),
            patch("m4bmaker.utils._process_is_alive", return_value=True),
        ):
            _sweep_stale_temp_roots()
        assert live.exists()

    def test_leaves_unrecognized_directory_names_alone(self, tmp_path: Path) -> None:
        # Old-format ("m4bmaker_<random>", no pid) or unrelated -- no pid
        # to check liveness against, so never touched by this sweep.
        old_format = tmp_path / "m4bmaker_ab1cd2ef"
        old_format.mkdir()
        with patch("m4bmaker.utils.tempfile.gettempdir", return_value=str(tmp_path)):
            _sweep_stale_temp_roots()
        assert old_format.exists()

    def test_ignores_unreadable_temp_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with patch("m4bmaker.utils.tempfile.gettempdir", return_value=str(missing)):
            _sweep_stale_temp_roots()  # must not raise


# ---------------------------------------------------------------------------
# safe_input
# ---------------------------------------------------------------------------


class TestSafeInput:
    def test_returns_input_value(self) -> None:
        with patch("builtins.input", return_value="hello"):
            assert safe_input("prompt: ") == "hello"

    def test_eof_error_raises_m4berror_with_hint(self) -> None:
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(M4BError, match="--no-prompt"):
                safe_input("prompt: ")

    def test_eof_error_uses_custom_hint(self) -> None:
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(M4BError, match="--custom-flag"):
                safe_input("prompt: ", no_prompt_hint="--custom-flag")

    def test_keyboard_interrupt_raises_m4berror(self) -> None:
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with pytest.raises(M4BError, match="Cancelled"):
                safe_input("prompt: ")
