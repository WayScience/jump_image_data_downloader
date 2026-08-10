"""Utilities for downloading and accessing CPG0016 metadata and files.

This subpackage exposes two complementary download workflows:

- ``CPG0016LoadDataWithIllumDownloader`` for per-plate metadata tables and the
  image or illumination files referenced by those tables.
- ``CPG0016AnalysisCSVDownloader`` for grouped single-cell analysis CSV folders
  such as ``Image.csv``, ``Nuclei.csv``, ``Cells.csv``, and ``Cytoplasm.csv``.
"""

from jump_image_datasets.cpg0016.load_data_with_illum_downloader import (
    CPG0016LoadDataWithIllumDownloader,
    LOAD_DATA_WITH_ILLUM_CSV_GLOB_PATTERN,
    CPG0016_BUCKET,
    CPG0016_PREFIX,
    DownloadJob,
    DownloadSummary,
    ILLUMINATION_COLUMNS,
    IMAGE_COLUMNS,
)
from jump_image_datasets.cpg0016.analysis_csv_downloader import (
    ANALYSIS_CSV_GLOB_PATTERN,
    ANALYSIS_PROFILE_FILENAMES,
    AnalysisCSVSet,
    CPG0016AnalysisCSVDownloader,
)

__all__ = [
    "CPG0016LoadDataWithIllumDownloader",
    "LOAD_DATA_WITH_ILLUM_CSV_GLOB_PATTERN",
    "CPG0016_BUCKET",
    "CPG0016_PREFIX",
    "DownloadJob",
    "DownloadSummary",
    "ILLUMINATION_COLUMNS",
    "IMAGE_COLUMNS",
    "CPG0016AnalysisCSVDownloader",
    "ANALYSIS_CSV_GLOB_PATTERN",
    "ANALYSIS_PROFILE_FILENAMES",
    "AnalysisCSVSet",
]
