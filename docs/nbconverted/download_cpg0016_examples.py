#!/usr/bin/env python
# coding: utf-8

# # Download CPG0016 metadata and referenced files

import sys
from pathlib import Path

from jump_image_datasets.cpg0016 import CPG0016LoadDataWithIllumDownloader


# Download all load_data_with_illum.csv files for cpg0016, excluding source_all.
# This can take a while even though the total size is only ~15 GB because it pulls
# ~2.5k separate S3 CSV objects; per-file request latency, public-s3fs overhead,
# Python copy/write overhead, and later CSV parsing dominate more than raw bandwidth.
# The total time for this process was 2 hours, 30 minutes with 8 cores in parallel with the S3 check.
# The time to download the csvs only took 10 to 15 minutes
# If you already downloaded the csvs, then you can disable the check.
downloader = CPG0016LoadDataWithIllumDownloader(
    csv_download_dir=Path("downloaded_cpg0016_csvs"),
    parallel=True,
    workers=8,
    use_existing_csvs_without_s3_check=True,
)
print(downloader.csv_download_summary)


# Load and concatenate the downloaded metadata tables.
metadata_df = downloader.get_dataframe()
print(f"Metadata shape: {metadata_df.shape}")
metadata_df[["Metadata_Source", "Metadata_Batch", "Metadata_Plate"]].head()

# Filter metadata explicitly before downloading.
filtered_metadata_df = metadata_df.iloc[:10].copy()
filtered_metadata_df["OutputDir"] = (
    filtered_metadata_df["Metadata_Plate"].astype(str).radd("temp_dir/")
)
print(f"Filtered metadata shape: {filtered_metadata_df.shape}")

# Download one exact S3-path column using an explicit dataframe and output directory column.
orig_dna_summary = downloader.download_files_from_column(
    dataframe=filtered_metadata_df,
    column_name="URL_OrigDNA",
    output_dir_column="OutputDir",
    parallel=True,
    workers=8,
)
print(orig_dna_summary)


# Download all illumination .npy files across the standard illumination columns.
illum_summary = downloader.download_illumination_files(
    dataframe=filtered_metadata_df,
    output_dir_column="OutputDir",
    parallel=True,
    workers=8,
)
print(illum_summary)


# Download all original image .tif files across the standard image columns.
image_summary = downloader.download_image_files(
    dataframe=filtered_metadata_df,
    output_dir_column="OutputDir",
    parallel=True,
    workers=8,
)
print(image_summary)
