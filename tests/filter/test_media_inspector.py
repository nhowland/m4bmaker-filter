"""Tests for m4bmaker.filter.media_inspector — eligibility + primary-track
selection (PRD §6.1, §6.4, D-09)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from m4bmaker.filter.media_inspector import compute_fingerprint, inspect


def _run_result(stdout: str, returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    return r


def _aac_track(
    index: int, *, default: bool, sample_rate=44100, channels=1, bit_rate=64000
):
    return {
        "index": index,
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": str(sample_rate),
        "channels": channels,
        "bit_rate": str(bit_rate),
        "disposition": {"default": 1 if default else 0},
    }


def _mp3_track(index: int, *, default: bool):
    return {
        "index": index,
        "codec_type": "audio",
        "codec_name": "mp3",
        "sample_rate": "44100",
        "channels": 2,
        "bit_rate": "128000",
        "disposition": {"default": 1 if default else 0},
    }


def _cover_stream(index: int):
    return {
        "index": index,
        "codec_type": "video",
        "codec_name": "mjpeg",
        "disposition": {"attached_pic": 1},
    }


def _probe_json(
    *,
    streams,
    duration_s=3723.456,
    format_name="mov,mp4,m4a,3gp,3g2,mj2",
    tags=None,
    chapters=None,
) -> str:
    return json.dumps(
        {
            "format": {
                "format_name": format_name,
                "duration": str(duration_s),
                "tags": tags or {},
            },
            "streams": streams,
            "chapters": chapters or [],
        }
    )


def _make_file(tmp_path: Path, name: str = "book.m4b") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00" * 128)
    return p


class TestEligibleSource:
    def test_default_track_selected_and_eligible(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        out = _probe_json(
            streams=[_aac_track(1, default=True), _cover_stream(2)],
            tags={"title": "Dune", "artist": "Frank Herbert", "genre": "Sci-Fi"},
            chapters=[
                {"start_time": "0.0", "tags": {"title": "Chapter 1"}},
                {"start_time": "1800.0", "tags": {"title": "Chapter 2"}},
            ],
        )
        with patch("subprocess.run", return_value=_run_result(out)):
            manifest = inspect(p, "ffprobe")

        assert manifest.eligible
        assert manifest.ineligibility_reasons == ()
        assert manifest.selected_track_index == 1
        assert manifest.selected_track_is_fallback is False
        assert manifest.duration_ms == 3723456
        assert manifest.required_metadata["title"] == "Dune"
        assert manifest.required_metadata["author"] == "Frank Herbert"
        assert manifest.cover_present is True
        assert [c.title for c in manifest.chapters] == ["Chapter 1", "Chapter 2"]
        assert manifest.chapters[1].start_ms == 1800000
        assert manifest.fingerprint.startswith("sha256:")

    def test_narrator_read_from_nrt_atom(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        out = _probe_json(
            streams=[_aac_track(0, default=True)],
            tags={"title": "Dune", "\xa9nrt": "Simon Vance"},
        )
        with patch("subprocess.run", return_value=_run_result(out)):
            manifest = inspect(p, "ffprobe")
        assert manifest.required_metadata["narrator"] == "Simon Vance"


class TestNoDefaultTrackFallback:
    def test_falls_back_to_first_audio_track(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        out = _probe_json(
            streams=[_aac_track(0, default=False), _aac_track(1, default=False)]
        )
        with patch("subprocess.run", return_value=_run_result(out)):
            manifest = inspect(p, "ffprobe")
        assert manifest.eligible
        assert manifest.selected_track_index == 0
        assert manifest.selected_track_is_fallback is True


class TestUnsupportedConditions:
    def test_no_audio_track_is_ineligible(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        out = _probe_json(streams=[_cover_stream(0)])
        with patch("subprocess.run", return_value=_run_result(out)):
            manifest = inspect(p, "ffprobe")
        assert not manifest.eligible
        assert any("No audio track" in r for r in manifest.ineligibility_reasons)
        assert manifest.selected_track_index is None

    def test_non_aac_primary_track_is_ineligible(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        out = _probe_json(streams=[_mp3_track(0, default=True)])
        with patch("subprocess.run", return_value=_run_result(out)):
            manifest = inspect(p, "ffprobe")
        assert not manifest.eligible
        assert any("not AAC" in r for r in manifest.ineligibility_reasons)
        # The track is still reported (for diagnostics), just not eligible.
        assert manifest.selected_track_index == 0

    def test_non_mp4_container_is_ineligible(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path, name="book.ogg")
        out = _probe_json(streams=[_aac_track(0, default=True)], format_name="ogg")
        with patch("subprocess.run", return_value=_run_result(out)):
            manifest = inspect(p, "ffprobe")
        assert not manifest.eligible
        assert any("MP4/M4B" in r for r in manifest.ineligibility_reasons)

    def test_ffprobe_nonzero_returncode_is_ineligible(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        with patch("subprocess.run", return_value=_run_result("", returncode=1)):
            manifest = inspect(p, "ffprobe")
        assert not manifest.eligible
        assert manifest.duration_ms == 0

    def test_malformed_json_is_ineligible(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        with patch("subprocess.run", return_value=_run_result("{not json")):
            manifest = inspect(p, "ffprobe")
        assert not manifest.eligible

    def test_missing_file_is_ineligible_without_calling_ffprobe(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "does-not-exist.m4b"
        with patch("subprocess.run") as m:
            manifest = inspect(missing, "ffprobe")
        m.assert_not_called()
        assert not manifest.eligible
        assert "does not exist" in manifest.ineligibility_reasons[0]


class TestComputeFingerprint:
    def test_deterministic_for_same_inputs(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        fp1 = compute_fingerprint(p, 3723456, "aac")
        fp2 = compute_fingerprint(p, 3723456, "aac")
        assert fp1 == fp2

    def test_differs_for_different_duration(self, tmp_path: Path) -> None:
        p = _make_file(tmp_path)
        fp1 = compute_fingerprint(p, 3723456, "aac")
        fp2 = compute_fingerprint(p, 1000, "aac")
        assert fp1 != fp2

    def test_missing_file_does_not_raise(self, tmp_path: Path) -> None:
        missing = tmp_path / "gone.m4b"
        # Must not raise even though the file doesn't exist — inspect()
        # calls this after already reporting ineligibility for the same
        # condition, so this helper must be defensive on its own.
        fp = compute_fingerprint(missing, 0, None)
        assert fp.startswith("sha256:")
