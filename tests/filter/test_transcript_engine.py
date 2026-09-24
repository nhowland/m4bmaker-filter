"""Tests for m4bmaker.filter.transcript_engine — whisper.cpp adapter (PRD §10.1).

Unit tests here mock ``subprocess.run``, matching the project's existing
convention (see ``tests/test_preflight.py``) — no real whisper.cpp binary
or model is required to run the suite. The JSON fixtures below are trimmed
excerpts of *real* whisper.cpp ``--output-json-full`` output, captured
during the G3 spike (see ``docs/adr/0001-stt-engine-integration.md``), not
invented data — this matters because the exact special-token/punctuation
shape (``[_BEG_]``, ``[_TT_225]``, standalone ``"."``/``","`` tokens) is
not documented anywhere and had to be observed directly.

A real-binary, real-model integration test is intentionally **not**
included in the default suite: it would require committing a ~74MB model
file to the repository, which contradicts D-11 (models are downloaded on
demand, never bundled/committed). It is gated behind an environment
variable instead, so it stays opt-in for a developer who has already
downloaded a model locally, and is skipped everywhere else including CI.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m4bmaker.filter.transcript import SegmentStatus, TranscriptSource
from m4bmaker.filter.transcript_engine import (
    WHISPER_RELEASES_URL,
    WhisperNotFoundError,
    WhisperTranscriptionError,
    dtw_model_name_for,
    find_whisper_cli,
    get_whisper_version,
    run_whisper,
    transcribe_short_audio,
    whisper_install_hint,
    whisper_missing_message,
    whisper_result_to_segment,
)

# Trimmed excerpt of real captured output from the G3 spike: a synthetic
# "This is a test of the darn filtering system. Go to hell and back, my
# friend." fixture generated via macOS `say`, transcribed with ggml-tiny.en.
REAL_SPIKE_JSON = {
    "transcription": [
        {
            "text": " this is a test of the darn filtering system.",
            "tokens": [
                {
                    "text": "[_BEG_]",
                    "offsets": {"from": 0, "to": 0},
                    "p": 0.993093,
                },
                {"text": " this", "offsets": {"from": 20, "to": 270}, "p": 0.14716},
                {"text": " is", "offsets": {"from": 270, "to": 400}, "p": 0.964873},
                {"text": " a", "offsets": {"from": 400, "to": 420}, "p": 0.991422},
                {"text": " test", "offsets": {"from": 520, "to": 680}, "p": 0.996755},
                {"text": " of", "offsets": {"from": 750, "to": 840}, "p": 0.994719},
                {"text": " the", "offsets": {"from": 900, "to": 1060}, "p": 0.994484},
                {"text": " darn", "offsets": {"from": 1060, "to": 1250}, "p": 0.75046},
                {
                    "text": " filtering",
                    "offsets": {"from": 1350, "to": 1850},
                    "p": 0.991144,
                },
                {
                    "text": " system",
                    "offsets": {"from": 1950, "to": 2350},
                    "p": 0.994031,
                },
                {"text": ".", "offsets": {"from": 2350, "to": 2370}, "p": 0.844642},
                {"text": " Go", "offsets": {"from": 2640, "to": 2750}, "p": 0.540901},
                {"text": " to", "offsets": {"from": 2750, "to": 2900}, "p": 0.988027},
                {"text": " hell", "offsets": {"from": 2900, "to": 3200}, "p": 0.466949},
                {
                    "text": "[_TT_225]",
                    "offsets": {"from": 4500, "to": 4500},
                    "p": 0.126074,
                },
            ],
        }
    ],
}


class TestFindWhisperCli:
    def test_returns_path_when_found(self) -> None:
        with patch(
            "m4bmaker.filter.transcript_engine.find_binary",
            return_value="/opt/homebrew/bin/whisper-cli",
        ):
            assert find_whisper_cli() == "/opt/homebrew/bin/whisper-cli"

    def test_returns_none_when_not_found(self) -> None:
        with patch("m4bmaker.filter.transcript_engine.find_binary", return_value=None):
            assert find_whisper_cli() is None


class TestWhisperInstallHint:
    """ADR-0056: one source of wording for the banner, the Model Manager
    label, and the worker's backstop error."""

    def test_macos_gets_the_verified_homebrew_command(self) -> None:
        hint = whisper_install_hint("darwin")
        assert hint.command == "brew install whisper-cpp"
        assert hint.url == WHISPER_RELEASES_URL

    @pytest.mark.parametrize("platform", ["win32", "linux", "freebsd14"])
    def test_other_platforms_get_no_guessed_command(self, platform: str) -> None:
        hint = whisper_install_hint(platform)
        assert hint.command is None
        assert hint.url == WHISPER_RELEASES_URL
        assert "not yet tested" in hint.note.lower()

    def test_defaults_to_the_running_platform(self) -> None:
        with patch("m4bmaker.filter.transcript_engine.sys.platform", "darwin"):
            assert whisper_install_hint().command == "brew install whisper-cpp"
        with patch("m4bmaker.filter.transcript_engine.sys.platform", "win32"):
            assert whisper_install_hint().command is None


class TestWhisperMissingMessage:
    def test_macos_message_names_the_command(self) -> None:
        with patch("m4bmaker.filter.transcript_engine.sys.platform", "darwin"):
            msg = whisper_missing_message()
        assert msg.startswith("whisper-cli not found")
        assert "brew install whisper-cpp" in msg

    def test_other_platform_message_points_at_releases(self) -> None:
        with patch("m4bmaker.filter.transcript_engine.sys.platform", "linux"):
            msg = whisper_missing_message()
        assert msg.startswith("whisper-cli not found")
        assert WHISPER_RELEASES_URL in msg
        assert "brew" not in msg


class TestGetWhisperVersion:
    def _run_result(self, stdout: str = "", stderr: str = "") -> MagicMock:
        r = MagicMock()
        r.stdout = stdout
        r.stderr = stderr
        return r

    def test_parses_version_from_stderr(self) -> None:
        # Real binary prints backend-init lines then the version line, all
        # observed on stderr in the G3 spike.
        stderr = "ggml_metal_device_init: ...\nwhisper.cpp version: 1.9.2\n"
        with (
            patch(
                "m4bmaker.filter.transcript_engine.find_whisper_cli",
                return_value="/bin/whisper-cli",
            ),
            patch("subprocess.run", return_value=self._run_result(stderr=stderr)),
        ):
            assert get_whisper_version() == "1.9.2"

    def test_parses_version_from_stdout(self) -> None:
        with (
            patch(
                "m4bmaker.filter.transcript_engine.find_whisper_cli",
                return_value="/bin/whisper-cli",
            ),
            patch(
                "subprocess.run",
                return_value=self._run_result(stdout="whisper.cpp version: 1.9.2\n"),
            ),
        ):
            assert get_whisper_version() == "1.9.2"

    def test_returns_none_when_binary_not_found(self) -> None:
        with patch(
            "m4bmaker.filter.transcript_engine.find_whisper_cli", return_value=None
        ):
            assert get_whisper_version() is None

    def test_returns_none_when_version_line_absent(self) -> None:
        with (
            patch(
                "m4bmaker.filter.transcript_engine.find_whisper_cli",
                return_value="/bin/whisper-cli",
            ),
            patch("subprocess.run", return_value=self._run_result(stdout="garbage\n")),
        ):
            assert get_whisper_version() is None


class TestRunWhisper:
    def _mock_subprocess_writing_json(self, json_payload: dict):
        def _side_effect(cmd, **kwargs):
            out_stem = cmd[cmd.index("-of") + 1]
            Path(out_stem + ".json").write_text(
                json.dumps(json_payload), encoding="utf-8"
            )
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        return _side_effect

    def test_raises_when_binary_not_found(self, tmp_path: Path) -> None:
        with patch(
            "m4bmaker.filter.transcript_engine.find_whisper_cli", return_value=None
        ):
            with pytest.raises(WhisperNotFoundError):
                run_whisper(tmp_path / "a.wav", tmp_path / "model.bin")

    def test_parses_json_output_on_success(self, tmp_path: Path) -> None:
        with patch(
            "subprocess.run",
            side_effect=self._mock_subprocess_writing_json({"ok": True}),
        ):
            result = run_whisper(
                tmp_path / "a.wav",
                tmp_path / "model.bin",
                whisper_cli="/bin/whisper-cli",
            )
        assert result == {"ok": True}

    def test_command_omits_no_gpu_flag(self, tmp_path: Path) -> None:
        """ADR-0001 (2026-08-25): GPU is used when ggml finds a compatible
        backend, CPU is the automatic fallback — reversed from the
        original CPU-only-for-v1 call, so --no-gpu is no longer passed."""
        captured_cmd = {}

        def _side_effect(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            out_stem = cmd[cmd.index("-of") + 1]
            Path(out_stem + ".json").write_text("{}", encoding="utf-8")
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=_side_effect):
            run_whisper(
                tmp_path / "a.wav",
                tmp_path / "model.bin",
                whisper_cli="/bin/whisper-cli",
            )
        assert "--no-gpu" not in captured_cmd["cmd"]

    def test_raises_transcription_error_on_nonzero_exit(self, tmp_path: Path) -> None:
        result = MagicMock()
        result.returncode = 1
        result.stderr = "some ffmpeg-style error"
        with patch("subprocess.run", return_value=result):
            with pytest.raises(WhisperTranscriptionError, match="exited with code 1"):
                run_whisper(
                    tmp_path / "a.wav",
                    tmp_path / "model.bin",
                    whisper_cli="/bin/whisper-cli",
                )

    def test_retries_and_recovers_from_a_transient_nonzero_exit(
        self, tmp_path: Path
    ) -> None:
        """A real whisper-cli invocation was observed crashing natively on
        one attempt and succeeding on 5/5 immediate retries of the exact
        same file/flags — non-deterministic, not content-dependent."""
        calls: list[list[str]] = []

        def _side_effect(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            if len(calls) < 3:
                result.returncode = -6
                result.stderr = "WHISPER_ASSERT: filter_width < a->ne[2]"
                return result
            out_stem = cmd[cmd.index("-of") + 1]
            Path(out_stem + ".json").write_text(
                json.dumps({"ok": True}), encoding="utf-8"
            )
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=_side_effect):
            result = run_whisper(
                tmp_path / "a.wav",
                tmp_path / "model.bin",
                whisper_cli="/bin/whisper-cli",
            )
        assert result == {"ok": True}
        assert len(calls) == 3

    def test_falls_back_to_cpu_after_gpu_attempts_exhausted(
        self, tmp_path: Path
    ) -> None:
        """Real testing: the exact input that crashed once on GPU
        transcribed cleanly every time on a CPU-only (--no-gpu) rerun."""
        calls: list[list[str]] = []

        def _side_effect(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            if "--no-gpu" not in cmd:
                result.returncode = -6
                result.stderr = "WHISPER_ASSERT: filter_width < a->ne[2]"
                return result
            out_stem = cmd[cmd.index("-of") + 1]
            Path(out_stem + ".json").write_text(
                json.dumps({"ok": True}), encoding="utf-8"
            )
            result.returncode = 0
            result.stderr = ""
            return result

        with patch("subprocess.run", side_effect=_side_effect):
            result = run_whisper(
                tmp_path / "a.wav",
                tmp_path / "model.bin",
                whisper_cli="/bin/whisper-cli",
                dtw_model_name="base.en",
            )
        assert result == {"ok": True}
        assert len(calls) == 4  # 3 GPU attempts + 1 CPU-only fallback
        assert calls[:3] == [c for c in calls[:3] if "--no-gpu" not in c]
        # DTW timing precision is preserved on the CPU fallback too.
        assert "-dtw" in calls[-1]
        assert "-nfa" in calls[-1]

    def test_raises_after_exhausting_retries_and_cpu_fallback(
        self, tmp_path: Path
    ) -> None:
        calls: list[list[str]] = []

        def _side_effect(cmd, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = -6
            result.stderr = "WHISPER_ASSERT: filter_width < a->ne[2]"
            return result

        with patch("subprocess.run", side_effect=_side_effect):
            with pytest.raises(
                WhisperTranscriptionError,
                match="3 GPU attempt.*CPU-only fallback",
            ):
                run_whisper(
                    tmp_path / "a.wav",
                    tmp_path / "model.bin",
                    whisper_cli="/bin/whisper-cli",
                )
        assert len(calls) == 4  # 3 GPU attempts + 1 CPU-only fallback, no more
        assert "--no-gpu" in calls[-1]

    def test_dtw_model_name_omitted_by_default(self, tmp_path: Path) -> None:
        captured_cmd = {}

        def _side_effect(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            out_stem = cmd[cmd.index("-of") + 1]
            Path(out_stem + ".json").write_text("{}", encoding="utf-8")
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=_side_effect):
            run_whisper(
                tmp_path / "a.wav",
                tmp_path / "model.bin",
                whisper_cli="/bin/whisper-cli",
            )
        assert "-dtw" not in captured_cmd["cmd"]
        assert "-nfa" not in captured_cmd["cmd"]

    def test_dtw_model_name_adds_dtw_and_disables_flash_attn(
        self, tmp_path: Path
    ) -> None:
        """ADR-0025: DTW timestamps come back all -1 under flash attention
        in this build (confirmed directly against real whisper-cli output,
        which logs "dtw_token_timestamps is not supported with flash_attn
        - disabling" rather than erroring) -- -nfa must always accompany
        -dtw, not be a second setting a caller could forget."""
        captured_cmd = {}

        def _side_effect(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            out_stem = cmd[cmd.index("-of") + 1]
            Path(out_stem + ".json").write_text("{}", encoding="utf-8")
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("subprocess.run", side_effect=_side_effect):
            run_whisper(
                tmp_path / "a.wav",
                tmp_path / "model.bin",
                whisper_cli="/bin/whisper-cli",
                dtw_model_name="base.en",
            )
        cmd = captured_cmd["cmd"]
        assert cmd[cmd.index("-dtw") + 1] == "base.en"
        assert "-nfa" in cmd


class TestDtwModelNameFor:
    def test_strips_ggml_prefix_matching_model_manager_convention(self) -> None:
        assert dtw_model_name_for(Path("/models/ggml-base.en.bin")) == "base.en"
        assert dtw_model_name_for(Path("/models/ggml-small.en.bin")) == "small.en"


class TestWhisperResultToSegment:
    def test_extracts_real_words_with_timestamps_and_confidence(self) -> None:
        segment = whisper_result_to_segment(
            REAL_SPIKE_JSON, "chunk-000000", segment_start_ms=0, segment_end_ms=4500
        )
        words = segment.words
        assert [w.text for w in words] == [
            "this",
            "is",
            "a",
            "test",
            "of",
            "the",
            "darn",
            "filtering",
            "system",
            "Go",
            "to",
            "hell",
        ]
        # Leading-space stripped and normalized (lowercased) independently.
        darn = words[6]
        assert darn.text == "darn"
        assert darn.normalized == "darn"
        assert darn.start_ms == 1060
        assert darn.end_ms == 1250
        assert darn.confidence == pytest.approx(0.75046)

    def test_drops_special_tokens(self) -> None:
        segment = whisper_result_to_segment(
            REAL_SPIKE_JSON, "chunk-000000", segment_start_ms=0, segment_end_ms=4500
        )
        texts = [w.text for w in segment.words]
        assert "[_BEG_]" not in texts
        assert "[_TT_225]" not in texts

    def test_drops_punctuation_only_tokens(self) -> None:
        segment = whisper_result_to_segment(
            REAL_SPIKE_JSON, "chunk-000000", segment_start_ms=0, segment_end_ms=4500
        )
        assert "." not in [w.text for w in segment.words]

    def test_segment_status_is_completed(self) -> None:
        segment = whisper_result_to_segment(
            REAL_SPIKE_JSON, "chunk-000000", segment_start_ms=0, segment_end_ms=4500
        )
        assert segment.status is SegmentStatus.COMPLETED

    def test_zero_width_token_is_skipped(self) -> None:
        raw = {
            "transcription": [
                {
                    "tokens": [
                        {
                            "text": " word",
                            "offsets": {"from": 100, "to": 100},
                            "p": 0.9,
                        },
                    ]
                }
            ]
        }
        segment = whisper_result_to_segment(raw, "chunk-0", 0, 1000)
        assert segment.words == ()

    def test_empty_transcription_yields_no_words(self) -> None:
        segment = whisper_result_to_segment({"transcription": []}, "chunk-0", 0, 1000)
        assert segment.words == ()


class TestWhisperResultToSegmentDtw:
    """ADR-0025: a token's DTW start (t_dtw, whisper.cpp's own 10ms units)
    takes priority over its heuristic offsets when present and valid; a
    token's *end* comes from the *next* token's DTW start, not its own
    heuristic 'to' -- tokens are contiguous in a decode sequence, so one
    token's alignment boundary is the next one's."""

    def test_uses_dtw_start_and_next_tokens_dtw_start_as_end(self) -> None:
        raw = {
            "transcription": [
                {
                    "tokens": [
                        {
                            "text": " one",
                            "offsets": {"from": 0, "to": 300},
                            "t_dtw": 5,  # -> 50ms
                            "p": 0.9,
                        },
                        {
                            "text": " two",
                            "offsets": {"from": 300, "to": 600},
                            "t_dtw": 40,  # -> 400ms
                            "p": 0.9,
                        },
                    ]
                }
            ]
        }
        segment = whisper_result_to_segment(raw, "chunk-0", 0, 1000)
        one, two = segment.words
        assert one.start_ms == 50
        assert one.end_ms == 400  # next token's DTW start, not one's own 'to' (300)
        # last token has no "next" DTW start to borrow -- falls back to its
        # own heuristic 'to'.
        assert two.start_ms == 400
        assert two.end_ms == 600

    def test_falls_back_to_heuristic_offsets_when_t_dtw_is_negative_one(self) -> None:
        """Real DTW output showed a handful of tokens (often segment
        boundaries) with t_dtw == -1 even when DTW mode was requested --
        must not be treated as a real 0.05ms timestamp."""
        raw = {
            "transcription": [
                {
                    "tokens": [
                        {
                            "text": " one",
                            "offsets": {"from": 10, "to": 200},
                            "t_dtw": -1,
                            "p": 0.9,
                        },
                    ]
                }
            ]
        }
        segment = whisper_result_to_segment(raw, "chunk-0", 0, 1000)
        assert segment.words[0].start_ms == 10
        assert segment.words[0].end_ms == 200

    def test_missing_t_dtw_key_behaves_exactly_like_no_dtw_requested(self) -> None:
        """A transcript produced without DTW mode simply has no t_dtw key
        at all -- this function doesn't need to know whether DTW was
        requested, only whether each token's own data has it."""
        segment_without_dtw = whisper_result_to_segment(
            REAL_SPIKE_JSON, "chunk-0", 0, 4500
        )
        darn = segment_without_dtw.words[6]
        assert darn.start_ms == 1060
        assert darn.end_ms == 1250


class TestTranscribeShortAudio:
    def test_builds_complete_transcript_from_real_captured_shape(
        self, tmp_path: Path
    ) -> None:
        def _side_effect(cmd, **kwargs):
            out_stem = cmd[cmd.index("-of") + 1]
            Path(out_stem + ".json").write_text(
                json.dumps(REAL_SPIKE_JSON), encoding="utf-8"
            )
            result = MagicMock()
            result.returncode = 0
            return result

        source = TranscriptSource(
            fingerprint="sha256:test", duration_ms=4500, selected_audio_stream=0
        )
        with patch("subprocess.run", side_effect=_side_effect):
            transcript = transcribe_short_audio(
                tmp_path / "speech.wav",
                tmp_path / "ggml-tiny.en.bin",
                model_checksum="sha256:model",
                source=source,
                whisper_cli="/bin/whisper-cli",
                engine_version="1.9.2",
            )

        assert transcript.status.value == "complete"
        assert transcript.engine.name == "whisper.cpp"
        assert transcript.engine.version == "1.9.2"
        assert transcript.engine.model == "ggml-tiny.en"
        assert transcript.engine.model_checksum == "sha256:model"
        assert len(transcript.segments) == 1
        assert transcript.segments[0].id == "chunk-000000"
        words = transcript.words()
        assert any(w.text == "darn" for w in words)
        assert any(w.text == "hell" for w in words)


@pytest.mark.skipif(
    not os.environ.get("M4BMAKER_WHISPER_MODEL"),
    reason=(
        "Opt-in real-binary integration test. Set M4BMAKER_WHISPER_MODEL to "
        "a local ggml model path (e.g. ggml-tiny.en.bin) and have whisper-cli "
        "on PATH to run this. Skipped by default and in CI — no model file "
        "is committed to the repository (PRD D-11: models are downloaded on "
        "demand, never bundled)."
    ),
)
class TestRealWhisperIntegration:
    def test_transcribes_synthetic_speech_fixture(self, tmp_path: Path) -> None:
        assert shutil.which("whisper-cli") is not None
        model_path = Path(os.environ["M4BMAKER_WHISPER_MODEL"])
        assert model_path.exists()

        # Requires macOS `say` + ffmpeg to build the fixture — matches how
        # the G3 spike itself produced a synthetic, legally-clean speech
        # sample rather than using any copyrighted audio.
        aiff_path = tmp_path / "speech.aiff"
        wav_path = tmp_path / "speech.wav"
        os.system(
            f'say -o "{aiff_path}" '
            '"This is a test of the darn filtering system. '
            'Go to hell and back, my friend."'
        )
        os.system(
            f'ffmpeg -y -i "{aiff_path}" -ar 16000 -ac 1 -c:a pcm_s16le "{wav_path}"'
        )

        source = TranscriptSource(
            fingerprint="sha256:integration-test",
            duration_ms=5000,
            selected_audio_stream=0,
        )
        transcript = transcribe_short_audio(
            wav_path,
            model_path,
            model_checksum="unverified-in-this-test",
            source=source,
        )
        words = [w.text.lower() for w in transcript.words()]
        assert "darn" in words
        assert "hell" in words
