import io
from pathlib import Path

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


def test_constructor_discovers_csv_sets_and_excludes_source_all(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Image.csv",
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv",
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Cells.csv",
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
        output_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    csv_sets = downloader.get_analysis_csv_sets()
    assert len(downloader.analysis_csv_urls) == 5
    assert all("/source_all/" not in url for url in downloader.analysis_csv_urls)
    assert len(csv_sets) == 2
    assert csv_sets[0].folder_s3_url == (
        "s3://cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/"
        "plate_a/analysis/plate_a-A01-1/"
    )
    assert csv_sets[0].image_s3_url.endswith("Image.csv")
    assert csv_sets[0].nuclei_s3_url.endswith("Nuclei.csv")
    assert csv_sets[0].cells_s3_url.endswith("Cells.csv")
    assert csv_sets[0].cytoplasm_s3_url.endswith("Cytoplasm.csv")
    assert csv_sets[0].folder_local_path == (
        tmp_path / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1"
    )
    assert csv_sets[1].nuclei_s3_url.endswith("Nuclei.csv")


def test_constructor_supports_deeper_analysis_paths(tmp_path, monkeypatch) -> None:
    glob_paths = [
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/subdir_a/plate_a-A01-1/Image.csv",
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/subdir_a/plate_a-A01-1/Nuclei.csv",
    ]

    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(files={}, glob_paths=glob_paths, anon=anon),
    )

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    csv_set = downloader.get_analysis_csv_sets()[0]
    assert csv_set.folder_relative_path == (
        Path("cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/subdir_a/plate_a-A01-1")
    )


def test_download_all_csv_profiles_preserves_relative_s3_subdirectories(tmp_path, monkeypatch) -> None:
    remote_image = (
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/"
        "GR00004416/analysis/GR00004416-A01-3/Image.csv"
    )
    remote_nuclei = (
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/"
        "GR00004416/analysis/GR00004416-A01-3/Nuclei.csv"
    )
    remote_cells = (
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/"
        "GR00004416/analysis/GR00004416-A01-3/Cells.csv"
    )
    files = {
        remote_image: b"ImageNumber,Metadata_Well\n1,A01\n",
        remote_nuclei: b"ImageNumber,ObjectNumber\n1,1\n",
        remote_cells: b"ImageNumber,ObjectNumber\n1,1\n",
    }

    fake_fs = FakeS3FileSystem(
        files=files,
        glob_paths=[remote_image, remote_nuclei, remote_cells],
    )
    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: fake_fs,
    )

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path / "downloads",
        parallel=False,
        workers=1,
        verbose=False,
    )

    summary = downloader.download_all_csv_profiles()

    assert summary.total_jobs == 3
    assert summary.downloaded == 3
    assert (
        tmp_path
        / "downloads/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Image.csv"
    ).exists()
    assert (
        tmp_path
        / "downloads/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Nuclei.csv"
    ).exists()
    assert fake_fs.opened_paths == [remote_cells, remote_image, remote_nuclei]

    csv_set = next(downloader.iter_analysis_csv_sets())
    assert csv_set.image_local_path.exists()
    assert csv_set.nuclei_local_path.exists()
    assert csv_set.cells_local_path.exists()


def test_download_all_csv_profiles_skips_existing_files_without_reopening_s3(tmp_path, monkeypatch) -> None:
    remote_nuclei = (
        "cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/run_a/"
        "plate_a/analysis/plate_a-A01-1/Nuclei.csv"
    )
    local_nuclei = (
        tmp_path
        / "downloads/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv"
    )
    local_nuclei.parent.mkdir(parents=True, exist_ok=True)
    local_nuclei.write_bytes(b"existing-data")

    fake_fs = FakeS3FileSystem(files={remote_nuclei: b"new-data"}, glob_paths=[remote_nuclei])
    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: fake_fs,
    )

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path / "downloads",
        parallel=False,
        workers=1,
        verbose=False,
    )

    summary = downloader.download_all_csv_profiles()

    assert summary.total_jobs == 1
    assert summary.downloaded == 0
    assert summary.skipped == 1
    assert fake_fs.opened_paths == []
    assert local_nuclei.read_bytes() == b"existing-data"


def test_local_only_mode_uses_existing_downloads_without_s3(tmp_path, monkeypatch) -> None:
    local_image = (
        tmp_path
        / "downloads/cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Image.csv"
    )
    local_nuclei = local_image.with_name("Nuclei.csv")
    local_image.parent.mkdir(parents=True, exist_ok=True)
    local_image.write_text("ImageNumber\n1\n")
    local_nuclei.write_text("ImageNumber\n1\n")

    monkeypatch.setattr(
        analysis_csv_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FailIfUsedS3FileSystem(anon=anon),
    )

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path / "downloads",
        parallel=False,
        workers=1,
        verbose=False,
        use_existing_csvs_without_s3_check=True,
    )

    csv_sets = list(downloader.iter_analysis_csv_sets())
    assert len(csv_sets) == 1
    assert csv_sets[0].folder_s3_url is None
    assert csv_sets[0].image_local_path == local_image
    assert csv_sets[0].nuclei_local_path == local_nuclei

    summary = downloader.download_all_csv_profiles()
    assert summary.total_jobs == 0
    assert summary.downloaded == 0
    assert summary.skipped == 0


def test_constructor_requires_output_dir() -> None:
    try:
        analysis_csv_downloader.CPG0016AnalysisCSVDownloader(output_dir=None)
    except ValueError as exc:
        assert str(exc) == "output_dir must be provided"
    else:
        raise AssertionError("Expected ValueError")
