"""Discover and download CPG0016 single-cell analysis CSV files.

This module indexes the public ``workspace/analysis`` tree for
``cellpainting-gallery/cpg0016-jump`` and groups discovered CSVs by their
containing analysis folder. Each grouped record exposes the common CellProfiler
profile files such as ``Image.csv``, ``Nuclei.csv``, ``Cells.csv``, and
``Cytoplasm.csv`` while preserving the S3-relative local download layout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional, Sequence
from urllib.parse import urlparse

import pandas as pd
import s3fs

from jump_image_datasets.cpg0016.load_data_with_illum_downloader import (
    CPG0016_BUCKET,
    CPG0016_PREFIX,
    DownloadSummary,
    is_source_all_path,
    remote_path_to_s3_url,
    s3_url_to_relative_local_path,
)


ANALYSIS_CSV_GLOB_PATTERN = f"{CPG0016_BUCKET}/{CPG0016_PREFIX}/*/workspace/analysis/**/*.csv"
ANALYSIS_PROFILE_FILENAMES = (
    "Image.csv",
    "Nuclei.csv",
    "Cells.csv",
    "Cytoplasm.csv",
)
AWS_DEFAULT_MAX_CONCURRENT_REQUESTS = 50
ANALYSIS_PROFILE_NAME_ALIASES = {
    "image": "Image.csv",
    "image.csv": "Image.csv",
    "nuclei": "Nuclei.csv",
    "nuclei.csv": "Nuclei.csv",
    "cells": "Cells.csv",
    "cells.csv": "Cells.csv",
    "cytoplasm": "Cytoplasm.csv",
    "cytoplasm.csv": "Cytoplasm.csv",
}
SOURCE_NAME_PATTERN = re.compile(r"^source_[^/]+$")


@dataclass(frozen=True)
class AnalysisCSVSet:
    """Grouped analysis CSV paths for one analysis folder.

    Attributes
    ----------
    folder_relative_path
        Dataset-relative folder containing one set of analysis CSV files.
    folder_local_path
        Local folder path under the downloader's ``output_dir``.
    folder_s3_url
        S3 URL for the folder when the record came from S3 discovery. This is
        ``None`` in local-only mode.
    image_s3_url, nuclei_s3_url, cells_s3_url, cytoplasm_s3_url
        S3 URLs for the common CellProfiler CSVs when available.
    image_local_path, nuclei_local_path, cells_local_path, cytoplasm_local_path
        Expected or discovered local paths for the common CellProfiler CSVs.
    other_s3_urls, other_local_paths
        Additional CSV files keyed by filename for callers that need more than
        the standard profile tables.
    """

    folder_relative_path: Path
    folder_local_path: Path
    folder_s3_url: Optional[str] = None
    image_s3_url: Optional[str] = None
    nuclei_s3_url: Optional[str] = None
    cells_s3_url: Optional[str] = None
    cytoplasm_s3_url: Optional[str] = None
    image_local_path: Optional[Path] = None
    nuclei_local_path: Optional[Path] = None
    cells_local_path: Optional[Path] = None
    cytoplasm_local_path: Optional[Path] = None
    other_s3_urls: dict[str, str] = field(default_factory=dict)
    other_local_paths: dict[str, Path] = field(default_factory=dict)

    def read_csv(self, filename: str) -> pd.DataFrame:
        """Read one analysis CSV and set path-derived metadata when available.

        Parameters
        ----------
        filename
            Analysis CSV filename to load from this grouped record.

        Returns
        -------
        pandas.DataFrame
            Loaded CSV contents with ``Metadata_Source`` and ``Metadata_Batch``
            overwritten from the dataset path when those segments can be
            extracted from the S3 or local provenance path.
        """

        local_path = self._get_local_path(filename)
        source_path = self._get_source_path(filename)
        dataframe = pd.read_csv(local_path)
        try:
            metadata_values = extract_metadata_from_dataset_path(source_path)
        except ValueError:
            return dataframe

        dataframe["Metadata_Source"] = metadata_values["Metadata_Source"]
        dataframe["Metadata_Batch"] = metadata_values["Metadata_Batch"]
        return dataframe

    def _get_local_path(self, filename: str) -> Path:
        """Return the local path for one CSV filename or raise if it is unavailable.

        Parameters
        ----------
        filename
            Analysis CSV filename to resolve within this grouped record.

        Returns
        -------
        pathlib.Path
            Local filesystem path for the requested CSV.

        Raises
        ------
        ValueError
            If the requested CSV is not present in this grouped record.
        """

        local_path = self._get_local_path_or_none(filename)
        if local_path is None:
            raise ValueError(f"CSV not available in this analysis set: {filename}")
        return local_path

    def _get_source_path(self, filename: str) -> str:
        """Return the best provenance path for one CSV, preferring the S3 URL.

        Parameters
        ----------
        filename
            Analysis CSV filename to resolve within this grouped record.

        Returns
        -------
        str
            S3 URL when available, otherwise the local filesystem path for the
            requested CSV.
        """

        s3_url = self._get_s3_url_or_none(filename)
        if s3_url is not None:
            return s3_url
        return str(self._get_local_path(filename))

    def _get_local_path_or_none(self, filename: str) -> Optional[Path]:
        """Return the local path for one CSV filename when present in this set.

        Parameters
        ----------
        filename
            Analysis CSV filename to resolve within this grouped record.

        Returns
        -------
        pathlib.Path | None
            Local filesystem path for the requested CSV, or ``None`` when this
            grouped record does not include that file.
        """

        if filename == "Image.csv":
            return self.image_local_path
        if filename == "Nuclei.csv":
            return self.nuclei_local_path
        if filename == "Cells.csv":
            return self.cells_local_path
        if filename == "Cytoplasm.csv":
            return self.cytoplasm_local_path
        return self.other_local_paths.get(filename)

    def _get_s3_url_or_none(self, filename: str) -> Optional[str]:
        """Return the S3 URL for one CSV filename when present in this set.

        Parameters
        ----------
        filename
            Analysis CSV filename to resolve within this grouped record.

        Returns
        -------
        str | None
            S3 URL for the requested CSV, or ``None`` when this grouped record
            does not include a remote URL for that file.
        """

        if filename == "Image.csv":
            return self.image_s3_url
        if filename == "Nuclei.csv":
            return self.nuclei_s3_url
        if filename == "Cells.csv":
            return self.cells_s3_url
        if filename == "Cytoplasm.csv":
            return self.cytoplasm_s3_url
        return self.other_s3_urls.get(filename)


def _split_dataset_path_parts(path_str: str | Path) -> list[str]:
    """Return normalized path segments for an S3 URL or mirrored local path."""

    raw_path = str(path_str).strip()
    parsed = urlparse(raw_path)
    if parsed.scheme == "s3":
        if parsed.netloc != CPG0016_BUCKET:
            raise ValueError(f"Invalid CPG0016 dataset path: {path_str}")
        return [part for part in parsed.path.split("/") if part]
    return [part for part in raw_path.replace("\\", "/").split("/") if part]


def extract_metadata_from_dataset_path(path_str: str | Path) -> dict[str, str]:
    """Extract CPG0016 source and batch segments from a dataset path."""

    path_parts = _split_dataset_path_parts(path_str)

    try:
        prefix_index = path_parts.index(CPG0016_PREFIX)
        source = path_parts[prefix_index + 1]
        if path_parts[prefix_index + 2 : prefix_index + 4] != ["workspace", "analysis"]:
            raise ValueError
        batch = path_parts[prefix_index + 4]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Invalid CPG0016 dataset path: {path_str}") from exc

    return {
        "Metadata_Source": source,
        "Metadata_Batch": batch,
    }


def extract_metadata_source_from_dataset_path(path_str: str | Path) -> str:
    """Extract the CPG0016 source segment from an S3 URL or mirrored local path."""

    return extract_metadata_from_dataset_path(path_str)["Metadata_Source"]


def extract_metadata_batch_from_dataset_path(path_str: str | Path) -> str:
    """Extract the CPG0016 batch segment from an S3 URL or mirrored local path."""

    return extract_metadata_from_dataset_path(path_str)["Metadata_Batch"]


def _copy_analysis_csv_set(csv_set: AnalysisCSVSet) -> AnalysisCSVSet:
    """Return a defensive copy of one grouped CSV-set record."""

    return AnalysisCSVSet(
        folder_relative_path=csv_set.folder_relative_path,
        folder_local_path=csv_set.folder_local_path,
        folder_s3_url=csv_set.folder_s3_url,
        image_s3_url=csv_set.image_s3_url,
        nuclei_s3_url=csv_set.nuclei_s3_url,
        cells_s3_url=csv_set.cells_s3_url,
        cytoplasm_s3_url=csv_set.cytoplasm_s3_url,
        image_local_path=csv_set.image_local_path,
        nuclei_local_path=csv_set.nuclei_local_path,
        cells_local_path=csv_set.cells_local_path,
        cytoplasm_local_path=csv_set.cytoplasm_local_path,
        other_s3_urls=dict(csv_set.other_s3_urls),
        other_local_paths=dict(csv_set.other_local_paths),
    )


def _folder_remote_path_to_s3_url(folder_remote_path: str) -> str:
    """Convert a remote folder path into a normalized trailing-slash S3 URL."""

    cleaned_folder_remote_path = str(folder_remote_path).rstrip("/")
    return remote_path_to_s3_url(cleaned_folder_remote_path) + "/"


def _build_analysis_csv_set(
    *,
    folder_relative_path: Path,
    output_dir: Path,
    remote_urls_by_name: Optional[dict[str, str]] = None,
    local_paths_by_name: Optional[dict[str, Path]] = None,
) -> AnalysisCSVSet:
    """Build one ``AnalysisCSVSet`` from grouped remote and/or local files."""

    remote_urls_by_name = remote_urls_by_name or {}
    local_paths_by_name = local_paths_by_name or {}

    folder_local_path = output_dir / folder_relative_path
    folder_s3_url: Optional[str] = None
    if remote_urls_by_name:
        folder_s3_url = _folder_remote_path_to_s3_url(
            f"{CPG0016_BUCKET}/{folder_relative_path.as_posix()}"
        )

    known_remote_urls = {
        filename: remote_urls_by_name[filename]
        for filename in ANALYSIS_PROFILE_FILENAMES
        if filename in remote_urls_by_name
    }
    known_local_paths = {
        filename: local_paths_by_name.get(filename, folder_local_path / filename)
        for filename in ANALYSIS_PROFILE_FILENAMES
        if filename in remote_urls_by_name or filename in local_paths_by_name
    }

    return AnalysisCSVSet(
        folder_relative_path=folder_relative_path,
        folder_local_path=folder_local_path,
        folder_s3_url=folder_s3_url,
        image_s3_url=known_remote_urls.get("Image.csv"),
        nuclei_s3_url=known_remote_urls.get("Nuclei.csv"),
        cells_s3_url=known_remote_urls.get("Cells.csv"),
        cytoplasm_s3_url=known_remote_urls.get("Cytoplasm.csv"),
        image_local_path=known_local_paths.get("Image.csv"),
        nuclei_local_path=known_local_paths.get("Nuclei.csv"),
        cells_local_path=known_local_paths.get("Cells.csv"),
        cytoplasm_local_path=known_local_paths.get("Cytoplasm.csv"),
        other_s3_urls={
            filename: url
            for filename, url in remote_urls_by_name.items()
            if filename not in ANALYSIS_PROFILE_FILENAMES
        },
        other_local_paths={
            filename: path
            for filename, path in local_paths_by_name.items()
            if filename not in ANALYSIS_PROFILE_FILENAMES
        },
    )


def normalize_analysis_csv_filenames(csv_names: Sequence[str] | None) -> tuple[str, ...]:
    """Normalize requested CSV names to canonical analysis profile filenames."""

    if csv_names is None:
        return ANALYSIS_PROFILE_FILENAMES

    normalized_names: list[str] = []
    invalid_names: list[str] = []
    for csv_name in csv_names:
        normalized_name = ANALYSIS_PROFILE_NAME_ALIASES.get(str(csv_name).strip().lower())
        if normalized_name is None:
            invalid_names.append(str(csv_name))
            continue
        if normalized_name not in normalized_names:
            normalized_names.append(normalized_name)

    if invalid_names:
        valid_names = ", ".join(ANALYSIS_PROFILE_FILENAMES)
        invalid_display = ", ".join(repr(name) for name in invalid_names)
        raise ValueError(
            f"Invalid analysis CSV names: {invalid_display}. Valid options are: {valid_names}"
        )

    return tuple(normalized_names)


def normalize_analysis_sources(sources: Sequence[str] | None) -> tuple[str, ...] | None:
    """Normalize requested source names to canonical dataset source segments."""

    if sources is None:
        return None

    normalized_sources: list[str] = []
    invalid_sources: list[str] = []
    for source in sources:
        normalized_source = str(source).strip()
        if not SOURCE_NAME_PATTERN.fullmatch(normalized_source):
            invalid_sources.append(str(source))
            continue
        if normalized_source not in normalized_sources:
            normalized_sources.append(normalized_source)

    if invalid_sources:
        invalid_display = ", ".join(repr(source) for source in invalid_sources)
        raise ValueError(
            "Invalid analysis sources: "
            f"{invalid_display}. Sources must match the dataset path segment format, for example 'source_10'."
        )

    return tuple(normalized_sources)


def build_analysis_csv_sets_from_s3_urls(
    s3_urls: list[str],
    *,
    output_dir: Path,
) -> list[AnalysisCSVSet]:
    """Group discovered analysis CSV URLs by their containing folder.

    Parameters
    ----------
    s3_urls
        Fully qualified analysis CSV S3 URLs.
    output_dir
        Local root directory where the downloader mirrors the dataset tree.

    Returns
    -------
    list[AnalysisCSVSet]
        One grouped record per folder containing discovered analysis CSV files.
    """

    grouped_urls: dict[Path, dict[str, str]] = {}
    for s3_url in s3_urls:
        relative_path = s3_url_to_relative_local_path(s3_url)
        folder_relative_path = relative_path.parent
        grouped_urls.setdefault(folder_relative_path, {})[relative_path.name] = s3_url

    csv_sets = [
        _build_analysis_csv_set(
            folder_relative_path=folder_relative_path,
            output_dir=output_dir,
            remote_urls_by_name=urls_by_name,
        )
        for folder_relative_path, urls_by_name in sorted(grouped_urls.items())
    ]
    return csv_sets


def build_analysis_csv_sets_from_local_paths(
    local_paths: list[Path],
    *,
    output_dir: Path,
) -> list[AnalysisCSVSet]:
    """Group local analysis CSV files by their containing folder.

    Parameters
    ----------
    local_paths
        Local CSV files already downloaded under ``output_dir``.
    output_dir
        Local root directory where the dataset tree is stored.

    Returns
    -------
    list[AnalysisCSVSet]
        One grouped record per local analysis folder.
    """

    grouped_paths: dict[Path, dict[str, Path]] = {}
    for local_path in local_paths:
        folder_relative_path = local_path.relative_to(output_dir).parent
        grouped_paths.setdefault(folder_relative_path, {})[local_path.name] = local_path

    csv_sets = [
        _build_analysis_csv_set(
            folder_relative_path=folder_relative_path,
            output_dir=output_dir,
            local_paths_by_name=paths_by_name,
        )
        for folder_relative_path, paths_by_name in sorted(grouped_paths.items())
    ]
    return csv_sets


def _build_analysis_csv_include_patterns(
    csv_filenames: Sequence[str],
    sources: Sequence[str] | None = None,
) -> list[str]:
    """Build AWS CLI include patterns for the requested analysis CSV filenames."""

    source_patterns = tuple(sources) if sources is not None else ("source_*",)
    return [
        f"{source_pattern}/workspace/analysis/**/{filename}"
        for source_pattern in source_patterns
        for filename in csv_filenames
    ]


def _create_aws_cli_config(max_concurrent_requests: int) -> str:
    """Create a temporary AWS CLI config file that sets S3 concurrency."""

    temp_dir = tempfile.mkdtemp(prefix="jump-analysis-aws-")
    config_path = Path(temp_dir) / "config"
    config_path.write_text(
        "\n".join(
            [
                "[default]",
                "s3 =",
                f"    max_concurrent_requests = {max_concurrent_requests}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return str(config_path)


class CPG0016AnalysisCSVDownloader:
    """Discover, iterate, and download CPG0016 analysis CSV folders.

    Parameters
    ----------
    output_dir
        Local root directory used to store the downloaded analysis CSV tree.
    workers
        Number of worker threads used when ``parallel`` is ``True``.
    parallel
        If ``True``, download files concurrently.
    verbose
        If ``True``, surface progress and failures from the shared downloader.
    use_existing_csvs_without_s3_check
        If ``True``, do not query S3 at all. Instead, inspect ``output_dir`` and
        build grouped CSV records from already-downloaded files.
    max_concurrent_requests
        Maximum number of concurrent S3 requests to allow when the AWS CLI bulk
        transfer downloads analysis CSVs. This only affects
        ``download_all_csv_profiles`` and defaults to a higher-throughput value
        than the AWS CLI default.
    """

    def __init__(
        self,
        output_dir: Path | str,
        *,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
        use_existing_csvs_without_s3_check: bool = False,
        max_concurrent_requests: int = AWS_DEFAULT_MAX_CONCURRENT_REQUESTS,
    ) -> None:
        """Initialize the analysis CSV downloader in S3 or local-only mode."""

        if output_dir is None:
            raise ValueError("output_dir must be provided")
        if max_concurrent_requests < 1:
            raise ValueError("max_concurrent_requests must be >= 1")

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workers = workers
        self.parallel = parallel
        self.verbose = verbose
        self.use_existing_csvs_without_s3_check = use_existing_csvs_without_s3_check
        self.max_concurrent_requests = max_concurrent_requests

        if self.use_existing_csvs_without_s3_check:
            self.analysis_csv_urls: list[str] = []
        else:
            self.analysis_csv_urls = []

        self.analysis_csv_sets = self.discover_local_analysis_csv_sets()

    def discover_analysis_csv_urls(self) -> list[str]:
        """Discover public CPG0016 analysis CSV URLs while excluding ``source_all``.

        Returns
        -------
        list[str]
            Fully qualified S3 URLs for analysis CSV files under the recursive
            ``workspace/analysis`` tree.
        """

        # Full analysis CSV discovery is large and can take multiple days in practice.
        fs = s3fs.S3FileSystem(anon=True)
        remote_paths = sorted(fs.glob(ANALYSIS_CSV_GLOB_PATTERN))
        analysis_csv_urls: list[str] = []
        for remote_path in remote_paths:
            if is_source_all_path(remote_path):
                continue
            analysis_csv_urls.append(remote_path_to_s3_url(remote_path))
        return analysis_csv_urls

    def discover_local_analysis_csv_sets(self) -> list[AnalysisCSVSet]:
        """Discover previously downloaded local analysis CSV groups.

        Returns
        -------
        list[AnalysisCSVSet]
            Grouped records built from local CSV files already present under
            ``output_dir``.
        """

        local_paths = self._discover_local_analysis_csv_paths()
        return build_analysis_csv_sets_from_local_paths(local_paths, output_dir=self.output_dir)

    def get_analysis_csv_sets(self) -> list[AnalysisCSVSet]:
        """Return copies of the grouped analysis CSV records.

        Returns
        -------
        list[AnalysisCSVSet]
            Defensive copies of the currently known grouped analysis folders.
        """

        return [_copy_analysis_csv_set(csv_set) for csv_set in self.analysis_csv_sets]

    def iter_analysis_csv_sets(self) -> Iterator[AnalysisCSVSet]:
        """Yield grouped analysis CSV records one folder at a time.

        Yields
        ------
        AnalysisCSVSet
            One defensive copy per grouped analysis folder.
        """

        for csv_set in self.analysis_csv_sets:
            yield _copy_analysis_csv_set(csv_set)

    def download_all_csv_profiles(
        self,
        csv_names: Sequence[str] | None = None,
        sources: Sequence[str] | None = None,
    ) -> DownloadSummary:
        """Download analysis CSV files into ``output_dir`` with the AWS CLI.

        This method uses the AWS CLI transfer manager against the public
        ``cellpainting-gallery`` bucket, then refreshes the grouped local CSV
        records from disk. When ``use_existing_csvs_without_s3_check`` is
        enabled, this method performs no S3 work and returns an empty summary.

        Parameters
        ----------
        csv_names
            Optional subset of analysis CSV names to download. Accepts shorthand
            names such as ``image`` or ``nuclei`` as well as full filenames such
            as ``Image.csv`` and ``Nuclei.csv``. When omitted, all standard
            profile CSVs are downloaded.
        sources
            Optional subset of dataset source path segments to download, for
            example ``["source_10", "source_11"]``. When omitted, all
            non-``source_all`` sources are eligible.

        Returns
        -------
        DownloadSummary
            Aggregate counts for the attempted download run, derived from the
            local mirror after a successful AWS CLI transfer.
        """

        if self.use_existing_csvs_without_s3_check:
            return DownloadSummary(total_jobs=0, downloaded=0, skipped=0, failed=0, failures=[])

        requested_filenames = tuple(normalize_analysis_csv_filenames(csv_names))
        requested_sources = normalize_analysis_sources(sources)
        requested_filename_set = set(requested_filenames)
        requested_source_set = set(requested_sources) if requested_sources is not None else None
        preexisting_paths = self._discover_local_analysis_csv_paths(
            requested_filenames=requested_filename_set,
            requested_sources=requested_source_set,
        )
        self._run_aws_analysis_csv_download(requested_filenames, requested_sources)
        post_download_paths = self._discover_local_analysis_csv_paths(
            requested_filenames=requested_filename_set,
            requested_sources=requested_source_set,
        )

        self.analysis_csv_urls = [self._local_analysis_csv_path_to_s3_url(path) for path in post_download_paths]
        self.analysis_csv_sets = build_analysis_csv_sets_from_s3_urls(
            self.analysis_csv_urls,
            output_dir=self.output_dir,
        )

        preexisting_path_set = set(preexisting_paths)
        downloaded = sum(1 for path in post_download_paths if path not in preexisting_path_set)
        skipped = len(post_download_paths) - downloaded
        return DownloadSummary(
            total_jobs=len(post_download_paths),
            downloaded=downloaded,
            skipped=skipped,
            failed=0,
            failures=[],
        )

    def _discover_local_analysis_csv_paths(
        self,
        requested_filenames: Optional[set[str]] = None,
        requested_sources: Optional[set[str]] = None,
    ) -> list[Path]:
        """Discover local analysis CSV files under ``output_dir``."""

        analysis_root = self.output_dir / CPG0016_PREFIX
        if not analysis_root.exists():
            return []

        local_paths = [
            local_path
            for local_path in sorted(analysis_root.rglob("workspace/analysis/**/*.csv"))
            if not is_source_all_path(local_path.relative_to(self.output_dir).as_posix())
            and (requested_filenames is None or local_path.name in requested_filenames)
            and (
                requested_sources is None
                or extract_metadata_source_from_dataset_path(local_path.relative_to(self.output_dir))
                in requested_sources
            )
        ]
        return local_paths

    def _local_analysis_csv_path_to_s3_url(self, local_path: Path) -> str:
        """Convert a local analysis CSV path under ``output_dir`` back into its S3 URL."""

        relative_path = local_path.relative_to(self.output_dir).as_posix().lstrip("/")
        return f"s3://{CPG0016_BUCKET}/{relative_path}"

    def _run_aws_analysis_csv_download(
        self,
        requested_filenames: Sequence[str],
        requested_sources: Sequence[str] | None,
    ) -> None:
        """Download the requested analysis CSVs with the AWS CLI transfer manager."""

        aws_path = shutil.which("aws")
        if aws_path is None:
            raise RuntimeError(
                "AWS CLI is required to download CPG0016 analysis CSVs. Install `aws` and retry."
            )

        destination_root = self.output_dir / CPG0016_PREFIX
        destination_root.mkdir(parents=True, exist_ok=True)

        command = [
            aws_path,
            "s3",
            "cp",
            f"s3://{CPG0016_BUCKET}/{CPG0016_PREFIX}/",
            str(destination_root),
            "--recursive",
            "--exclude",
            "*",
            "--exclude",
            "source_all/*",
        ]
        for include_pattern in _build_analysis_csv_include_patterns(
            requested_filenames,
            requested_sources,
        ):
            command.extend(["--include", include_pattern])
        command.extend(["--no-sign-request", "--only-show-errors"])

        effective_max_concurrent_requests = 1 if not self.parallel else self.max_concurrent_requests
        config_path = _create_aws_cli_config(effective_max_concurrent_requests)
        config_dir = Path(config_path).parent
        env = os.environ.copy()
        env["AWS_CONFIG_FILE"] = config_path
        env.setdefault("AWS_PAGER", "")

        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=not self.verbose,
                text=True,
                env=env,
            )
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            details = stderr or stdout or f"aws exited with status {result.returncode}"
            raise RuntimeError(f"AWS CLI analysis CSV download failed: {details}")
