"""Discover and download CPG0016 single-cell analysis CSV files.

This module indexes the public ``workspace/analysis`` tree for
``cellpainting-gallery/cpg0016-jump`` and groups discovered CSVs by their
containing analysis folder. Each grouped record exposes the common CellProfiler
profile files such as ``Image.csv``, ``Nuclei.csv``, ``Cells.csv``, and
``Cytoplasm.csv`` while preserving the S3-relative local download layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import s3fs

from jump_image_datasets.cpg0016.load_data_with_illum_downloader import (
    CPG0016_BUCKET,
    CPG0016_PREFIX,
    DownloadJob,
    DownloadSummary,
    is_source_all_path,
    remote_path_to_s3_url,
    run_download_jobs,
    s3_url_to_relative_local_path,
)


ANALYSIS_CSV_GLOB_PATTERN = f"{CPG0016_BUCKET}/{CPG0016_PREFIX}/*/workspace/analysis/**/*.csv"
ANALYSIS_PROFILE_FILENAMES = (
    "Image.csv",
    "Nuclei.csv",
    "Cells.csv",
    "Cytoplasm.csv",
)


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
    """

    def __init__(
        self,
        output_dir: Path | str,
        *,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
        use_existing_csvs_without_s3_check: bool = False,
    ) -> None:
        """Initialize the analysis CSV downloader in S3 or local-only mode."""

        if output_dir is None:
            raise ValueError("output_dir must be provided")

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.workers = workers
        self.parallel = parallel
        self.verbose = verbose
        self.use_existing_csvs_without_s3_check = use_existing_csvs_without_s3_check

        if self.use_existing_csvs_without_s3_check:
            self.analysis_csv_urls: list[str] = []
            self.analysis_csv_sets = self.discover_local_analysis_csv_sets()
        else:
            self.analysis_csv_urls = self.discover_analysis_csv_urls()
            self.analysis_csv_sets = build_analysis_csv_sets_from_s3_urls(
                self.analysis_csv_urls,
                output_dir=self.output_dir,
            )

    def discover_analysis_csv_urls(self) -> list[str]:
        """Discover public CPG0016 analysis CSV URLs while excluding ``source_all``.

        Returns
        -------
        list[str]
            Fully qualified S3 URLs for analysis CSV files under the recursive
            ``workspace/analysis`` tree.
        """

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

        dataset_root = self.output_dir / CPG0016_PREFIX
        if not dataset_root.exists():
            return []

        local_paths = [
            local_path
            for local_path in sorted(dataset_root.rglob("*.csv"))
            if not is_source_all_path(local_path.relative_to(self.output_dir).as_posix())
        ]
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

    def download_all_csv_profiles(self) -> DownloadSummary:
        """Download all discovered analysis CSV files into ``output_dir``.

        Existing local files are skipped without being re-downloaded. When
        ``use_existing_csvs_without_s3_check`` is enabled, this method performs
        no S3 work and returns an empty summary.

        Returns
        -------
        DownloadSummary
            Aggregate counts for the attempted download run.
        """

        if self.use_existing_csvs_without_s3_check:
            return DownloadSummary(total_jobs=0, downloaded=0, skipped=0, failed=0, failures=[])

        jobs = self._build_download_jobs(self.analysis_csv_urls)
        summary = run_download_jobs(
            jobs,
            overwrite=False,
            workers=self.workers,
            parallel=self.parallel,
            verbose=self.verbose,
        )
        return summary

    def _build_download_jobs(self, s3_urls: list[str]) -> list[DownloadJob]:
        """Build unique download jobs while detecting local-path collisions."""

        jobs: list[DownloadJob] = []
        planned_paths: dict[Path, str] = {}

        for s3_url in s3_urls:
            relative_path = s3_url_to_relative_local_path(s3_url)
            local_path = self.output_dir / relative_path
            existing_s3_url = planned_paths.get(local_path)
            if existing_s3_url is None:
                planned_paths[local_path] = s3_url
                jobs.append(DownloadJob(s3_url=s3_url, local_path=local_path))
                continue

            if existing_s3_url != s3_url:
                raise ValueError(
                    f"Conflicting S3 URLs for local path {local_path}: "
                    f"{existing_s3_url!r} and {s3_url!r}"
                )

        return jobs
