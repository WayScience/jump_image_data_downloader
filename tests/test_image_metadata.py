from pathlib import Path

import pandas as pd

from jump_image_datasets.jump_pilot import image_metadata


def test_get_metadata_path_points_to_packaged_parquet() -> None:
    metadata_path = image_metadata.get_metadata_path()
    assert metadata_path.exists()
    assert metadata_path.name == image_metadata.METADATA_FILENAME


def test_load_metadata_reads_packaged_data() -> None:
    metadata_df = image_metadata.load_metadata(use_cache=False)
    assert not metadata_df.empty
    assert "Metadata_FileUrl" in metadata_df.columns
    assert "Metadata_Filename" in metadata_df.columns


def test_load_metadata_uses_optional_cache(monkeypatch) -> None:
    image_metadata.clear_metadata_cache()

    expected_path = Path("/tmp/fake_metadata.parquet")
    call_count = {"count": 0}
    expected_df = pd.DataFrame({"x": [1, 2, 3]})

    def fake_get_metadata_path() -> Path:
        return expected_path

    def fake_read_parquet(path: Path) -> pd.DataFrame:
        call_count["count"] += 1
        assert path == expected_path
        return expected_df

    monkeypatch.setattr(image_metadata, "get_metadata_path", fake_get_metadata_path)
    monkeypatch.setattr(image_metadata.pd, "read_parquet", fake_read_parquet)

    first = image_metadata.load_metadata(use_cache=True)
    second = image_metadata.load_metadata(use_cache=True)
    third = image_metadata.load_metadata(use_cache=False)

    assert call_count["count"] == 2
    assert first is second
    assert third is expected_df
