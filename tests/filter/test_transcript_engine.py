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
    WhisperNotFoundError,
    WhisperTranscriptionError,
    find_whisper_cli,
    get_whisper_version,
    run_whisper,
    transcribe_short_audio,
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

    def test_command_includes_no_gpu_flag(self, tmp_path: Path) -> None:
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
        assert "--no-gpu" in captured_cmd["cmd"]

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
