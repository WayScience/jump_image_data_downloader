import io
from pathlib import Path

import pandas as pd
import pytest

from jump_image_datasets.jump_pilot import image_downloader
from jump_image_datasets.jump_pilot import image_metadata


class FakeS3FileSystem:
    def __init__(self, files: dict[str, bytes], anon: bool = True):
        self.files = files
        self.anon = anon

    def open(self, remote_path: str, mode: str):
        assert mode == "rb"
        if remote_path not in self.files:
            raise FileNotFoundError(remote_path)
        return io.BytesIO(self.files[remote_path])


def test_build_jobs_filters_deduplicates_and_limits() -> None:
    df = pd.DataFrame(
        {
            "Metadata_FileUrl": [
                "s3://bucket/alpha.tiff",
                "s3://bucket/alpha.tiff",
                "s3://bucket/notes.txt",
                "s3://bucket/beta.TIFF",
            ]
        }
    )

    jobs = image_downloader.build_jobs(
        df=df,
        url_column="Metadata_FileUrl",
        default_output_dir=Path("out"),
    )
    assert [job.local_path.name for job in jobs] == ["alpha.tiff", "beta.TIFF"]

    limited_jobs = image_downloader.build_jobs(
        df=df,
        url_column="Metadata_FileUrl",
        default_output_dir=Path("out"),
        max_files=1,
    )
    assert len(limited_jobs) == 1
    assert limited_jobs[0].local_path.name == "alpha.tiff"


def test_downloaded_filenames_match_metadata_and_location(tmp_path, monkeypatch) -> None:
    df = pd.DataFrame(
        {
            "Metadata_FileUrl": [
                "s3://bucket/path/r01c01f01p01-ch5sk1fk1fl1.tiff",
                "s3://bucket/path/r01c01f02p01-ch5sk1fk1fl1.tiff",
                "s3://bucket/path/r01c01f02p01-ch5sk1fk1fl1.tiff",
            ],
            "Metadata_Filename": [
                "r01c01f01p01-ch5sk1fk1fl1.tiff",
                "r01c01f02p01-ch5sk1fk1fl1.tiff",
                "r01c01f02p01-ch5sk1fk1fl1.tiff",
            ],
        }
    )

    fake_files = {
        "bucket/path/r01c01f01p01-ch5sk1fk1fl1.tiff": b"file-1",
        "bucket/path/r01c01f02p01-ch5sk1fk1fl1.tiff": b"file-2",
    }

    monkeypatch.setattr(
        image_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(fake_files, anon=anon),
    )

    out_dir = tmp_path / "downloaded_images"
    summary = image_downloader.download_images_with_metadata(
        df=df,
        url_column="Metadata_FileUrl",
        default_output_dir=out_dir,
        parallel=False,
        workers=1,
        verbose=False,
    )

    expected_filenames = sorted(df["Metadata_Filename"].drop_duplicates().tolist())
    downloaded_files = sorted(path.name for path in out_dir.glob("*.tiff"))

    assert summary.total_jobs == 2
    assert summary.downloaded == 2
    assert summary.skipped == 0
    assert summary.failed == 0
    assert downloaded_files == expected_filenames
    assert all((out_dir / filename).exists() for filename in expected_filenames)


def test_download_images_respects_output_dir_column(tmp_path, monkeypatch) -> None:
    plate_a = tmp_path / "plate_a"
    plate_b = tmp_path / "plate_b"
    df = pd.DataFrame(
        {
            "Metadata_FileUrl": [
                "s3://bucket/path/a.tiff",
                "s3://bucket/path/b.tiff",
            ],
            "OutputDir": [str(plate_a), str(plate_b)],
        }
    )

    fake_files = {
        "bucket/path/a.tiff": b"A",
        "bucket/path/b.tiff": b"B",
    }

    monkeypatch.setattr(
        image_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(fake_files, anon=anon),
    )

    summary = image_downloader.download_images_with_metadata(
        df=df,
        url_column="Metadata_FileUrl",
        output_dir_column="OutputDir",
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert summary.downloaded == 2
    assert (plate_a / "a.tiff").exists()
    assert (plate_b / "b.tiff").exists()


def test_non_parallel_workers_validation() -> None:
    df = pd.DataFrame({"Metadata_FileUrl": ["s3://bucket/path/a.tiff"]})

    with pytest.raises(ValueError, match="workers cannot be > 1 when parallel=False"):
        image_downloader.download_images_with_metadata(
            df=df,
            url_column="Metadata_FileUrl",
            parallel=False,
            workers=2,
            verbose=False,
            dry_run=True,
        )


def test_real_metadata_slice_download_plan_and_output_paths(tmp_path, monkeypatch) -> None:
    metadata_df = image_metadata.load_metadata(use_cache=True)

    slice_df = (
        metadata_df.loc[:, ["Metadata_FileUrl", "Metadata_Filename"]]
        .dropna(subset=["Metadata_FileUrl", "Metadata_Filename"])
        .drop_duplicates(subset=["Metadata_FileUrl"])
        .head(3)
        .copy()
    )
    assert len(slice_df) == 3

    jobs = image_downloader.build_jobs(
        df=slice_df,
        url_column="Metadata_FileUrl",
        default_output_dir=tmp_path,
    )

    expected_filenames = slice_df["Metadata_Filename"].tolist()
    planned_filenames = [job.local_path.name for job in jobs]
    assert planned_filenames == expected_filenames

    fake_files = {
        image_downloader.s3_url_to_remote_path(job.s3_url): job.local_path.name.encode("utf-8")
        for job in jobs
    }
    monkeypatch.setattr(
        image_downloader.s3fs,
        "S3FileSystem",
        lambda anon=True: FakeS3FileSystem(fake_files, anon=anon),
    )

    summary = image_downloader.download_images_with_metadata(
        df=slice_df,
        url_column="Metadata_FileUrl",
        default_output_dir=tmp_path,
        parallel=False,
        workers=1,
        verbose=False,
    )

    assert summary.total_jobs == 3
    assert summary.downloaded == 3
    assert summary.failed == 0
    for expected_name in expected_filenames:
        assert (tmp_path / expected_name).exists()
