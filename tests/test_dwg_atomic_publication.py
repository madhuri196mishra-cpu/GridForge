from pathlib import Path

import pytest

from core.application.dwg_export.backends.oda_file_converter import ODAFileConverterBackend


def test_atomic_publication_replaces_destination_with_complete_output(tmp_path: Path) -> None:
    generated = tmp_path / "generated.dwg"
    destination = tmp_path / "final.dwg"
    generated.write_bytes(b"AC1032-complete")
    destination.write_bytes(b"old-output")

    ODAFileConverterBackend._publish_atomically(generated, destination)

    assert destination.read_bytes() == b"AC1032-complete"
    assert list(tmp_path.glob(".final.dwg.gridforge-*.tmp")) == []


def test_atomic_publication_failure_preserves_existing_destination_and_cleans_staging(tmp_path: Path, monkeypatch) -> None:
    generated = tmp_path / "generated.dwg"
    destination = tmp_path / "final.dwg"
    generated.write_bytes(b"new-output")
    destination.write_bytes(b"old-output")

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("core.application.dwg_export.backends.oda_file_converter.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        ODAFileConverterBackend._publish_atomically(generated, destination)

    assert destination.read_bytes() == b"old-output"
    assert list(tmp_path.glob(".final.dwg.gridforge-*.tmp")) == []
