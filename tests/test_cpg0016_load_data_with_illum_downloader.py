import io
import shutil
from pathlib import Path

import pandas as pd
import pytest

from jump_image_datasets.cpg0016 import load_data_with_illum_downloader


TEST_DATA_DIR = Path(__file__).parent / "data" / "cpg0016"


def _copy_test_csv_tree(destination: Path) -> None:
    shutil.copytree(TEST_DATA_DIR / "cpg0016-jump", destination / "cpg0016-jump")


class FakeS3FileSystem:
    def __init__(self, files: dict[str, bytes], glob_paths: list[str], anon: bool = True):
        self.files = files
        self.glob_paths = glob_paths
        self.anon = anon
        self.opened_paths: list[str] = []

    def glob(self, pattern: str) -> list[str]:
        assert (
            pattern
            == load_data_with_illum_downloader.LOAD_DATA_WITH_ILLUM_CSV_GLOB_PATTERN
        )
        return list(self.glob_paths)

    def open(self, remote_path: str, mode: str):
        assert mode == "rb"
        self.opened_paths.append(remote_path)
        if remote_path not in self.files:
            raise FileNotFoundError(remote_path)
        return io.BytesIO(self.files[remote_path])


class FailIfUsedS3FileSystem:
    def __init__(self, anon: bool = True):
        self.anon = anon

    def glob(self, pattern: str) -> list[str]:
        raise AssertionError("S3 glob should not be called")

    def open(self, remote_path: str, mode: str):
        raise AssertionError("S3 open should not be called")


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    dataframe = pd.DataFrame(rows)
    return dataframe.to_csv(index=False).encode("utf-8")


def test_constructor_discovers_downloads_and_excludes_source_all(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
        "cellpainting-gallery/cpg0016-jump/source_all/workspace/load_data_csv/run_all/plate_all/load_data_with_illum.csv",
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/load_data_csv/run_b/plate_b/load_data_with_illum.csv",
    ]
    files = {
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv": _csv_bytes(
            [{"URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy"}]
        ),
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/load_data_csv/run_b/plate_b/load_data_with_illum.csv": _csv_bytes(
            [{"URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_11/images/run_b/plate_b/agp.npy"}]
        ),
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=glob_paths, anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert len(downloader.csv_urls) == 2
    assert all("/source_all/" not in url for url in downloader.csv_urls)
    assert downloader.csv_download_summary.downloaded == 2
    assert (
        tmp_path
        / "cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    ).exists()
    assert (
        tmp_path
        / "cpg0016-jump/source_11/workspace/load_data_csv/run_b/plate_b/load_data_with_illum.csv"
    ).exists()


def test_get_dataframe_concatenates_downloaded_csvs_with_provenance(tmp_path, monkeypatch) -> None:
    _copy_test_csv_tree(tmp_path)
    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FailIfUsedS3FileSystem(anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
        use_existing_csvs_without_s3_check=True,
    )
    dataframe = downloader.get_dataframe()

    assert len(dataframe) == 3
    assert set(dataframe["Metadata_Source"]) == {"source_10", "source_11"}
    assert "Metadata_LoadDataCSVPath" in dataframe.columns
    assert "Metadata_LoadDataCSVURL" in dataframe.columns


def test_download_files_from_column_creates_directory_and_deduplicates(tmp_path, monkeypatch) -> None:
    _copy_test_csv_tree(tmp_path / "csvs")
    files = {
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy": b"npy-data",
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=[], anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path / "csvs",
        parallel=False,
        workers=1,
        verbose=False,
        use_existing_csvs_without_s3_check=True,
    )

    dataframe = downloader.get_dataframe().query("Metadata_Source == 'source_10'")
    dataframe["OutputDir"] = [str(tmp_path / "plate_a")] * len(dataframe)

    summary = downloader.download_files_from_column(
        dataframe=dataframe,
        column_name="URL_IllumAGP",
        output_dir_column="OutputDir",
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert summary.total_jobs == 1
    assert summary.downloaded == 1
    assert (tmp_path / "plate_a/agp.npy").exists()


def test_download_group_helpers_download_expected_file_types(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
    ]
    files = {
        glob_paths[0]: _csv_bytes(
            [
                {
                    "URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy",
                    "URL_IllumDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.npy",
                    "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif",
                    "URL_OrigAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.tif",
                    "URL_IllumBrightfield_L": None,
                    "URL_IllumBrightfield_H": None,
                    "URL_IllumBrightfield": None,
                    "URL_IllumRNA": None,
                    "URL_IllumMito": None,
                    "URL_IllumER": None,
                    "URL_OrigBrightfield_L": None,
                    "URL_OrigBrightfield_H": None,
                    "URL_OrigBrightfield": None,
                    "URL_OrigRNA": None,
                    "URL_OrigMito": None,
                    "URL_OrigER": None,
                }
            ]
        ),
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy": b"illum-agp",
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.npy": b"illum-dna",
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif": b"orig-dna",
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.tif": b"orig-agp",
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=glob_paths, anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path / "csvs",
        parallel=False,
        workers=1,
        verbose=False,
    )

    dataframe = downloader.get_dataframe()
    dataframe["OutputDir"] = str(tmp_path / "plate_outputs")

    illum_summary = downloader.download_illumination_files(
        dataframe=dataframe,
        output_dir_column="OutputDir",
        columns=["URL_IllumAGP", "URL_IllumDNA"],
        parallel=False,
        workers=1,
        verbose=False,
    )
    image_summary = downloader.download_image_files(
        dataframe=dataframe,
        output_dir_column="OutputDir",
        columns=["URL_OrigDNA", "URL_OrigAGP"],
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert illum_summary.downloaded == 2
    assert image_summary.downloaded == 2
    assert (tmp_path / "plate_outputs/agp.npy").exists()
    assert (tmp_path / "plate_outputs/dna.npy").exists()
    assert (tmp_path / "plate_outputs/dna.tif").exists()
    assert (tmp_path / "plate_outputs/agp.tif").exists()


def test_missing_required_columns_raise(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
    ]
    files = {
        glob_paths[0]: _csv_bytes([{"URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy"}]),
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=glob_paths, anon=anon),
    )

    with pytest.raises(ValueError, match="csv_download_dir must be provided"):
        load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
            csv_download_dir=None,
            parallel=False,
            workers=1,
            verbose=False,
        )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path / "csvs",
        parallel=False,
        workers=1,
        verbose=False,
    )

    dataframe = downloader.get_dataframe()

    with pytest.raises(ValueError, match="columns not found: MissingColumn"):
        downloader.download_files_from_column(
            dataframe=dataframe,
            column_name="MissingColumn",
            output_dir_column="OutputDir",
            parallel=False,
            workers=1,
            verbose=False,
        )

    with pytest.raises(ValueError, match="Output directory column not found: OutputDir"):
        downloader.download_files_from_column(
            dataframe=dataframe,
            column_name="URL_IllumAGP",
            output_dir_column="OutputDir",
            parallel=False,
            workers=1,
            verbose=False,
        )

    with pytest.raises(ValueError, match="columns not found: MissingA, MissingB"):
        downloader.download_image_files(
            dataframe=dataframe,
            output_dir_column="OutputDir",
            columns=["MissingA", "MissingB"],
            parallel=False,
            workers=1,
            verbose=False,
        )


def test_filtered_dataframe_is_respected(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/load_data_csv/run_b/plate_b/load_data_with_illum.csv",
    ]
    files = {
        glob_paths[0]: _csv_bytes(
            [{
                "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna_a.tif",
                "Metadata_Source": "source_10",
            }]
        ),
        glob_paths[1]: _csv_bytes(
            [{
                "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_11/images/run_b/plate_b/dna_b.tif",
                "Metadata_Source": "source_11",
            }]
        ),
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna_a.tif": b"A",
        "cellpainting-gallery/cpg0016-jump/source_11/images/run_b/plate_b/dna_b.tif": b"B",
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=glob_paths, anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path / "csvs",
        parallel=False,
        workers=1,
        verbose=False,
    )

    dataframe = downloader.get_dataframe()
    filtered_df = dataframe[dataframe["Metadata_Source"] == "source_10"].copy()
    filtered_df["OutputDir"] = str(tmp_path / "filtered")

    summary = downloader.download_files_from_column(
        dataframe=filtered_df,
        column_name="URL_OrigDNA",
        output_dir_column="OutputDir",
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert summary.total_jobs == 1
    assert summary.downloaded == 1
    assert (tmp_path / "filtered/dna_a.tif").exists()
    assert not (tmp_path / "filtered/dna_b.tif").exists()


def test_output_dir_column_supports_absolute_and_relative_paths(tmp_path, monkeypatch) -> None:
    remote_csv = "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    files = {
        remote_csv: _csv_bytes(
            [{
                "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif",
            }]
        ),
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif": b"dna",
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=[remote_csv], anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path / "csvs",
        parallel=False,
        workers=1,
        verbose=False,
    )

    relative_dir = tmp_path / "relative_dir"
    absolute_dir = tmp_path / "absolute_dir"
    dataframe = pd.DataFrame(
        {
            "URL_OrigDNA": [
                "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif",
                "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif",
            ],
            "OutputDir": [str(relative_dir), str(absolute_dir.resolve())],
        }
    )

    summary = downloader.download_files_from_column(
        dataframe=dataframe,
        column_name="URL_OrigDNA",
        output_dir_column="OutputDir",
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert summary.downloaded == 2
    assert (relative_dir / "dna.tif").exists()
    assert (absolute_dir / "dna.tif").exists()


def test_invalid_values_raise_with_row_index(tmp_path, monkeypatch) -> None:
    remote_csv = "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    files = {
        remote_csv: _csv_bytes([{"URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif"}]),
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=[remote_csv], anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path / "csvs",
        parallel=False,
        workers=1,
        verbose=False,
    )

    invalid_url_df = pd.DataFrame(
        {
            "URL_OrigDNA": ["not-an-s3-path"],
            "OutputDir": [str(tmp_path / "files")],
        }
    )
    with pytest.raises(ValueError, match=r"row index 0.*URL_OrigDNA"):
        downloader.download_files_from_column(
            dataframe=invalid_url_df,
            column_name="URL_OrigDNA",
            output_dir_column="OutputDir",
            parallel=False,
            workers=1,
            verbose=False,
        )

    invalid_output_dir_df = pd.DataFrame(
        {
            "URL_OrigDNA": ["s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif"],
            "OutputDir": ["   "],
        }
    )
    with pytest.raises(ValueError, match=r"row index 0.*OutputDir"):
        downloader.download_files_from_column(
            dataframe=invalid_output_dir_df,
            column_name="URL_OrigDNA",
            output_dir_column="OutputDir",
            parallel=False,
            workers=1,
            verbose=False,
        )

    missing_filename_df = pd.DataFrame(
        {
            "URL_OrigDNA": ["s3://cellpainting-gallery/"],
            "OutputDir": [str(tmp_path / "files")],
        }
    )
    with pytest.raises(ValueError, match=r"row index 0.*URL_OrigDNA"):
        downloader.download_files_from_column(
            dataframe=missing_filename_df,
            column_name="URL_OrigDNA",
            output_dir_column="OutputDir",
            parallel=False,
            workers=1,
            verbose=False,
        )


def test_conflicting_local_paths_raise(tmp_path, monkeypatch) -> None:
    remote_csv = "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    files = {remote_csv: _csv_bytes([{"URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif"}])}

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=[remote_csv], anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path / "csvs",
        parallel=False,
        workers=1,
        verbose=False,
    )

    dataframe = pd.DataFrame(
        {
            "URL_OrigDNA": ["s3://bucket/a/shared.tif", "s3://bucket/b/shared.tif"],
            "OutputDir": [str(tmp_path / "same_dir"), str(tmp_path / "same_dir")],
        }
    )

    with pytest.raises(ValueError, match=r"Conflicting S3 URLs for local path"):
        downloader.download_files_from_column(
            dataframe=dataframe,
            column_name="URL_OrigDNA",
            output_dir_column="OutputDir",
            parallel=False,
            workers=1,
            verbose=False,
        )


def test_constructor_prefilters_existing_csv_files(tmp_path, monkeypatch) -> None:
    remote_path = "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    local_path = tmp_path / "cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(_csv_bytes([{"URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy"}]))

    fake_fs = FakeS3FileSystem(files={}, glob_paths=[remote_path])
    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: fake_fs,
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert downloader.csv_download_summary.total_jobs == 1
    assert downloader.csv_download_summary.downloaded == 0
    assert downloader.csv_download_summary.skipped == 1
    assert downloader.csv_download_summary.failed == 0
    assert fake_fs.opened_paths == []


def test_constructor_overwrite_redownloads_existing_csv_files(tmp_path, monkeypatch) -> None:
    remote_path = "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    local_path = tmp_path / "cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(b"old")

    fake_fs = FakeS3FileSystem(
        files={remote_path: _csv_bytes([{"URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy"}])},
        glob_paths=[remote_path],
    )
    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: fake_fs,
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
        overwrite=True,
    )

    assert downloader.csv_download_summary.total_jobs == 1
    assert downloader.csv_download_summary.downloaded == 1
    assert downloader.csv_download_summary.skipped == 0
    assert downloader.csv_download_summary.failed == 0
    assert fake_fs.opened_paths == [remote_path]


def test_constructor_can_use_existing_local_csvs_without_s3_check(tmp_path, monkeypatch) -> None:
    _copy_test_csv_tree(tmp_path)
    local_path = tmp_path / "cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FailIfUsedS3FileSystem(anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
        use_existing_csvs_without_s3_check=True,
    )

    assert downloader.csv_urls == [
        "s3://cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
        "s3://cellpainting-gallery/cpg0016-jump/source_11/workspace/load_data_csv/run_b/plate_b/load_data_with_illum.csv",
    ]
    assert downloader.csv_jobs[0].local_path == local_path
    assert downloader.csv_download_summary.total_jobs == 2
    assert downloader.csv_download_summary.downloaded == 0
    assert downloader.csv_download_summary.skipped == 2
    assert downloader.csv_download_summary.failed == 0

    dataframe = downloader.get_dataframe()
    assert len(dataframe) == 3
    assert dataframe.loc[0, "Metadata_Source"] == "source_10"
    assert dataframe.loc[0, "Metadata_LoadDataCSVPath"] == str(local_path)
    assert (
        dataframe.loc[0, "Metadata_LoadDataCSVURL"]
        == "s3://cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv"
    )


def test_get_dataframe_raises_clear_error_when_csv_download_is_incomplete(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/load_data_csv/run_b/plate_b/load_data_with_illum.csv",
    ]
    files = {
        glob_paths[0]: _csv_bytes(
            [
                {
                    "URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy",
                    "Metadata_Source": "source_10",
                }
            ]
        ),
    }

    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files=files, glob_paths=glob_paths, anon=anon),
    )

    downloader = load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
        csv_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert downloader.csv_download_summary.failed == 1
    with pytest.raises(
        RuntimeError,
        match=r"Cannot build dataframe because one or more metadata CSV files are missing\.",
    ):
        downloader.get_dataframe()


def test_constructor_local_only_mode_requires_existing_local_csvs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        load_data_with_illum_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FailIfUsedS3FileSystem(anon=anon),
    )

    with pytest.raises(
        ValueError,
        match=(
            r"No local load_data_with_illum.csv files were found under csv_download_dir; "
            r"disable use_existing_csvs_without_s3_check to discover them from S3\."
        ),
    ):
        load_data_with_illum_downloader.CPG0016LoadDataWithIllumDownloader(
            csv_download_dir=tmp_path,
            parallel=False,
            workers=1,
            verbose=False,
            use_existing_csvs_without_s3_check=True,
        )
