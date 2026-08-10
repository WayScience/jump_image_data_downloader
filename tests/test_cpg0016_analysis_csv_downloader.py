import io
import os
import shutil
from pathlib import Path

import pytest

from jump_image_datasets.cpg0016 import analysis_csv_downloader


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


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_downloaded_csv(output_dir: Path, relative_path: str, contents: str = "ImageNumber\n1\n") -> None:
    local_path = output_dir / relative_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(contents)


def _install_fake_aws(
    monkeypatch,
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    side_effect=None,
):
    captured: dict[str, object] = {}

    monkeypatch.setattr(analysis_csv_downloader.shutil, "which", lambda name: "/usr/bin/aws")

    def fake_run(command, check, capture_output, text, env):
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["env"] = dict(env)
        if side_effect is not None:
            side_effect(command=command, env=env, capture_output=capture_output)
        return FakeCompletedProcess(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(analysis_csv_downloader.subprocess, "run", fake_run)
    return captured


def test_discover_analysis_csv_urls_excludes_source_all(tmp_path, monkeypatch) -> None:
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
    urls = downloader.discover_analysis_csv_urls()

    assert len(urls) == 5
    assert all("/source_all/" not in url for url in urls)


def test_build_analysis_csv_sets_from_local_paths_uses_realistic_fixture_subset(tmp_path) -> None:
    _copy_test_csv_tree(tmp_path)

    local_paths = sorted((tmp_path / "cpg0016-jump").rglob("workspace/analysis/**/*.csv"))
    csv_sets = analysis_csv_downloader.build_analysis_csv_sets_from_local_paths(
        local_paths,
        output_dir=tmp_path,
    )

    assert len(csv_sets) == 3
    assert csv_sets[0].folder_s3_url is None
    assert csv_sets[0].folder_relative_path == (
        Path("cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1")
    )
    assert csv_sets[0].image_local_path == (
        tmp_path
        / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Image.csv"
    )
    assert csv_sets[0].nuclei_local_path == (
        tmp_path
        / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv"
    )
    assert csv_sets[0].cells_local_path == (
        tmp_path
        / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Cells.csv"
    )
    assert csv_sets[0].cytoplasm_local_path is None
    assert csv_sets[1].folder_relative_path == (
        Path(
            "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/"
            "subdir_a/plate_a-A01-1"
        )
    )
    assert csv_sets[1].image_local_path == (
        tmp_path
        / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/subdir_a/plate_a-A01-1/Image.csv"
    )
    assert csv_sets[1].nuclei_local_path == (
        tmp_path
        / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/subdir_a/plate_a-A01-1/Nuclei.csv"
    )
    assert csv_sets[2].folder_relative_path == (
        Path("cpg0016-jump/source_all/workspace/analysis/run_all/plate_all/analysis/plate_all-A01-1")
    )


def test_local_only_mode_uses_realistic_fixture_subset_and_skips_source_all(tmp_path, monkeypatch) -> None:
    _copy_test_csv_tree(tmp_path / "downloads")

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

    csv_sets = downloader.get_analysis_csv_sets()
    assert len(csv_sets) == 2
    assert all("source_all" not in str(csv_set.folder_relative_path) for csv_set in csv_sets)
    assert csv_sets[0].folder_s3_url is None
    assert csv_sets[0].image_local_path.exists()
    assert csv_sets[0].nuclei_local_path.exists()
    assert csv_sets[0].cells_local_path.exists()
    assert csv_sets[1].folder_relative_path == (
        Path(
            "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/"
            "subdir_a/plate_a-A01-1"
        )
    )
    assert csv_sets[1].image_local_path.exists()
    assert csv_sets[1].nuclei_local_path.exists()


def test_download_all_csv_profiles_preserves_relative_s3_subdirectories(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "downloads"

    def side_effect(command, env, capture_output) -> None:
        assert command[:4] == [
            "/usr/bin/aws",
            "s3",
            "cp",
            "s3://cellpainting-gallery/cpg0016-jump/",
        ]
        assert command[4] == str(output_dir / "cpg0016-jump")
        assert "--recursive" in command
        assert "--no-sign-request" in command
        assert "--only-show-errors" in command
        assert "source_all/*" in command
        assert command.count("--include") == 4
        config_text = Path(env["AWS_CONFIG_FILE"]).read_text()
        assert "max_concurrent_requests = 1" in config_text
        assert capture_output is True

        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Image.csv",
            "ImageNumber,Metadata_Well\n1,A01\n",
        )
        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Nuclei.csv",
            "ImageNumber,ObjectNumber\n1,1\n",
        )
        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Cells.csv",
            "ImageNumber,ObjectNumber\n1,1\n",
        )

    _install_fake_aws(monkeypatch, side_effect=side_effect)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=output_dir,
        parallel=False,
        workers=1,
        verbose=False,
    )

    summary = downloader.download_all_csv_profiles()

    assert summary.total_jobs == 3
    assert summary.downloaded == 3
    assert summary.skipped == 0
    assert (
        output_dir
        / "cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Image.csv"
    ).exists()
    assert (
        output_dir
        / "cpg0016-jump/source_10/workspace/analysis/20211103-Run16/GR00004416/analysis/GR00004416-A01-3/Nuclei.csv"
    ).exists()

    csv_set = next(downloader.iter_analysis_csv_sets())
    assert csv_set.folder_s3_url == (
        "s3://cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/"
        "GR00004416/analysis/GR00004416-A01-3/"
    )
    assert csv_set.image_s3_url == (
        "s3://cellpainting-gallery/cpg0016-jump/source_10/workspace/analysis/20211103-Run16/"
        "GR00004416/analysis/GR00004416-A01-3/Image.csv"
    )
    assert csv_set.image_local_path.exists()
    assert csv_set.nuclei_local_path.exists()
    assert csv_set.cells_local_path.exists()


def test_download_all_csv_profiles_can_filter_requested_csvs(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "downloads"

    def side_effect(command, env, capture_output) -> None:
        assert command.count("--include") == 1
        assert "source_*/workspace/analysis/**/Nuclei.csv" in command
        assert "source_*/workspace/analysis/**/Image.csv" not in command
        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv",
        )

    _install_fake_aws(monkeypatch, side_effect=side_effect)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=output_dir,
        parallel=False,
        workers=1,
        verbose=False,
    )

    summary = downloader.download_all_csv_profiles(csv_names=["nuclei"])

    assert summary.total_jobs == 1
    assert summary.downloaded == 1
    assert list(output_dir.rglob("Image.csv")) == []
    assert (
        output_dir
        / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv"
    ).exists()


def test_download_all_csv_profiles_accepts_mixed_csv_name_forms(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "downloads"

    def side_effect(command, env, capture_output) -> None:
        assert command.count("--include") == 2
        assert "source_*/workspace/analysis/**/Image.csv" in command
        assert "source_*/workspace/analysis/**/Nuclei.csv" in command
        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Image.csv",
        )
        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv",
        )

    _install_fake_aws(monkeypatch, side_effect=side_effect)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=output_dir,
        parallel=False,
        workers=1,
        verbose=False,
    )

    summary = downloader.download_all_csv_profiles(csv_names=["image", "Nuclei.csv", "IMAGE"])

    assert summary.total_jobs == 2
    assert summary.downloaded == 2


def test_download_all_csv_profiles_rejects_invalid_csv_names(tmp_path, monkeypatch) -> None:
    _install_fake_aws(monkeypatch)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path / "downloads",
        parallel=False,
        workers=1,
        verbose=False,
    )

    with pytest.raises(ValueError, match="Invalid analysis CSV names: 'not_a_csv'"):
        downloader.download_all_csv_profiles(csv_names=["not_a_csv"])


def test_analysis_csv_set_read_csv_overwrites_metadata_source_from_s3_path(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "downloads"

    def side_effect(command, env, capture_output) -> None:
        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Image.csv",
            "ImageNumber,Metadata_Source\n1,wrong_source\n",
        )

    _install_fake_aws(monkeypatch, side_effect=side_effect)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=output_dir,
        parallel=False,
        workers=1,
        verbose=False,
    )
    downloader.download_all_csv_profiles(csv_names=["image"])

    csv_set = next(downloader.iter_analysis_csv_sets())
    dataframe = csv_set.read_csv("Image.csv")

    assert dataframe.loc[0, "Metadata_Source"] == "source_10"


def test_download_all_csv_profiles_reports_existing_files_as_skipped(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "downloads"
    local_nuclei = (
        output_dir
        / "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Nuclei.csv"
    )
    local_nuclei.parent.mkdir(parents=True, exist_ok=True)
    local_nuclei.write_bytes(b"existing-data")

    _install_fake_aws(monkeypatch, side_effect=lambda **kwargs: None)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=output_dir,
        parallel=False,
        workers=1,
        verbose=False,
    )

    summary = downloader.download_all_csv_profiles(csv_names=["nuclei"])

    assert summary.total_jobs == 1
    assert summary.downloaded == 0
    assert summary.skipped == 1
    assert local_nuclei.read_bytes() == b"existing-data"


def test_download_all_csv_profiles_uses_requested_max_concurrent_requests(tmp_path, monkeypatch) -> None:
    output_dir = tmp_path / "downloads"

    def side_effect(command, env, capture_output) -> None:
        config_text = Path(env["AWS_CONFIG_FILE"]).read_text()
        assert "max_concurrent_requests = 17" in config_text
        _write_downloaded_csv(
            output_dir,
            "cpg0016-jump/source_10/workspace/analysis/run_a/plate_a/analysis/plate_a-A01-1/Image.csv",
        )

    _install_fake_aws(monkeypatch, side_effect=side_effect)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=output_dir,
        parallel=True,
        workers=1,
        verbose=False,
        max_concurrent_requests=17,
    )

    summary = downloader.download_all_csv_profiles(csv_names=["image"])

    assert summary.downloaded == 1


def test_download_all_csv_profiles_raises_when_aws_cli_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(analysis_csv_downloader.shutil, "which", lambda name: None)

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path / "downloads",
        parallel=False,
        workers=1,
        verbose=False,
    )

    with pytest.raises(RuntimeError, match="AWS CLI is required"):
        downloader.download_all_csv_profiles(csv_names=["image"])


def test_download_all_csv_profiles_raises_on_aws_failure(tmp_path, monkeypatch) -> None:
    _install_fake_aws(monkeypatch, returncode=2, stderr="boom")

    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path / "downloads",
        parallel=False,
        workers=1,
        verbose=False,
    )

    with pytest.raises(RuntimeError, match="AWS CLI analysis CSV download failed: boom"):
        downloader.download_all_csv_profiles(csv_names=["image"])


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


def test_analysis_csv_set_read_csv_overwrites_metadata_source_from_local_path(tmp_path, monkeypatch) -> None:
    local_image = (
        tmp_path
        / "downloads/cpg0016-jump/source_11/workspace/analysis/run_b/plate_b/analysis/plate_b-B03-2/Image.csv"
    )
    local_image.parent.mkdir(parents=True, exist_ok=True)
    local_image.write_text("ImageNumber,Metadata_Source\n1,wrong_source\n")

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

    csv_set = next(downloader.iter_analysis_csv_sets())
    dataframe = csv_set.read_csv("Image.csv")

    assert dataframe.loc[0, "Metadata_Source"] == "source_11"


def test_constructor_requires_output_dir() -> None:
    try:
        analysis_csv_downloader.CPG0016AnalysisCSVDownloader(output_dir=None)
    except ValueError as exc:
        assert str(exc) == "output_dir must be provided"
    else:
        raise AssertionError("Expected ValueError")


def test_constructor_requires_positive_max_concurrent_requests(tmp_path) -> None:
    with pytest.raises(ValueError, match="max_concurrent_requests must be >= 1"):
        analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
            output_dir=tmp_path,
            max_concurrent_requests=0,
        )


@pytest.mark.skipif(
    os.environ.get("JUMP_RUN_S3_TESTS") != "1",
    reason="Set JUMP_RUN_S3_TESTS=1 to run live public S3 discovery checks.",
)
def test_live_s3_discovery_excludes_source_all(tmp_path) -> None:
    downloader = analysis_csv_downloader.CPG0016AnalysisCSVDownloader(
        output_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )
    urls = downloader.discover_analysis_csv_urls()

    assert urls
    assert all(url.startswith("s3://cellpainting-gallery/cpg0016-jump/") for url in urls)
    assert all("/source_all/" not in url for url in urls)
