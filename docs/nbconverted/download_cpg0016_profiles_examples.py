#!/usr/bin/env python
# coding: utf-8

# # Download and iterate through CPG0016 profiles CSV folders

from pathlib import Path

from jump_image_datasets.cpg0016 import CPG0016AnalysisCSVDownloader


# Bulk profile CSV downloads use the AWS CLI transfer manager, so make sure
# `aws` is installed and available on your PATH before running this example.

# Download all analysis CSVs from S3 into an organized local folder structure
# rooted at ``downloaded_profiles_csvs``. Files appear in that folder as the AWS
# CLI transfers them, even though normal per-file transfer logs stay quiet.
downloader = CPG0016AnalysisCSVDownloader(
    output_dir=Path("downloaded_profiles_csvs"),
    parallel=True,
    max_concurrent_requests=50,
)

# Omit ``csv_names`` to download all standard profile CSVs.
summary = downloader.download_all_csv_profiles()
print(summary)


# Download only a chosen subset when you do not need every profile table.
subset_summary = downloader.download_all_csv_profiles(csv_names=["nuclei", "image"])
print(subset_summary)


# Limit downloads to specific dataset sources. Sources must use full names such
# as ``source_10``.
source_subset_summary = downloader.download_all_csv_profiles(
    csv_names=["nuclei"],
    sources=["source_10"],
)
print(source_subset_summary)


# Combine CSV-name and source filters when you only need a few profile tables
# from a few sources.
multi_source_summary = downloader.download_all_csv_profiles(
    csv_names=["image", "nuclei"],
    sources=["source_10", "source_11"],
)
print(multi_source_summary)


# Reuse the existing local CSV tree later without checking S3 or probing which
# files already exist remotely.
local_only_downloader = CPG0016AnalysisCSVDownloader(
    output_dir=Path("downloaded_profiles_csvs"),
    use_existing_csvs_without_s3_check=True,
    parallel=True,
)


# Iterate through one analysis folder at a time and choose your own operations.
for csv_set in local_only_downloader.iter_analysis_csv_sets():
    print(csv_set.folder_local_path)
    print(csv_set.image_local_path)
    print(csv_set.nuclei_local_path)
    break
