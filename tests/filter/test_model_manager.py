"""Tests for m4bmaker.filter.model_manager (PRD §10.1, §12.4).

All network access is mocked via ``urllib.request.urlopen`` — no real
download happens in this suite. The real download/checksum mechanics were
already proven against the live Hugging Face URLs during the G3 spike
(see ``docs/adr/0001-stt-engine-integration.md``): both ``base.en`` and
``small.en`` were downloaded for real, hashed locally, and the resulting
checksums are what ``KNOWN_MODELS`` pins.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from m4bmaker.filter.model_manager import (
    KNOWN_MODELS,
    ModelChecksumMismatchError,
    ModelDownloadCancelled,
    ModelDownloadError,
    ModelSpec,
    download_model,
    get_model_spec,
    is_installed,
    list_installed,
    model_path,
    remove_model,
    verify_installed,
)


class _FakeResponse:
    """Minimal stand-in for the object urllib.request.urlopen() returns:
    supports the context-manager protocol and chunked .read(n)."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _spec_for(data: bytes, url: str = "https://example.com/model.bin") -> ModelSpec:
    return ModelSpec(
        name="test-model",
        label="Test",
        url=url,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


class TestKnownModelsCatalog:
    def test_exactly_two_models(self) -> None:
        assert len(KNOWN_MODELS) == 2

    def test_base_en_is_recommended(self) -> None:
        base = get_model_spec("base.en")
        assert base.label == "Recommended"

    def test_small_en_is_higher_effort(self) -> None:
        small = get_model_spec("small.en")
        assert small.label == "Higher recognition effort"

    def test_all_urls_are_https(self) -> None:
        assert all(spec.url.startswith("https://") for spec in KNOWN_MODELS)

    def test_all_checksums_are_64_hex_chars(self) -> None:
        for spec in KNOWN_MODELS:
            assert len(spec.sha256) == 64
            int(spec.sha256, 16)  # raises ValueError if not valid hex

    def test_unknown_model_name_raises(self) -> None:
        with pytest.raises(KeyError):
            get_model_spec("nonexistent")


class TestModelPath:
    def test_filename_convention(self, tmp_path: Path) -> None:
        base = get_model_spec("base.en")
        assert model_path(base, tmp_path) == tmp_path / "ggml-base.en.bin"


class TestIsInstalled:
    def test_false_when_absent(self, tmp_path: Path) -> None:
        spec = _spec_for(b"x" * 100)
        assert is_installed(spec, tmp_path) is False

    def test_true_when_present_with_matching_size(self, tmp_path: Path) -> None:
        data = b"x" * 100
        spec = _spec_for(data)
        model_path(spec, tmp_path).write_bytes(data)
        assert is_installed(spec, tmp_path) is True

    def test_false_when_size_mismatches(self, tmp_path: Path) -> None:
        spec = _spec_for(b"x" * 100)
        model_path(spec, tmp_path).write_bytes(b"x" * 50)  # wrong size
        assert is_installed(spec, tmp_path) is False


class TestVerifyInstalled:
    def test_false_when_absent(self, tmp_path: Path) -> None:
        spec = _spec_for(b"x" * 100)
        assert verify_installed(spec, tmp_path) is False

    def test_true_when_checksum_matches(self, tmp_path: Path) -> None:
        data = b"real model bytes"
        spec = _spec_for(data)
        model_path(spec, tmp_path).write_bytes(data)
        assert verify_installed(spec, tmp_path) is True

    def test_false_when_file_corrupted(self, tmp_path: Path) -> None:
        data = b"real model bytes"
        spec = _spec_for(data)
        model_path(spec, tmp_path).write_bytes(b"corrupted garbage")
        assert verify_installed(spec, tmp_path) is False


class TestListInstalled:
    def test_lists_only_installed_known_models(self, tmp_path: Path) -> None:
        base = get_model_spec("base.en")
        model_path(base, tmp_path).write_bytes(b"x" * base.size_bytes)
        installed = list_installed(tmp_path)
        assert installed == [base]


class TestRemoveModel:
    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        spec = _spec_for(b"x" * 10)
        path = model_path(spec, tmp_path)
        path.write_bytes(b"x" * 10)
        remove_model(spec, tmp_path)
        assert not path.exists()

    def test_idempotent_when_absent(self, tmp_path: Path) -> None:
        spec = _spec_for(b"x" * 10)
        remove_model(spec, tmp_path)  # must not raise


class TestDownloadModel:
    def test_rejects_non_https_url(self, tmp_path: Path) -> None:
        spec = _spec_for(b"data", url="http://example.com/model.bin")
        with pytest.raises(ValueError, match="HTTPS"):
            download_model(spec, tmp_path)

    def test_happy_path_installs_verified_file(self, tmp_path: Path) -> None:
        data = b"model bytes" * 1000
        spec = _spec_for(data)
        with patch("urllib.request.urlopen", return_value=_FakeResponse(data)):
            result_path = download_model(spec, tmp_path)
        assert result_path == model_path(spec, tmp_path)
        assert result_path.read_bytes() == data

    def test_no_partial_file_left_after_success(self, tmp_path: Path) -> None:
        data = b"model bytes" * 1000
        spec = _spec_for(data)
        with patch("urllib.request.urlopen", return_value=_FakeResponse(data)):
            download_model(spec, tmp_path)
        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".partial"]
        assert leftovers == []

    def test_progress_callback_receives_increasing_bytes_and_correct_total(
        self, tmp_path: Path
    ) -> None:
        data = b"x" * (3 * 1024 * 1024 + 500)  # spans multiple 1MiB chunks
        spec = _spec_for(data)
        calls: list[tuple[int, int]] = []
        with patch("urllib.request.urlopen", return_value=_FakeResponse(data)):
            download_model(
                spec, tmp_path, progress_callback=lambda d, t: calls.append((d, t))
            )
        assert len(calls) >= 3
        downloaded_values = [c[0] for c in calls]
        assert downloaded_values == sorted(downloaded_values)
        assert downloaded_values[-1] == len(data)
        assert all(total == spec.size_bytes for _, total in calls)

    def test_checksum_mismatch_raises_and_cleans_up(self, tmp_path: Path) -> None:
        data = b"model bytes"
        spec = ModelSpec(
            name="test-model",
            label="Test",
            url="https://example.com/model.bin",
            sha256="0" * 64,  # deliberately wrong
            size_bytes=len(data),
        )
        with patch("urllib.request.urlopen", return_value=_FakeResponse(data)):
            with pytest.raises(ModelChecksumMismatchError):
                download_model(spec, tmp_path)
        assert not model_path(spec, tmp_path).exists()
        assert list(tmp_path.iterdir()) == []

    def test_network_error_on_open_raises_download_error(self, tmp_path: Path) -> None:
        spec = _spec_for(b"data")
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            with pytest.raises(ModelDownloadError):
                download_model(spec, tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_network_error_mid_stream_raises_and_cleans_up(
        self, tmp_path: Path
    ) -> None:
        spec = _spec_for(b"x" * 100)

        class _FlakyResponse(_FakeResponse):
            def read(self, n: int) -> bytes:
                raise OSError("connection reset")

        with patch("urllib.request.urlopen", return_value=_FlakyResponse(b"")):
            with pytest.raises(ModelDownloadError):
                download_model(spec, tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_cancellation_raises_and_cleans_up(self, tmp_path: Path) -> None:
        data = b"x" * (5 * 1024 * 1024)  # multiple chunks so cancel can land mid-stream
        spec = _spec_for(data)
        cancel_event = threading.Event()
        cancel_event.set()  # already cancelled before the first chunk
        with patch("urllib.request.urlopen", return_value=_FakeResponse(data)):
            with pytest.raises(ModelDownloadCancelled):
                download_model(spec, tmp_path, cancel_event=cancel_event)
        assert list(tmp_path.iterdir()) == []

    def test_preexisting_good_install_survives_a_failed_redownload(
        self, tmp_path: Path
    ) -> None:
        good_data = b"good model bytes"
        spec = _spec_for(good_data)
        final = model_path(spec, tmp_path)
        final.write_bytes(good_data)

        bad_data = b"different bytes entirely"
        with patch("urllib.request.urlopen", return_value=_FakeResponse(bad_data)):
            with pytest.raises(ModelChecksumMismatchError):
                download_model(spec, tmp_path)

        assert final.read_bytes() == good_data  # untouched
