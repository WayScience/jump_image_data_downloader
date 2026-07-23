"""Utilities for planning and downloading TIFF images from JUMP metadata tables.

This module converts metadata rows containing S3 URLs into concrete download jobs,
then executes those jobs either serially or with a thread pool.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import pandas as pd
import s3fs


@dataclass(frozen=True)
class DownloadJob:
    """Single download task mapping one S3 URL to one local file path.

    Attributes
    ----------
    s3_url
        Source image URL in ``s3://bucket/key`` form.
    local_path
        Destination path where the image should be written.
    """

    s3_url: str
    local_path: Path


@dataclass(frozen=True)
class DownloadSummary:
    """Aggregate counts and error details from a download run.

    Attributes
    ----------
    total_jobs
        Number of unique TIFF download jobs considered.
    downloaded
        Number of files downloaded in this run.
    skipped
        Number of files skipped because they already existed and ``overwrite``
        was ``False``.
    failed
        Number of failed download attempts.
    failures
        List of ``(DownloadJob, error_message)`` tuples for failed downloads.
    """

    total_jobs: int
    downloaded: int
    skipped: int
    failed: int
    failures: list[tuple[DownloadJob, str]]


def validate_column(df: pd.DataFrame, column_name: str, kind: str) -> None:
    """Ensure a required dataframe column exists.

    Parameters
    ----------
    df
        Input metadata dataframe.
    column_name
        Column required for the current operation.
    kind
        Human-readable label used in the error message.

    Raises
    ------
    ValueError
        If ``column_name`` is not present in ``df``.
    """

    if column_name not in df.columns:
        raise ValueError(f"{kind} column not found: {column_name}")


def s3_url_to_remote_path(s3_url: str) -> str:
    """Convert an ``s3://`` URL into ``bucket/key`` for ``s3fs``.

    Parameters
    ----------
    s3_url
        Fully qualified S3 URL, for example ``s3://my-bucket/path/file.tiff``.

    Returns
    -------
    str
        The ``bucket/key`` path expected by ``s3fs`` APIs.

    Raises
    ------
    ValueError
        If ``s3_url`` is not a valid S3 URL.
    """

    parsed = urlparse(s3_url)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URL: {s3_url}")
    return f"{parsed.netloc}{parsed.path}"


def build_jobs(
    df: pd.DataFrame,
    url_column: str,
    output_dir_column: Optional[str] = None,
    default_output_dir: Path | str = Path("downloaded_jump_pilot_images"),
    max_files: Optional[int] = None,
) -> list[DownloadJob]:
    """Create validated download jobs from metadata columns in a dataframe.

    Parameters
    ----------
    df
        Metadata dataframe containing at least ``url_column``.
    url_column
        Column containing image S3 URLs.
    output_dir_column
        Optional column containing per-row output directories. If omitted,
        ``default_output_dir`` is used for all files.
    default_output_dir
        Fallback output directory when ``output_dir_column`` is not provided.
    max_files
        Optional maximum number of jobs to keep after validation, filtering,
        and deduplication.

    Returns
    -------
    list[DownloadJob]
        Planned download jobs. Only URLs ending in ``.tiff`` (case-insensitive)
        are included, and jobs are deduplicated by URL.

    Raises
    ------
    ValueError
        If required columns are missing.
    """

    validate_column(df, url_column, "URL")
    if output_dir_column is not None:
        validate_column(df, output_dir_column, "Output directory")

    cols = [url_column]
    if output_dir_column is not None:
        cols.append(output_dir_column)

    jobs_df = df[cols].copy()
    jobs_df = jobs_df.dropna(subset=[url_column])
    jobs_df[url_column] = jobs_df[url_column].astype(str).str.strip()
    jobs_df = jobs_df[jobs_df[url_column].str.lower().str.endswith(".tiff")]

    if output_dir_column is not None:
        jobs_df = jobs_df.dropna(subset=[output_dir_column])
        jobs_df[output_dir_column] = jobs_df[output_dir_column].astype(str).str.strip()
        jobs_df = jobs_df[jobs_df[output_dir_column] != ""]

    jobs_df = jobs_df.drop_duplicates(subset=[url_column]).reset_index(drop=True)

    default_output_dir_path = Path(default_output_dir)
    jobs: list[DownloadJob] = []
    for _, row in jobs_df.iterrows():
        s3_url = row[url_column]
        filename = Path(urlparse(s3_url).path).name
        local_dir = (
            Path(row[output_dir_column])
            if output_dir_column is not None
            else default_output_dir_path
        )
        jobs.append(DownloadJob(s3_url=s3_url, local_path=local_dir / filename))

    if max_files is not None:
        jobs = jobs[:max_files]

    return jobs


def download_one(
    fs: s3fs.S3FileSystem,
    job: DownloadJob,
    overwrite: bool = False,
) -> tuple[str, DownloadJob, Optional[str]]:
    """Download one image and return status, job, and optional error message.

    Parameters
    ----------
    fs
        S3 filesystem client used for reading remote objects.
    job
        Download job containing source URL and destination file path.
    overwrite
        If ``True``, overwrite existing local files. If ``False``, existing
        files are counted as skipped.

    Returns
    -------
    tuple[str, DownloadJob, str | None]
        A tuple of ``(status, job, error_message)`` where status is one of
        ``"downloaded"``, ``"skipped"``, or ``"failed"``.
    """

    try:
        job.local_path.parent.mkdir(parents=True, exist_ok=True)
        if job.local_path.exists() and not overwrite:
            return ("skipped", job, None)

        remote = s3_url_to_remote_path(job.s3_url)
        with fs.open(remote, "rb") as src, open(job.local_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)

        return ("downloaded", job, None)
    except Exception as exc:  # noqa: BLE001
        return ("failed", job, str(exc))


def download_images_with_metadata(
    df: pd.DataFrame,
    url_column: str,
    output_dir_column: Optional[str] = None,
    default_output_dir: Path | str = Path("downloaded_jump_pilot_images"),
    workers: int = 4,
    parallel: bool = True,
    max_files: Optional[int] = None,
    overwrite: bool = False,
    dry_run: bool = False,
    verbose: bool = True,
) -> DownloadSummary:
    """Download TIFF images listed in a dataframe and report run statistics.

    Parameters
    ----------
    df
        Metadata dataframe containing at least ``url_column``.
    url_column
        Column containing image S3 URLs.
    output_dir_column
        Optional column containing per-row output directories.
    default_output_dir
        Default destination directory when ``output_dir_column`` is not used.
    workers
        Number of worker threads to use when ``parallel=True``.
    parallel
        If ``True``, downloads are executed with a thread pool. If ``False``,
        downloads are executed serially.
    max_files
        Optional cap on number of planned jobs.
    overwrite
        If ``True``, replace existing files; otherwise existing files are
        counted as skipped.
    dry_run
        If ``True``, validate and plan jobs without downloading files.
    verbose
        If ``True``, print progress and summary information.

    Returns
    -------
    DownloadSummary
        Aggregate counts and failure details for the run.

    Raises
    ------
    ValueError
        If ``workers < 1`` or if ``parallel`` is ``False`` and ``workers > 1``.
    """

    if workers < 1:
        raise ValueError("workers must be >= 1")
    if not parallel and workers > 1:
        raise ValueError("workers cannot be > 1 when parallel=False")

    jobs = build_jobs(
        df=df,
        url_column=url_column,
        output_dir_column=output_dir_column,
        default_output_dir=Path(default_output_dir),
        max_files=max_files,
    )

    if verbose:
        print(f"Rows in dataframe: {len(df):,}")
        print(f"Unique TIFF files to process: {len(jobs):,}")

    if dry_run:
        if verbose:
            for job in jobs[:20]:
                print(f"[DRY RUN] {job.s3_url} -> {job.local_path}")
            if len(jobs) > 20:
                print(f"[DRY RUN] ... and {len(jobs) - 20} more")
        return DownloadSummary(
            total_jobs=len(jobs),
            downloaded=0,
            skipped=0,
            failed=0,
            failures=[],
        )

    preexisting_jobs = 0
    jobs_to_run = jobs
    if not overwrite:
        jobs_to_run = []
        for job in jobs:
            if job.local_path.exists():
                preexisting_jobs += 1
            else:
                jobs_to_run.append(job)

    fs = s3fs.S3FileSystem(anon=True)

    downloaded = 0
    skipped = preexisting_jobs
    failed = 0
    failures: list[tuple[DownloadJob, str]] = []

    if parallel:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(download_one, fs, job, overwrite) for job in jobs_to_run]
            for index, future in enumerate(as_completed(futures), start=1):
                status, job, error = future.result()
                if status == "downloaded":
                    downloaded += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1
                    failures.append((job, error or "Unknown error"))
                    if verbose:
                        print(f"[FAILED] {job.s3_url} -> {job.local_path}\n  {error}")

                if verbose and (index % 200 == 0 or index == len(jobs_to_run)):
                    print(
                        f"Progress {index + preexisting_jobs:,}/{len(jobs):,} "
                        f"(downloaded={downloaded:,}, skipped={skipped:,}, failed={failed:,})"
                    )
    else:
        for index, job in enumerate(jobs_to_run, start=1):
            status, job, error = download_one(fs, job, overwrite)
            if status == "downloaded":
                downloaded += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
                failures.append((job, error or "Unknown error"))
                if verbose:
                    print(f"[FAILED] {job.s3_url} -> {job.local_path}\n  {error}")

            if verbose and (index % 200 == 0 or index == len(jobs_to_run)):
                print(
                    f"Progress {index + preexisting_jobs:,}/{len(jobs):,} "
                    f"(downloaded={downloaded:,}, skipped={skipped:,}, failed={failed:,})"
                )

    if verbose and preexisting_jobs and not jobs_to_run:
        print(
            f"Progress {len(jobs):,}/{len(jobs):,} "
            f"(downloaded={downloaded:,}, skipped={skipped:,}, failed={failed:,})"
        )

    if verbose:
        print("Done.")
        print(f"Downloaded: {downloaded:,}")
        print(f"Skipped:    {skipped:,}")
        print(f"Failed:     {failed:,}")

    return DownloadSummary(
        total_jobs=len(jobs),
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        failures=failures,
    )
