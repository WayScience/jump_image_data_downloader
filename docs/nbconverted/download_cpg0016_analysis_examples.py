#!/usr/bin/env python
# coding: utf-8

# # Discover and download CPG0016 analysis CSVs

from pathlib import Path

from jump_image_datasets.cpg0016 import CPG0016AnalysisCSVDownloader


# Build the analysis-path manifest from S3 and save it where you choose.
downloader = CPG0016AnalysisCSVDownloader(
    manifest_download_dir=Path("analysis_manifest_cache"),
    manifest_csv_path=Path("downloaded_cpg0016_analysis_paths.csv"),
    parallel=True,
    workers=8,
)
analysis_df = downloader.get_dataframe()
print(f"Analysis manifest shape: {analysis_df.shape}")
analysis_df.head()


# Reuse the previously saved manifest later without querying S3 again.
cached_downloader = CPG0016AnalysisCSVDownloader(
    manifest_download_dir=Path("analysis_manifest_cache"),
    manifest_csv_path=Path("downloaded_cpg0016_analysis_paths.csv"),
    use_existing_manifest_without_s3_check=True,
    parallel=True,
    workers=8,
)
cached_analysis_df = cached_downloader.get_dataframe().iloc[:10].copy()


# Download one analysis CSV type while preserving the S3-relative directory tree
# under the per-row output root to avoid filename collisions.
cached_analysis_df["OutputRoot"] = "downloaded_analysis_csvs"
nuclei_summary = cached_downloader.download_csvs_from_column(
    dataframe=cached_analysis_df,
    column_name="Nuclei_S3_Path",
    output_root_column="OutputRoot",
    parallel=True,
    workers=8,
)
print(nuclei_summary)


# Download all three analysis CSV types from the same filtered dataframe.
all_analysis_summary = cached_downloader.download_csvs_from_columns(
    dataframe=cached_analysis_df,
    output_root_column="OutputRoot",
    parallel=True,
    workers=8,
)
print(all_analysis_summary)
