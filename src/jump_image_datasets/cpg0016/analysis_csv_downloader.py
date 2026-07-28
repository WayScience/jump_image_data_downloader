"""Discover and download CPG0016 single-cell analysis CSV files.

This module indexes the public ``workspace/analysis`` tree for
``cellpainting-gallery/cpg0016-jump`` and builds a manifest dataframe with one
row per ``source/batch/plate/well/site`` combination. The manifest stores the
three analysis CSV S3 paths used by CellProfiler outputs: ``Cells.csv``,
``Cytoplasm.csv``, and ``Nuclei.csv``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
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
    validate_column,
)


ANALYSIS_CSV_GLOB_PATTERN = (
    f"{CPG0016_BUCKET}/{CPG0016_PREFIX}/source_*/workspace/analysis/*/*/analysis/*/*.csv"
)
ANALYSIS_FILE_TO_COLUMN = {
    "Nuclei.csv": "Nuclei_S3_Path",
    "Cells.csv": "Cells_S3_Path",
    "Cytoplasm.csv": "Cytoplasm_S3_Path",
}
ANALYSIS_CSV_COLUMNS = ["Nuclei_S3_Path", "Cells_S3_Path", "Cytoplasm_S3_Path"]
ANALYSIS_MANIFEST_COLUMNS = [
    "Metadata_Plate",
    "Metadata_Well",
    "Metadata_Site",
    "Metadata_Batch",
    "Metadata_Source",
    *ANALYSIS_CSV_COLUMNS,
]


def parse_analysis_csv_s3_url(s3_url: str) -> dict[str, str]:
    """Parse one analysis CSV S3 URL into manifest metadata fields.

    Parameters
    ----------
    s3_url
        Analysis CSV URL in ``s3://bucket/key`` form.

    Returns
    -------
    dict[str, str]
        Parsed metadata and target manifest column for the analysis CSV.

    Raises
    ------
    ValueError
        If the path does not match the expected CPG0016 analysis layout.
    """

    relative_path = s3_url_to_relative_local_path(s3_url)
    parts = relative_path.parts
    if len(parts) != 9:
        raise ValueError(f"Invalid analysis CSV S3 URL: {s3_url}")

    dataset_prefix, source, workspace, analysis_dir, batch, plate, analysis_leaf, plate_well_site, filename = parts
    if dataset_prefix != CPG0016_PREFIX:
        raise ValueError(f"Invalid analysis CSV S3 URL: {s3_url}")
    if workspace != "workspace" or analysis_dir != "analysis" or analysis_leaf != "analysis":
        raise ValueError(f"Invalid analysis CSV S3 URL: {s3_url}")

    manifest_column = ANALYSIS_FILE_TO_COLUMN.get(filename)
    if manifest_column is None:
        raise ValueError(f"Invalid analysis CSV filename in S3 URL: {s3_url}")

    plate_well_site_parts = plate_well_site.split("-")
    if len(plate_well_site_parts) < 3:
        raise ValueError(f"Invalid analysis CSV S3 URL: {s3_url}")

    parsed_plate = "-".join(plate_well_site_parts[:-2])
    well = plate_well_site_parts[-2]
    site = plate_well_site_parts[-1]
    if parsed_plate != plate or not well or not site:
        raise ValueError(f"Invalid analysis CSV S3 URL: {s3_url}")

    return {
        "Metadata_Plate": plate,
        "Metadata_Well": well,
        "Metadata_Site": site,
        "Metadata_Batch": batch,
        "Metadata_Source": source,
        "manifest_column": manifest_column,
    }


def build_analysis_manifest_dataframe(s3_urls: list[str]) -> pd.DataFrame:
    """Build the analysis manifest dataframe from discovered S3 URLs.

    Parameters
    ----------
    s3_urls
        Analysis CSV URLs to index.

    Returns
    -------
    pandas.DataFrame
        Manifest dataframe with one row per ``source/batch/plate/well/site``.
    """

    rows_by_key: dict[tuple[str, str, str, str, str], dict[str, object]] = {}

    # Group the three CSV types onto a single manifest row while keeping rows
    # even when one or two of the expected analysis CSVs are missing.
    for s3_url in s3_urls:
        parsed = parse_analysis_csv_s3_url(s3_url)
        key = (
            parsed["Metadata_Source"],
            parsed["Metadata_Batch"],
            parsed["Metadata_Plate"],
            parsed["Metadata_Well"],
            parsed["Metadata_Site"],
        )
        row = rows_by_key.setdefault(
            key,
            {
                "Metadata_Plate": parsed["Metadata_Plate"],
                "Metadata_Well": parsed["Metadata_Well"],
                "Metadata_Site": parsed["Metadata_Site"],
                "Metadata_Batch": parsed["Metadata_Batch"],
                "Metadata_Source": parsed["Metadata_Source"],
                "Nuclei_S3_Path": pd.NA,
                "Cells_S3_Path": pd.NA,
                "Cytoplasm_S3_Path": pd.NA,
            },
        )
        row[parsed["manifest_column"]] = s3_url

    if not rows_by_key:
        return pd.DataFrame(columns=ANALYSIS_MANIFEST_COLUMNS)

    dataframe = pd.DataFrame(rows_by_key.values(), columns=ANALYSIS_MANIFEST_COLUMNS)
    return dataframe.sort_values(
        by=[
            "Metadata_Source",
            "Metadata_Batch",
            "Metadata_Plate",
            "Metadata_Well",
            "Metadata_Site",
        ],
        kind="stable",
    ).reset_index(drop=True)


class CPG0016AnalysisCSVDownloader:
    """Discover, save, load, and download CPG0016 analysis CSV path manifests.

    Parameters
    ----------
    manifest_download_dir
        Local directory used for manifest-related files and validation.
    manifest_csv_path
        Optional path where the manifest dataframe should be saved or loaded.
    overwrite
        If ``True``, re-download existing analysis CSV files.
    workers
        Number of worker threads to use for downloads when ``parallel`` is
        ``True``.
    parallel
        If ``True``, run downloads concurrently. If ``False``, run serially.
    verbose
        If ``True``, print failures and progress updates.
    use_existing_manifest_without_s3_check
        If ``True``, skip S3 discovery and load a previously saved manifest CSV
        from ``manifest_csv_path``.
    """

    def __init__(
        self,
        manifest_download_dir: Path | str,
        *,
        manifest_csv_path: Path | str | None = None,
        overwrite: bool = False,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
        use_existing_manifest_without_s3_check: bool = False,
    ) -> None:
        """Initialize the analysis manifest downloader.

        Raises
        ------
        ValueError
            If required constructor arguments are missing or invalid for the
            chosen mode.
        """

        if manifest_download_dir is None:
            raise ValueError("manifest_download_dir must be provided")

        self.manifest_download_dir = Path(manifest_download_dir)
        self.manifest_download_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_csv_path = Path(manifest_csv_path) if manifest_csv_path is not None else None
        self.overwrite = overwrite
        self.workers = workers
        self.parallel = parallel
        self.verbose = verbose
        self.use_existing_manifest_without_s3_check = use_existing_manifest_without_s3_check

        if self.use_existing_manifest_without_s3_check:
            if self.manifest_csv_path is None:
                raise ValueError(
                    "manifest_csv_path must be provided when "
                    "use_existing_manifest_without_s3_check=True"
                )
            if not self.manifest_csv_path.exists():
                raise ValueError(
                    "manifest_csv_path does not exist; disable "
                    "use_existing_manifest_without_s3_check to discover it from S3. "
                    f"Missing path: {self.manifest_csv_path}"
                )
            self.dataframe = self._load_manifest_csv(self.manifest_csv_path)
            self.analysis_csv_urls = self._dataframe_to_urls(self.dataframe)
        else:
            self.analysis_csv_urls = self.discover_analysis_csv_urls()
            self.dataframe = build_analysis_manifest_dataframe(self.analysis_csv_urls)
            if self.manifest_csv_path is not None:
                self.save_dataframe_csv(self.dataframe, self.manifest_csv_path)

    def discover_analysis_csv_urls(self) -> list[str]:
        """Discover public CPG0016 analysis CSV URLs while excluding ``source_all``.

        Returns
        -------
        list[str]
            Discovered analysis CSV S3 URLs.
        """

        fs = s3fs.S3FileSystem(anon=True)
        remote_paths = sorted(fs.glob(ANALYSIS_CSV_GLOB_PATTERN))
        analysis_csv_urls: list[str] = []
        for remote_path in remote_paths:
            if is_source_all_path(remote_path):
                continue
            filename = Path(remote_path).name
            if filename not in ANALYSIS_FILE_TO_COLUMN:
                continue
            analysis_csv_urls.append(remote_path_to_s3_url(remote_path))
        return analysis_csv_urls

    def get_dataframe(self) -> pd.DataFrame:
        """Return a copy of the current analysis manifest dataframe.

        Returns
        -------
        pandas.DataFrame
            Analysis manifest dataframe.
        """

        return self.dataframe.copy()

    def save_dataframe_csv(
        self,
        dataframe: pd.DataFrame,
        output_csv_path: Path | str,
    ) -> Path:
        """Save a manifest dataframe CSV to a caller-chosen location.

        Parameters
        ----------
        dataframe
            Manifest dataframe to save.
        output_csv_path
            CSV destination path.

        Returns
        -------
        pathlib.Path
            Saved CSV path.
        """

        output_path = Path(output_csv_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        dataframe.to_csv(output_path, index=False)
        return output_path

    def download_csvs_from_column(
        self,
        dataframe: pd.DataFrame,
        column_name: str,
        *,
        output_root: Optional[str | Path] = None,
        output_root_column: Optional[str] = None,
        overwrite: bool = False,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
    ) -> DownloadSummary:
        """Download analysis CSVs from one manifest S3-path column.

        Parameters
        ----------
        dataframe
            Manifest rows whose analysis CSVs should be downloaded.
        column_name
            Manifest S3-path column to download.
        output_root
            Output root directory to use for every row.
        output_root_column
            Column containing the per-row output root directory.
        overwrite
            If ``True``, replace existing local files.
        workers
            Number of worker threads to use when ``parallel`` is ``True``.
        parallel
            If ``True``, run downloads with a thread pool. If ``False``, run
            downloads serially.
        verbose
            If ``True``, print failures and progress updates.

        Returns
        -------
        DownloadSummary
            Aggregate counts and failure details for the run.
        """

        return self.download_csvs_from_columns(
            dataframe=dataframe,
            columns=[column_name],
            output_root=output_root,
            output_root_column=output_root_column,
            overwrite=overwrite,
            workers=workers,
            parallel=parallel,
            verbose=verbose,
        )

    def download_csvs_from_columns(
        self,
        dataframe: pd.DataFrame,
        *,
        columns: Optional[list[str]] = None,
        output_root: Optional[str | Path] = None,
        output_root_column: Optional[str] = None,
        overwrite: bool = False,
        workers: int = 4,
        parallel: bool = True,
        verbose: bool = True,
    ) -> DownloadSummary:
        """Download analysis CSVs from one or more manifest S3-path columns.

        Parameters
        ----------
        dataframe
            Manifest rows whose analysis CSVs should be downloaded.
        columns
            Optional subset of analysis path columns. Defaults to all three
            analysis CSV columns.
        output_root
            Output root directory to use for every row.
        output_root_column
            Column containing the per-row output root directory.
        overwrite
            If ``True``, replace existing local files.
        workers
            Number of worker threads to use when ``parallel`` is ``True``.
        parallel
            If ``True``, run downloads with a thread pool. If ``False``, run
            downloads serially.
        verbose
            If ``True``, print failures and progress updates.

        Returns
        -------
        DownloadSummary
            Aggregate counts and failure details for the run.
        """

        jobs = self._build_jobs_from_dataframe(
            dataframe=dataframe,
            columns=columns or ANALYSIS_CSV_COLUMNS,
            output_root=output_root,
            output_root_column=output_root_column,
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
        output_root: Optional[str | Path],
        output_root_column: Optional[str],
    ) -> list[DownloadJob]:
        """Build download jobs from a manifest dataframe.

        Validation is fail-fast and reports row indices for invalid values.
        Analysis CSV downloads preserve the dataset-relative S3 subdirectories
        under the caller-provided output root to avoid filename collisions.
        """

        missing_columns = [column for column in columns if column not in dataframe.columns]
        if missing_columns:
            raise ValueError(f"columns not found: {', '.join(missing_columns)}")
        if output_root is not None and output_root_column is not None:
            raise ValueError("Provide either output_root or output_root_column, not both")
        if output_root is None and output_root_column is None:
            raise ValueError("Provide output_root or output_root_column")
        if output_root_column is not None:
            validate_column(dataframe, output_root_column, "Output root")

        constant_output_root: Optional[Path] = None
        if output_root is not None:
            raw_output_root = str(output_root).strip()
            if not raw_output_root:
                raise ValueError(f"Invalid output root value: {output_root!r}")
            constant_output_root = Path(raw_output_root)

        jobs: list[DownloadJob] = []
        planned_paths: dict[Path, tuple[str, object, str]] = {}

        for row_index, row in dataframe.iterrows():
            if constant_output_root is not None:
                resolved_output_root = constant_output_root
            else:
                raw_output_root = row[output_root_column]
                if pd.isna(raw_output_root) or str(raw_output_root).strip() == "":
                    raise ValueError(
                        f"Invalid output root value at row index {row_index} "
                        f"for column {output_root_column}: {raw_output_root!r}"
                    )
                resolved_output_root = Path(str(raw_output_root).strip())

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
                    relative_path = s3_url_to_relative_local_path(s3_url)
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid S3 URL at row index {row_index} for column {column}: {raw_s3_url!r}"
                    ) from exc

                local_path = resolved_output_root / relative_path
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

    def _load_manifest_csv(self, manifest_csv_path: Path) -> pd.DataFrame:
        """Load a manifest CSV and normalize the required manifest columns."""

        dataframe = pd.read_csv(manifest_csv_path, dtype={"Metadata_Site": "string"})
        missing_columns = [column for column in ANALYSIS_MANIFEST_COLUMNS if column not in dataframe.columns]
        if missing_columns:
            raise ValueError(
                "manifest CSV is missing required columns: "
                f"{', '.join(missing_columns)}"
            )

        dataframe = dataframe.loc[:, ANALYSIS_MANIFEST_COLUMNS].copy()
        dataframe["Metadata_Site"] = dataframe["Metadata_Site"].astype("string")
        return dataframe

    def _dataframe_to_urls(self, dataframe: pd.DataFrame) -> list[str]:
        """Collect all non-missing analysis CSV URLs from a manifest dataframe."""

        urls: list[str] = []
        for column in ANALYSIS_CSV_COLUMNS:
            for value in dataframe[column].dropna().tolist():
                cleaned_value = str(value).strip()
                if cleaned_value:
                    urls.append(cleaned_value)
        return urls
