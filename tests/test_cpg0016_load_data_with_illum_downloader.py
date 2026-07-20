import io
from pathlib import Path

import pandas as pd
import pytest

from jump_image_datasets.cpg0016 import load_data_with_illum_downloader


class FakeS3FileSystem:
    def __init__(self, files: dict[str, bytes], glob_paths: list[str], anon: bool = True):
        self.files = files
        self.glob_paths = glob_paths
        self.anon = anon
        self.opened_paths: list[str] = []

    def glob(self, pattern: str) -> list[str]:
        assert pattern == load_data_with_illum_downloader.CSV_GLOB_PATTERN
        return list(self.glob_paths)

    def open(self, remote_path: str, mode: str):
        assert mode == "rb"
        self.opened_paths.append(remote_path)
        if remote_path not in self.files:
            raise FileNotFoundError(remote_path)
        return io.BytesIO(self.files[remote_path])


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
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/load_data_csv/run_b/plate_b/load_data_with_illum.csv",
    ]
    files = {
        glob_paths[0]: _csv_bytes(
            [
                {
                    "URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy",
                    "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif",
                    "Metadata_Source": "source_10",
                }
            ]
        ),
        glob_paths[1]: _csv_bytes(
            [
                {
                    "URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_11/images/run_b/plate_b/agp.npy",
                    "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_11/images/run_b/plate_b/dna.tif",
                    "Metadata_Source": "source_11",
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
    dataframe = downloader.get_dataframe()

    assert len(dataframe) == 2
    assert set(dataframe["Metadata_Source"]) == {"source_10", "source_11"}
    assert "Metadata_LoadDataCSVPath" in dataframe.columns
    assert "Metadata_LoadDataCSVURL" in dataframe.columns


def test_download_files_from_column_creates_directory_and_deduplicates(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/load_data_csv/run_a/plate_a/load_data_with_illum.csv",
    ]
    files = {
        glob_paths[0]: _csv_bytes(
            [
                {
                    "URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy",
                    "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif",
                },
                {
                    "URL_IllumAGP": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy",
                    "URL_OrigDNA": "s3://cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna_2.tif",
                },
            ]
        ),
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy": b"npy-data",
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif": b"tif-data",
        "cellpainting-gallery/cpg0016-jump/source_10/images/run_a/plate_a/dna_2.tif": b"tif-data-2",
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

    summary = downloader.download_files_from_column(
        column_name="URL_IllumAGP",
        download_dir=tmp_path / "illum_files",
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert summary.total_jobs == 1
    assert summary.downloaded == 1
    assert (
        tmp_path
        / "illum_files/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy"
    ).exists()


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

    illum_summary = downloader.download_illumination_files(
        download_dir=tmp_path / "illum",
        columns=["URL_IllumAGP", "URL_IllumDNA"],
        parallel=False,
        workers=1,
        verbose=False,
    )
    image_summary = downloader.download_image_files(
        download_dir=tmp_path / "images",
        columns=["URL_OrigDNA", "URL_OrigAGP"],
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert illum_summary.downloaded == 2
    assert image_summary.downloaded == 2
    assert (tmp_path / "illum/cpg0016-jump/source_10/images/run_a/plate_a/agp.npy").exists()
    assert (tmp_path / "illum/cpg0016-jump/source_10/images/run_a/plate_a/dna.npy").exists()
    assert (tmp_path / "images/cpg0016-jump/source_10/images/run_a/plate_a/dna.tif").exists()
    assert (tmp_path / "images/cpg0016-jump/source_10/images/run_a/plate_a/agp.tif").exists()


def test_missing_download_directory_or_column_raises(tmp_path, monkeypatch) -> None:
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

    with pytest.raises(ValueError, match="download_dir must be provided"):
        downloader.download_files_from_column(
            column_name="URL_IllumAGP",
            download_dir=None,
            parallel=False,
            workers=1,
            verbose=False,
        )

    with pytest.raises(ValueError, match="column not found: MissingColumn"):
        downloader.download_files_from_column(
            column_name="MissingColumn",
            download_dir=tmp_path / "files",
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
