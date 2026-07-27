import io

import pandas as pd
import pytest

from jump_image_datasets.cpg0016 import analysis_csv_downloader


class FakeS3FileSystem:
    def __init__(self, files: dict[str, bytes], glob_paths: list[str], anon: bool = True):
        self.files = files
        self.glob_paths = glob_paths
        self.anon = anon
        self.opened_paths: list[str] = []

    def glob(self, pattern: str) -> list[str]:
        assert pattern == analysis_csv_downloader.ANALYSIS_CSV_GLOB_PATTERN
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


def test_constructor_discovers_manifest_and_excludes_source_all(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Cells.csv",
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv",
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Cytoplasm.csv",
        "cellpainting-gallery/cpg0016-jump/source_all/workspace/analysis/run_all/plate_all/analysis/plate_all-A01-1/Nuclei.csv",
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/analysis/run_b/plate_b/analysis/plate_b-B03-2/Nuclei.csv",
    ]

    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files={}, glob_paths=glob_paths, anon=anon),
    )

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        manifest_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    dataframe = downloader.get_dataframe()
    assert len(downloader.analysis_csv_urls) == 4
    assert all("/source_all/" not in url for url in downloader.analysis_csv_urls)
    assert list(dataframe.columns) == analysis_csv_downloader.ANALYSIS_MANIFEST_COLUMNS
    assert len(dataframe) == 2
    assert dataframe.loc[0, "Metadata_Source"] == "source_10"
    assert dataframe.loc[0, "Metadata_Batch"] == "run_a"
    assert dataframe.loc[0, "Metadata_Plate"] == "plate_a"
    assert dataframe.loc[0, "Metadata_Well"] == "A01"
    assert dataframe.loc[0, "Metadata_Site"] == "1"
    assert dataframe.loc[0, "Cells_S3_Path"].endswith("Cells.csv")
    assert dataframe.loc[0, "Cytoplasm_S3_Path"].endswith("Cytoplasm.csv")
    assert dataframe.loc[0, "Nuclei_S3_Path"].endswith("Nuclei.csv")


def test_manifest_keeps_rows_when_one_analysis_csv_is_missing(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/analysis/run_b/plate_b/analysis/plate_b-B03-2/Nuclei.csv",
        "cellpainting-gallery/cpg0016-jump/source_11/workspace/analysis/run_b/plate_b/analysis/plate_b-B03-2/Cells.csv",
    ]

    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files={}, glob_paths=glob_paths, anon=anon),
    )

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        manifest_download_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    dataframe = downloader.get_dataframe()
    assert len(dataframe) == 1
    assert pd.isna(dataframe.loc[0, "Cytoplasm_S3_Path"])
    assert dataframe.loc[0, "Metadata_Site"] == "2"


def test_manifest_can_be_saved_and_reused_without_s3(tmp_path, monkeypatch) -> None:
    manifest_csv_path = tmp_path / "manifests" / "analysis_paths.csv"
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv",
    ]

    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files={}, glob_paths=glob_paths, anon=anon),
    )
    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        manifest_download_dir=tmp_path / "cache",
        manifest_csv_path=manifest_csv_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert manifest_csv_path.exists()
    assert downloader.get_dataframe().loc[0, "Metadata_Site"] == "1"

    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FailIfUsedS3FileSystem(anon=anon),
    )
    local_only_downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        manifest_download_dir=tmp_path / "cache",
        manifest_csv_path=manifest_csv_path,
        parallel=False,
        workers=1,
        verbose=False,
        use_existing_manifest_without_s3_check=True,
    )

    reused_dataframe = local_only_downloader.get_dataframe()
    assert reused_dataframe.loc[0, "Metadata_Site"] == "1"
    assert reused_dataframe.loc[0, "Nuclei_S3_Path"].endswith("Nuclei.csv")


def test_local_only_mode_requires_existing_manifest_csv(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FailIfUsedS3FileSystem(anon=anon),
    )

    with pytest.raises(
        ValueError,
        match="manifest_csv_path must be provided when use_existing_manifest_without_s3_check=True",
    ):
        analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
            manifest_download_dir=tmp_path,
            parallel=False,
            workers=1,
            verbose=False,
            use_existing_manifest_without_s3_check=True,
        )

    with pytest.raises(
        ValueError,
        match="manifest_csv_path does not exist; disable use_existing_manifest_without_s3_check to discover it from S3",
    ):
        analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
            manifest_download_dir=tmp_path,
            manifest_csv_path=tmp_path / "missing.csv",
            parallel=False,
            workers=1,
            verbose=False,
            use_existing_manifest_without_s3_check=True,
        )


def test_download_csvs_from_column_preserves_relative_s3_subdirectories(tmp_path, monkeypatch) -> None:
    remote_nuclei = (
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/"
        "GR00004416/analysis/GR00004416-A01-3/Nuclei.csv"
    )
    remote_cells = (
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/"
        "GR00004416/analysis/GR00004416-A01-3/Cells.csv"
    )
    files = {
        remote_nuclei: b"nuclei-data",
        remote_cells: b"cells-data",
    }

    fake_fs = FakeS3FileSystem(files=files, glob_paths=[])
    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: fake_fs,
    )

    manifest_df = pd.DataFrame(
        {
            "Metadata_Plate": ["GR00004416"],
            "Metadata_Well": ["A01"],
            "Metadata_Site": ["3"],
            "Metadata_Batch": ["20211103-Run16"],
            "Metadata_Source": ["source_10"],
            "Nuclei_S3_Path": [f"s3://{remote_nuclei}"],
            "Cells_S3_Path": [f"s3://{remote_cells}"],
            "Cytoplasm_S3_Path": [pd.NA],
            "OutputRoot": [str(tmp_path / "downloads")],
        }
    )
    bootstrap_downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        manifest_download_dir=tmp_path / "cache",
        parallel=False,
        workers=1,
        verbose=False,
    )
    bootstrap_downloader.save_dataframe_csv(
        manifest_df[analysis_csv_downloader.ANALYSIS_MANIFEST_COLUMNS],
        tmp_path / "manifest.csv",
    )
    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        manifest_download_dir=tmp_path / "cache",
        manifest_csv_path=tmp_path / "manifest.csv",
        parallel=False,
        workers=1,
        verbose=False,
        use_existing_manifest_without_s3_check=True,
    )

    summary = downloader.download_csvs_from_columns(
        dataframe=manifest_df,
        columns=["Nuclei_S3_Path", "Cells_S3_Path"],
        output_root_column="OutputRoot",
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert summary.total_jobs == 2
    assert summary.downloaded == 2
    assert (
        tmp_path
        / "downloads/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Nuclei.csv"
    ).exists()
    assert (
        tmp_path
        / "downloads/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Cells.csv"
    ).exists()
    assert fake_fs.opened_paths == [remote_nuclei, remote_cells]


def test_invalid_dataframe_values_raise_clear_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FailIfUsedS3FileSystem(anon=anon),
    )
    manifest_csv = tmp_path / "manifest.csv"
    pd.DataFrame(columns=analysis_csv_downloader.ANALYSIS_MANIFEST_COLUMNS).to_csv(manifest_csv, index=False)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        manifest_download_dir=tmp_path,
        manifest_csv_path=manifest_csv,
        parallel=False,
        workers=1,
        verbose=False,
        use_existing_manifest_without_s3_check=True,
    )

    with pytest.raises(ValueError, match="columns not found: MissingColumn"):
        downloader.download_csvs_from_column(
            dataframe=pd.DataFrame({"OutputRoot": [str(tmp_path)]}),
            column_name="MissingColumn",
            output_root_column="OutputRoot",
            parallel=False,
            workers=1,
            verbose=False,
        )

    invalid_url_df = pd.DataFrame(
        {
            "Nuclei_S3_Path": ["not-an-s3-path"],
            "OutputRoot": [str(tmp_path / "downloads")],
        }
    )
    with pytest.raises(ValueError, match=r"row index 0.*Nuclei_S3_Path"):
        downloader.download_csvs_from_column(
            dataframe=invalid_url_df,
            column_name="Nuclei_S3_Path",
            output_root_column="OutputRoot",
            parallel=False,
            workers=1,
            verbose=False,
        )

    invalid_output_root_df = pd.DataFrame(
        {
            "Nuclei_S3_Path": ["s3://cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run/plate/analysis/plate-A01-1/Nuclei.csv"],
            "OutputRoot": ["   "],
        }
    )
    with pytest.raises(ValueError, match=r"row index 0.*OutputRoot"):
        downloader.download_csvs_from_column(
            dataframe=invalid_output_root_df,
            column_name="Nuclei_S3_Path",
            output_root_column="OutputRoot",
            parallel=False,
            workers=1,
            verbose=False,
        )


def test_parse_analysis_csv_s3_url_rejects_invalid_plate_well_site() -> None:
    with pytest.raises(ValueError, match="Invalid analysis CSV S3 URL"):
        analysis_csv_downloader.parse_analysis_csv_s3_url(
            "s3://cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/other_plate-A01-1/Nuclei.csv"
        )
