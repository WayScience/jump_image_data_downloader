"""Download and access CPG0016 ``load_data_with_illum.csv`` metadata tables.

This module discovers all per-plate ``load_data_with_illum.csv`` files under the
public ``cellpainting-gallery`` bucket for ``cpg0016-jump`` while excluding
``source_all``. The downloader mirrors the public-S3 and local directory
creation behavior used by the JUMP pilot image downloader.
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


CPG0016_BUCKET = "cellpainting-gallery"
CPG0016_PREFIX = "cpg0016-jump"
LOAD_DATA_WITH_ILLUM_CSV_GLOB_PATTERN = (
    f"{CPG0016_BUCKET}/{CPG0016_PREFIX}/source_*/workspace/load_data_csv/*/*/"
    "load_data_with_illum.csv"
)
ILLUMINATION_COLUMNS = [
    "URL_IllumBrightfield_L",
    "URL_IllumBrightfield_H",
    "URL_IllumBrightfield",
    "URL_IllumRNA",
    "URL_IllumMito",
    "URL_IllumER",
    "URL_IllumDNA",
    "URL_IllumAGP",
]
IMAGE_COLUMNS = [
    "URL_OrigBrightfield_L",
    "URL_OrigBrightfield_H",
    "URL_OrigBrightfield",
    "URL_OrigRNA",
    "URL_OrigMito",
    "URL_OrigER",
    "URL_OrigDNA",
    "URL_OrigAGP",
]


@dataclass(frozen=True)
class DownloadJob:
    """Single download task from one S3 URL to one local file path."""

    s3_url: str
    local_path: Path


@dataclass(frozen=True)
class DownloadSummary:
    """Aggregate counts and error details from a download run."""

    total_jobs: int
    downloaded: int
    skipped: int
    failed: int
    failures: list[tuple[DownloadJob, str]]


def validate_column(dataframe: pd.DataFrame, column_name: str, kind: str) -> None:
    """Ensure a required dataframe column exists."""

    if column_name not in dataframe.columns:
        raise ValueError(f"{kind} column not found: {column_name}")


def s3_url_to_remote_path(s3_url: str) -> str:
    """Convert an ``s3://`` URL into the ``bucket/key`` format used by ``s3fs``."""

    parsed = urlparse(s3_url)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URL: {s3_url}")
    return f"{parsed.netloc}{parsed.path}"


def remote_path_to_s3_url(remote_path: str) -> str:
    """Convert a ``bucket/key`` path into an ``s3://`` URL."""

    cleaned_remote_path = str(remote_path).strip().lstrip("/")
    if "/" not in cleaned_remote_path:
        raise ValueError(f"Invalid remote path: {remote_path}")
    bucket, key = cleaned_remote_path.split("/", 1)
    if not bucket or not key:
        raise ValueError(f"Invalid remote path: {remote_path}")
    return f"s3://{bucket}/{key}"


def is_source_all_path(path_str: str) -> bool:
    """Return whether a remote or relative dataset path points at ``source_all``."""

    return "/source_all/" in f"/{str(path_str).strip().strip('/')}/"


def s3_url_to_relative_local_path(s3_url: str) -> Path:
    """Map an ``s3://`` URL to a stable relative local path.

    The bucket name is excluded so downloaded files land under a concise,
    dataset-relative tree rooted at the caller-provided output directory.
    """

    remote_path = s3_url_to_remote_path(s3_url)
    _, key = remote_path.split("/", 1)
    return Path(key)


def s3_url_to_filename(s3_url: str) -> str:
    """Extract the filename from an ``s3://`` URL."""

    parsed = urlparse(s3_url)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URL: {s3_url}")

    filename = Path(parsed.path).name
    if not filename:
        raise ValueError(f"S3 URL does not include a filename: {s3_url}")
    return filename


def download_one(
    fs: s3fs.S3FileSystem,
    job: DownloadJob,
    overwrite: bool = False,
) -> tuple[str, DownloadJob, Optional[str]]:
    """Download one file and return status, job, and optional error message."""

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


def run_download_jobs(
    jobs: list[DownloadJob],
    *,
    overwrite: bool = False,
    workers: int = 4,
    parallel: bool = True,
    verbose: bool = True,
) -> DownloadSummary:
    """Execute download jobs and return a summary."""

    if workers < 1:
        raise ValueError("workers must be >= 1")
    if not parallel and workers > 1:
        raise ValueError("workers cannot be > 1 when parallel=False")

    preexisting_jobs = 0
    jobs_to_run = jobs
    if not overwrite:
        # Count already-downloaded files up front so progress reflects skipped
        # work without spending executor slots on jobs that would no-op.
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

    return DownloadSummary(
        total_jobs=len(jobs),
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        failures=failures,
    )


class CPG0016LoadDataWithIllumDownloader:
    """Discover, download, and read CPG0016 metadata CSVs and referenced files."""

    def __init__(
        self,
        csv_download_dir: Path | str,
        *,
        overwrite: bool = False,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
        use_existing_csvs_without_s3_check: bool = False,
    ) -> None:
        if csv_download_dir is None:
            raise ValueError("csv_download_dir must be provided")

        self.csv_download_dir = Path(csv_download_dir)
        self.csv_download_dir.mkdir(parents=True, exist_ok=True)
        self.overwrite = overwrite
        self.workers = workers
        self.parallel = parallel
        self.verbose = verbose
        self.use_existing_csvs_without_s3_check = use_existing_csvs_without_s3_check

        if self.use_existing_csvs_without_s3_check:
            self.csv_jobs = self._discover_local_csv_jobs()
            self.csv_urls = [job.s3_url for job in self.csv_jobs]
            self.csv_download_summary = DownloadSummary(
                total_jobs=len(self.csv_jobs),
                downloaded=0,
                skipped=len(self.csv_jobs),
                failed=0,
                failures=[],
            )
        else:
            self.csv_urls = self.discover_csv_urls()
            self.csv_jobs = self._build_jobs(self.csv_urls, self.csv_download_dir)
            self.csv_download_summary = run_download_jobs(
                self.csv_jobs,
                overwrite=self.overwrite,
                workers=self.workers,
                parallel=self.parallel,
                verbose=self.verbose,
            )

    def discover_csv_urls(self) -> list[str]:
        """List all public CPG0016 metadata CSVs while excluding ``source_all``."""

        fs = s3fs.S3FileSystem(anon=True)
        remote_paths = sorted(fs.glob(LOAD_DATA_WITH_ILLUM_CSV_GLOB_PATTERN))
        return [
            remote_path_to_s3_url(remote_path)
            for remote_path in remote_paths
            if not is_source_all_path(remote_path)
        ]

    def _build_jobs(
        self,
        s3_urls: list[str],
        base_dir: Path | str,
    ) -> list[DownloadJob]:
        """Build unique download jobs preserving source-relative directory structure."""

        base_dir_path = Path(base_dir)
        jobs: list[DownloadJob] = []
        seen_urls: set[str] = set()

        for s3_url in s3_urls:
            cleaned_url = str(s3_url).strip()
            if not cleaned_url or cleaned_url in seen_urls:
                continue
            seen_urls.add(cleaned_url)
            relative_path = s3_url_to_relative_local_path(cleaned_url)
            jobs.append(
                DownloadJob(
                    s3_url=cleaned_url,
                    local_path=base_dir_path / relative_path,
                )
            )

        return jobs

    def _discover_local_csv_jobs(self) -> list[DownloadJob]:
        """Build download jobs from existing local CSVs without querying S3."""

        local_csv_paths = [
            local_path
            for local_path in sorted(self.csv_download_dir.glob("**/load_data_with_illum.csv"))
            if not is_source_all_path(local_path.relative_to(self.csv_download_dir).as_posix())
        ]
        if not local_csv_paths:
            raise ValueError(
                "No local load_data_with_illum.csv files were found under "
                "csv_download_dir; disable use_existing_csvs_without_s3_check "
                "to discover them from S3."
            )

        return [
            DownloadJob(
                s3_url=self._local_csv_path_to_s3_url(local_path),
                local_path=local_path,
            )
            for local_path in local_csv_paths
        ]

    def _local_csv_path_to_s3_url(self, local_path: Path) -> str:
        """Convert a local CSV path under ``csv_download_dir`` back into its S3 URL."""

        relative_path = local_path.relative_to(self.csv_download_dir).as_posix().lstrip("/")
        return f"s3://{CPG0016_BUCKET}/{relative_path}"

    def get_dataframe(self) -> pd.DataFrame:
        """Load all downloaded metadata CSVs and concatenate them into one DataFrame."""

        missing_jobs = [job for job in self.csv_jobs if not job.local_path.exists()]
        if missing_jobs:
            raise RuntimeError(
                "Cannot build dataframe because one or more metadata CSV files are missing. "
                "Inspect csv_download_summary.failures and retry the failed downloads. "
                f"Missing files: {len(missing_jobs)}"
            )

        dataframes: list[pd.DataFrame] = []
        for job in self.csv_jobs:
            dataframe = pd.read_csv(job.local_path)
            dataframe["Metadata_LoadDataCSVPath"] = str(job.local_path)
            dataframe["Metadata_LoadDataCSVURL"] = job.s3_url
            dataframes.append(dataframe)

        if not dataframes:
            return pd.DataFrame()

        return pd.concat(dataframes, ignore_index=True)

    def download_files_from_column(
        self,
        dataframe: pd.DataFrame,
        column_name: str,
        *,
        output_dir_column: str,
        overwrite: bool = False,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
    ) -> DownloadSummary:
        """Download unique S3 files from one metadata column.

        The caller must provide both the metadata rows to use and a column of
        per-row output directories. Files are saved as
        ``Path(output_dir_column_value) / filename``.
        """

        jobs = self._build_jobs_from_dataframe(
            dataframe=dataframe,
            columns=[column_name],
            output_dir_column=output_dir_column,
        )
        return run_download_jobs(
            jobs,
            overwrite=overwrite,
            workers=workers,
            parallel=parallel,
            verbose=verbose,
        )

    def download_illumination_files(
        self,
        dataframe: pd.DataFrame,
        *,
        output_dir_column: str,
        columns: Optional[list[str]] = None,
        overwrite: bool = False,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
    ) -> DownloadSummary:
        """Download unique illumination ``.npy`` files from explicit metadata rows."""

        return self._download_files_from_columns(
            dataframe=dataframe,
            columns=columns or ILLUMINATION_COLUMNS,
            output_dir_column=output_dir_column,
            overwrite=overwrite,
            workers=workers,
            parallel=parallel,
            verbose=verbose,
        )

    def download_image_files(
        self,
        dataframe: pd.DataFrame,
        *,
        output_dir_column: str,
        columns: Optional[list[str]] = None,
        overwrite: bool = False,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
    ) -> DownloadSummary:
        """Download unique original image ``.tif`` files from explicit metadata rows."""

        return self._download_files_from_columns(
            dataframe=dataframe,
            columns=columns or IMAGE_COLUMNS,
            output_dir_column=output_dir_column,
            overwrite=overwrite,
            workers=workers,
            parallel=parallel,
            verbose=verbose,
        )

    def _download_files_from_columns(
        self,
        *,
        dataframe: pd.DataFrame,
        columns: list[str],
        output_dir_column: str,
        overwrite: bool,
        workers: int,
        parallel: bool,
        verbose: bool,
    ) -> DownloadSummary:
        """Download unique S3 files referenced across multiple DataFrame columns."""

        jobs = self._build_jobs_from_dataframe(
            dataframe=dataframe,
            columns=columns,
            output_dir_column=output_dir_column,
        )
        return run_download_jobs(
            jobs,
            overwrite=overwrite,
            workers=workers,
            parallel=parallel,
            verbose=verbose,
        )

    def _build_jobs_from_dataframe(
        self,
        *,
        dataframe: pd.DataFrame,
        columns: list[str],
        output_dir_column: str,
    ) -> list[DownloadJob]:
        """Build validated download jobs from explicit metadata rows.

        Validation is fail-fast and reports the row index and column for invalid
        output directories, invalid S3 URLs, and local-path collisions.
        """

        missing_columns = [column for column in columns if column not in dataframe.columns]
        if missing_columns:
            missing = ", ".join(missing_columns)
            raise ValueError(f"columns not found: {missing}")
        validate_column(dataframe, output_dir_column, "Output directory")

        jobs: list[DownloadJob] = []
        planned_paths: dict[Path, tuple[str, object, str]] = {}

        # Walk each requested row/column pair once, skipping missing URLs,
        # deduplicating repeated references, and failing fast if two distinct
        # S3 URLs would write to the same local file.
        for row_index, row in dataframe.iterrows():
            raw_output_dir = row[output_dir_column]
            if pd.isna(raw_output_dir) or str(raw_output_dir).strip() == "":
                raise ValueError(
                    f"Invalid output directory value at row index {row_index} "
                    f"for column {output_dir_column}: {raw_output_dir!r}"
                )
            output_dir = Path(str(raw_output_dir).strip())

            for column in columns:
                raw_s3_url = row[column]
                if pd.isna(raw_s3_url):
                    continue

                s3_url = str(raw_s3_url).strip()
                if not s3_url:
                    continue
                if not s3_url.startswith("s3://"):
                    raise ValueError(
                        f"Invalid S3 URL at row index {row_index} for column {column}: {raw_s3_url!r}"
                    )

                try:
                    filename = s3_url_to_filename(s3_url)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid S3 URL at row index {row_index} for column {column}: {raw_s3_url!r}"
                    ) from exc

                local_path = output_dir / filename
                existing = planned_paths.get(local_path)
                if existing is None:
                    planned_paths[local_path] = (s3_url, row_index, column)
                    jobs.append(DownloadJob(s3_url=s3_url, local_path=local_path))
                    continue

                existing_s3_url, existing_row_index, existing_column = existing
                if existing_s3_url == s3_url:
                    continue

                raise ValueError(
                    f"Conflicting S3 URLs for local path {local_path}: "
                    f"row index {existing_row_index} column {existing_column} uses {existing_s3_url!r}, "
                    f"but row index {row_index} column {column} uses {s3_url!r}"
                )

        return jobs
