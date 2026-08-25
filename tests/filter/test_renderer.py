"""Tests for m4bmaker.filter.renderer (PRD §8; ADR-0002, ADR-0006, ADR-0007).

Subprocess calls (ffmpeg) are mocked throughout, matching the project's
existing convention — the real end-to-end proof against a real ~13.5-hour
production AAC M4B is documented in docs/adr/0007-renderer-and-validator.md
rather than re-run in the default test suite (no such fixture is
committed to the repo, and re-running a ~10-minute real render on every
test invocation would defeat the point of a fast test suite).

generate_envelope_pcm itself is exercised for real (no mocking) — it's
pure Python file I/O with no subprocess involved, and its correctness is
best proven by reading the actual bytes back, the same way the real ADR-
0007 validation did.
"""

from __future__ import annotations

import struct
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m4bmaker.filter.models import (
    AttenuationSettings,
    AudioTrack,
    MediaManifest,
    RenderInterval,
    RenderPlan,
)
from m4bmaker.filter.renderer import (
    RenderError,
    RenderResult,
    _db_to_linear,
    _gain_at,
    apply_gain_envelope,
    encode_and_mux,
    estimate_storage_bytes,
    extract_primary_audio_pcm,
    generate_envelope_pcm,
    render,
)

_RENDERER = "m4bmaker.filter.renderer"


def _run_result(returncode: int = 0, stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stderr = stderr
    return r


class TestDbToLinear:
    def test_zero_db_is_unity(self) -> None:
        assert _db_to_linear(0.0) == pytest.approx(1.0)

    def test_minus_80_db(self) -> None:
        assert _db_to_linear(-80.0) == pytest.approx(0.0001, rel=1e-3)

    def test_minus_60_db(self) -> None:
        assert _db_to_linear(-60.0) == pytest.approx(0.001, rel=1e-3)


class TestGainAt:
    def test_start_of_fade_in_is_full_gain(self) -> None:
        assert _gain_at(
            0, span_samples=1000, fade_in_n=100, fade_out_n=100, floor=0.1
        ) == pytest.approx(1.0)

    def test_end_of_fade_in_approaches_floor(self) -> None:
        g = _gain_at(99, span_samples=1000, fade_in_n=100, fade_out_n=100, floor=0.1)
        assert g < 0.2  # nearly at floor by the last fade-in sample

    def test_sustain_region_is_floor(self) -> None:
        g = _gain_at(500, span_samples=1000, fade_in_n=100, fade_out_n=100, floor=0.1)
        assert g == pytest.approx(0.1)

    def test_fade_out_ramps_back_up(self) -> None:
        g_start = _gain_at(
            901, span_samples=1000, fade_in_n=100, fade_out_n=100, floor=0.1
        )
        g_end = _gain_at(
            999, span_samples=1000, fade_in_n=100, fade_out_n=100, floor=0.1
        )
        assert g_start < g_end  # gain increases moving toward the very end


class TestExtractPrimaryAudioPcm:
    def test_command_uses_input_side_no_seek_and_correct_format(
        self, tmp_path: Path
    ) -> None:
        dest = tmp_path / "out.pcm"
        with patch("subprocess.run", return_value=_run_result()) as m:
            extract_primary_audio_pcm(tmp_path / "src.m4b", 0, 44100, 2, "ffmpeg", dest)
        cmd = m.call_args[0][0]
        assert "-map" in cmd and "0:0" in cmd
        assert "-ar" in cmd and "44100" in cmd
        assert "-ac" in cmd and "2" in cmd
        assert "-f" in cmd and "s16le" in cmd

    def test_raises_render_error_on_failure(self, tmp_path: Path) -> None:
        with patch("subprocess.run", return_value=_run_result(1, "boom")):
            with pytest.raises(RenderError, match="boom"):
                extract_primary_audio_pcm(
                    tmp_path / "src.m4b", 0, 44100, 2, "ffmpeg", tmp_path / "out.pcm"
                )

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        dest = tmp_path / "nested" / "out.pcm"
        with patch("subprocess.run", return_value=_run_result()):
            extract_primary_audio_pcm(tmp_path / "src.m4b", 0, 44100, 2, "ffmpeg", dest)
        assert dest.parent.is_dir()


class TestGenerateEnvelopePcm:
    def _read_samples(
        self, path: Path, count: int, channels: int
    ) -> list[tuple[int, ...]]:
        data = path.read_bytes()
        n = count * channels
        values = struct.unpack(f"<{n}h", data[: n * 2])
        return [values[i * channels : (i + 1) * channels] for i in range(count)]

    def test_baseline_is_full_scale(self, tmp_path: Path) -> None:
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=100
        )
        dest = tmp_path / "env.pcm"
        generate_envelope_pcm(plan, sample_rate=1000, channels=1, dest_pcm_path=dest)
        samples = self._read_samples(dest, 100, 1)
        assert all(s == (32767,) for s in samples)

    def test_exact_sample_count_when_total_samples_given(self, tmp_path: Path) -> None:
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=100
        )
        dest = tmp_path / "env.pcm"
        generate_envelope_pcm(
            plan, sample_rate=1000, channels=2, dest_pcm_path=dest, total_samples=50
        )
        assert dest.stat().st_size == 50 * 2 * 2  # samples * channels * 2 bytes

    def test_interval_reaches_floor_in_sustain_region(self, tmp_path: Path) -> None:
        atten = AttenuationSettings(
            lead_padding_ms=0,
            tail_padding_ms=0,
            fade_in_ms=5,
            fade_out_ms=5,
            gain_floor_db=-80.0,
        )
        interval = RenderInterval(
            start_ms=100, end_ms=200, fade_in_ms=5, fade_out_ms=5, hit_ids=("h-1",)
        )
        plan = RenderPlan(
            intervals=(interval,), attenuation=atten, source_duration_ms=1000
        )
        dest = tmp_path / "env.pcm"
        # sample_rate=1000 -> 1 sample per ms, easy to reason about indices
        generate_envelope_pcm(plan, sample_rate=1000, channels=1, dest_pcm_path=dest)
        samples = self._read_samples(dest, 1000, 1)
        # Sustain region: [105, 195) -- well past the 5ms fade edges.
        sustain_val = samples[150][0]
        assert sustain_val < 10  # near floor (0.0001 * 32767 ~ 3.3, rounds to ~3)
        # Outside the interval entirely.
        assert samples[50][0] == 32767
        assert samples[500][0] == 32767

    def test_channels_carry_identical_gain(self, tmp_path: Path) -> None:
        interval = RenderInterval(
            start_ms=10, end_ms=20, fade_in_ms=5, fade_out_ms=5, hit_ids=("h-1",)
        )
        plan = RenderPlan(
            intervals=(interval,),
            attenuation=AttenuationSettings(),
            source_duration_ms=100,
        )
        dest = tmp_path / "env.pcm"
        generate_envelope_pcm(plan, sample_rate=1000, channels=2, dest_pcm_path=dest)
        samples = self._read_samples(dest, 100, 2)
        for left, right in samples:
            assert left == right

    def test_multiple_chunks_produce_correct_total_length(self, tmp_path: Path) -> None:
        # Force multiple chunk iterations with a tiny chunk size.
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=1000
        )
        dest = tmp_path / "env.pcm"
        with patch(f"{_RENDERER}._ENVELOPE_CHUNK_FRAMES", 10):
            generate_envelope_pcm(
                plan, sample_rate=1000, channels=1, dest_pcm_path=dest
            )
        assert dest.stat().st_size == 1000 * 2

    def test_interval_spanning_a_chunk_boundary_is_still_correct(
        self, tmp_path: Path
    ) -> None:
        interval = RenderInterval(
            start_ms=8, end_ms=18, fade_in_ms=5, fade_out_ms=5, hit_ids=("h-1",)
        )
        plan = RenderPlan(
            intervals=(interval,),
            attenuation=AttenuationSettings(),
            source_duration_ms=100,
        )
        dest = tmp_path / "env.pcm"
        with patch(
            f"{_RENDERER}._ENVELOPE_CHUNK_FRAMES", 10
        ):  # boundary at sample 10, inside [8,18)
            generate_envelope_pcm(
                plan, sample_rate=1000, channels=1, dest_pcm_path=dest
            )
        samples = self._read_samples(dest, 100, 1)
        assert samples[13][0] < 10  # sustain region, spans the chunk seam
        assert samples[50][0] == 32767


class TestApplyGainEnvelope:
    def test_command_specifies_raw_pcm_format_for_both_inputs(
        self, tmp_path: Path
    ) -> None:
        with patch("subprocess.run", return_value=_run_result()) as m:
            apply_gain_envelope(
                tmp_path / "src.pcm",
                tmp_path / "env.pcm",
                tmp_path / "out.pcm",
                44100,
                2,
                "ffmpeg",
            )
        cmd = m.call_args[0][0]
        assert cmd.count("s16le") == 3  # two inputs + output
        assert "amultiply" in " ".join(cmd)

    def test_raises_on_failure(self, tmp_path: Path) -> None:
        with patch("subprocess.run", return_value=_run_result(1, "amultiply boom")):
            with pytest.raises(RenderError, match="amultiply boom"):
                apply_gain_envelope(
                    tmp_path / "src.pcm",
                    tmp_path / "env.pcm",
                    tmp_path / "out.pcm",
                    44100,
                    2,
                    "ffmpeg",
                )


class TestEncodeAndMux:
    def test_without_cover_does_not_map_video(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.m4b"

        def _side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"fake m4b")
            return _run_result()

        with patch("subprocess.run", side_effect=_side_effect) as m:
            encode_and_mux(
                tmp_path / "filtered.pcm",
                tmp_path / "source.m4b",
                dest,
                "128k",
                2,
                44100,
                "ffmpeg",
            )
        cmd = m.call_args[0][0]
        assert "2:v" not in cmd
        assert dest.exists()

    def test_with_cover_maps_third_input_as_video(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.m4b"
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"\xff\xd8fake-jpeg")

        def _side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"fake m4b")
            return _run_result()

        with patch("subprocess.run", side_effect=_side_effect) as m:
            encode_and_mux(
                tmp_path / "filtered.pcm",
                tmp_path / "source.m4b",
                dest,
                "128k",
                2,
                44100,
                "ffmpeg",
                cover_path=cover,
            )
        cmd = m.call_args[0][0]
        assert "2:v" in cmd
        assert "copy" in cmd  # jpeg cover -> stream-copied, not re-encoded
        assert "attached_pic" in cmd

    def test_non_jpeg_png_cover_is_transcoded_to_mjpeg(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.m4b"
        cover = tmp_path / "cover.webp"
        cover.write_bytes(b"fake-webp")

        def _side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"fake m4b")
            return _run_result()

        with patch("subprocess.run", side_effect=_side_effect) as m:
            encode_and_mux(
                tmp_path / "filtered.pcm",
                tmp_path / "source.m4b",
                dest,
                "128k",
                2,
                44100,
                "ffmpeg",
                cover_path=cover,
            )
        cmd = m.call_args[0][0]
        assert "mjpeg" in cmd

    def test_atomic_no_partial_left_after_success(self, tmp_path: Path) -> None:
        dest = tmp_path / "out.m4b"

        def _side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"fake m4b")
            return _run_result()

        with patch("subprocess.run", side_effect=_side_effect):
            encode_and_mux(
                tmp_path / "filtered.pcm",
                tmp_path / "source.m4b",
                dest,
                "128k",
                2,
                44100,
                "ffmpeg",
            )
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".partial"]
        assert leftovers == []
        assert dest.read_bytes() == b"fake m4b"

    def test_partial_removed_on_failure_and_dest_untouched(
        self, tmp_path: Path
    ) -> None:
        dest = tmp_path / "out.m4b"
        dest.write_bytes(b"pre-existing good output")
        with patch("subprocess.run", return_value=_run_result(1, "encode boom")):
            with pytest.raises(RenderError):
                encode_and_mux(
                    tmp_path / "filtered.pcm",
                    tmp_path / "source.m4b",
                    dest,
                    "128k",
                    2,
                    44100,
                    "ffmpeg",
                )
        assert dest.read_bytes() == b"pre-existing good output"
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".partial"]
        assert leftovers == []


class TestRender:
    def _manifest(self) -> MediaManifest:
        return MediaManifest(
            schema_version=1,
            source_path="/books/a.m4b",
            fingerprint="sha256:x",
            duration_ms=10_000,
            tracks=(
                AudioTrack(
                    index=0,
                    codec_name="aac",
                    is_default=True,
                    channels=2,
                    sample_rate=44100,
                    bit_rate=128000,
                ),
            ),
            selected_track_index=0,
            selected_track_is_fallback=False,
            chapters=(),
            required_metadata={},
            cover_present=False,
            eligible=True,
        )

    def test_raises_when_track_missing_sample_rate(self, tmp_path: Path) -> None:
        manifest = MediaManifest(
            schema_version=1,
            source_path="/books/a.m4b",
            fingerprint="sha256:x",
            duration_ms=10_000,
            tracks=(
                AudioTrack(
                    index=0,
                    codec_name="aac",
                    is_default=True,
                    channels=2,
                    sample_rate=None,
                    bit_rate=128000,
                ),
            ),
            selected_track_index=0,
            selected_track_is_fallback=False,
            chapters=(),
            required_metadata={},
            cover_present=False,
            eligible=True,
        )
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=10_000
        )
        with pytest.raises(RenderError, match="sample rate"):
            render(
                tmp_path / "src.m4b",
                manifest,
                plan,
                tmp_path / "out.m4b",
                "ffmpeg",
                "ffprobe",
                "128k",
            )

    def test_happy_path_calls_stages_in_order_and_skips_cover_when_absent(
        self, tmp_path: Path
    ) -> None:
        manifest = self._manifest()
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=10_000
        )
        calls: list[str] = []

        def _extract(*a, **k):
            calls.append("extract")
            a[-1].write_bytes(b"\x00" * 100)  # source_pcm

        def _envelope(*a, **k):
            calls.append("envelope")

        def _apply(*a, **k):
            calls.append("apply")

        def _mux(*a, **k):
            calls.append("mux")

        with (
            patch(f"{_RENDERER}.extract_primary_audio_pcm", side_effect=_extract),
            patch(f"{_RENDERER}.generate_envelope_pcm", side_effect=_envelope),
            patch(f"{_RENDERER}.apply_gain_envelope", side_effect=_apply),
            patch(f"{_RENDERER}.encode_and_mux", side_effect=_mux) as mux_mock,
        ):
            result = render(
                tmp_path / "src.m4b",
                manifest,
                plan,
                tmp_path / "out.m4b",
                "ffmpeg",
                "ffprobe",
                "128k",
            )
        assert calls == ["extract", "envelope", "apply", "mux"]
        assert mux_mock.call_args.kwargs["cover_path"] is None
        assert isinstance(result, RenderResult)
        assert result.duration_ms == 10_000

    def test_extracts_cover_when_present(self, tmp_path: Path) -> None:
        manifest = replace(self._manifest(), cover_present=True)
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=10_000
        )

        def _extract(*a, **k):
            a[-1].write_bytes(b"\x00" * 100)

        with (
            patch(f"{_RENDERER}.extract_primary_audio_pcm", side_effect=_extract),
            patch(f"{_RENDERER}.generate_envelope_pcm"),
            patch(f"{_RENDERER}.apply_gain_envelope"),
            patch(f"{_RENDERER}.encode_and_mux") as mux_mock,
            patch(
                "m4bmaker.cover.extract_cover_from_audio",
                return_value=Path("/tmp/cover.jpg"),
            ) as cover_mock,
        ):
            render(
                tmp_path / "src.m4b",
                manifest,
                plan,
                tmp_path / "out.m4b",
                "ffmpeg",
                "ffprobe",
                "128k",
            )
        cover_mock.assert_called_once()
        assert mux_mock.call_args.kwargs["cover_path"] == Path("/tmp/cover.jpg")

    def test_progress_callback_reaches_100_percent(self, tmp_path: Path) -> None:
        manifest = self._manifest()
        plan = RenderPlan(
            intervals=(), attenuation=AttenuationSettings(), source_duration_ms=10_000
        )
        fractions: list[float] = []

        def _extract(*a, **k):
            a[-1].write_bytes(b"\x00" * 100)

        with (
            patch(f"{_RENDERER}.extract_primary_audio_pcm", side_effect=_extract),
            patch(f"{_RENDERER}.generate_envelope_pcm"),
            patch(f"{_RENDERER}.apply_gain_envelope"),
            patch(f"{_RENDERER}.encode_and_mux"),
        ):
            render(
                tmp_path / "src.m4b",
                manifest,
                plan,
                tmp_path / "out.m4b",
                "ffmpeg",
                "ffprobe",
                "128k",
                progress_callback=lambda msg, frac: fractions.append(frac),
            )
        assert fractions[-1] == 1.0
        assert fractions == sorted(fractions)


class TestEstimateStorageBytes:
    def _manifest(
        self,
        duration_ms: int = 10_000,
        sample_rate: int | None = 44_100,
        channels: int | None = 2,
        bit_rate: int | None = 128_000,
    ) -> MediaManifest:
        return MediaManifest(
            schema_version=1,
            source_path="/books/a.m4b",
            fingerprint="sha256:x",
            duration_ms=duration_ms,
            tracks=(
                AudioTrack(
                    index=0,
                    codec_name="aac",
                    is_default=True,
                    channels=channels,
                    sample_rate=sample_rate,
                    bit_rate=bit_rate,
                ),
            ),
            selected_track_index=0,
            selected_track_is_fallback=False,
            chapters=(),
            required_metadata={},
            cover_present=False,
            eligible=True,
        )

    def test_matches_the_real_13_5_hour_fixture_measurement(self) -> None:
        # ADR-0007/this module's own docstring: "this book's PCM is ~8.6GB
        # per intermediate stage" — the real Dungeon Crawler Carl fixture,
        # 48693.108345s at stereo 44.1kHz. Cross-checking the formula
        # against that real, previously-measured number rather than only
        # a synthetic one.
        manifest = self._manifest(
            duration_ms=48_693_108, sample_rate=44_100, channels=2, bit_rate=126_000
        )
        estimate = estimate_storage_bytes(manifest)

        pcm_per_stage = 48_693.108 * 44_100 * 2 * 2
        assert pcm_per_stage == pytest.approx(8.59e9, rel=0.01)

        expected = round(pcm_per_stage * 3 + 48_693.108 * (126_000 / 8))
        assert estimate == pytest.approx(expected, rel=1e-6)

    def test_three_pcm_stages_plus_aac_output(self) -> None:
        # 10s @ 44.1kHz stereo 16-bit = 1,764,000 bytes/stage * 3 stages
        # + 10s @ 128kbps AAC = 160,000 bytes.
        manifest = self._manifest(
            duration_ms=10_000, sample_rate=44_100, channels=2, bit_rate=128_000
        )
        assert estimate_storage_bytes(manifest) == 1_764_000 * 3 + 160_000

    def test_missing_sample_rate_returns_zero(self) -> None:
        manifest = self._manifest(sample_rate=None)
        assert estimate_storage_bytes(manifest) == 0

    def test_missing_channels_returns_zero(self) -> None:
        manifest = self._manifest(channels=None)
        assert estimate_storage_bytes(manifest) == 0

    def test_missing_selected_track_returns_zero(self) -> None:
        manifest = replace(self._manifest(), selected_track_index=99)
        assert estimate_storage_bytes(manifest) == 0

    def test_missing_bit_rate_omits_aac_term_but_still_counts_pcm(self) -> None:
        manifest = self._manifest(
            duration_ms=10_000, sample_rate=44_100, channels=2, bit_rate=None
        )
        assert estimate_storage_bytes(manifest) == 1_764_000 * 3

    def test_mono_track_estimate(self) -> None:
        manifest = self._manifest(
            duration_ms=10_000, sample_rate=44_100, channels=1, bit_rate=64_000
        )
        assert estimate_storage_bytes(manifest) == 882_000 * 3 + 80_000
