#!/usr/bin/env python
# coding: utf-8

# # Download CPG0016 metadata and referenced files

import sys
from pathlib import Path

from jump_image_datasets.cpg0016 import CPG0016LoadDataWithIllumDownloader


# Download all load_data_with_illum.csv files for cpg0016, excluding source_all.
downloader = CPG0016LoadDataWithIllumDownloader(
    csv_download_dir=Path("downloaded_cpg0016_csvs"),
    parallel=True,
    workers=8,
)
print(downloader.csv_download_summary)


# Load and concatenate the downloaded metadata tables.
metadata_df = downloader.get_dataframe()
print(f"Metadata shape: {metadata_df.shape}")
metadata_df[["Metadata_Source", "Metadata_Batch", "Metadata_Plate"]].head()


# Download one exact S3-path column.
orig_dna_summary = downloader.download_files_from_column(
    column_name="URL_OrigDNA",
    download_dir=Path("downloaded_cpg0016_orig_dna"),
    parallel=True,
    workers=8,
)
print(orig_dna_summary)


# Download all illumination .npy files across the standard illumination columns.
illum_summary = downloader.download_illumination_files(
    download_dir=Path("downloaded_cpg0016_illum"),
    parallel=True,
    workers=8,
)
print(illum_summary)


# Download all original image .tif files across the standard image columns.
image_summary = downloader.download_image_files(
    download_dir=Path("downloaded_cpg0016_images"),
    parallel=True,
    workers=8,
)
print(image_summary)
