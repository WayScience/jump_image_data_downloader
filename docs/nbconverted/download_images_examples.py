#!/usr/bin/env python
# coding: utf-8

# # Download pilot images from packaged metadata

from pathlib import Path

from jump_image_datasets.jump_pilot import image_downloader, image_metadata


# Load packaged metadata parquet.
img_metadf = image_metadata.load_metadata(use_cache=True)
print(f"Metadata shape: {img_metadf.shape}")


# Optionally filter rows to limit download volume for examples.
img_metadf = img_metadf.iloc[:10].copy()


# Download files using the URL column from metadata.
summary = image_downloader.download_images_with_metadata(
    df=img_metadf,
    url_column="Metadata_FileUrl",
    default_output_dir=Path("downloaded_jump_pilot_images"),
    parallel=True,
    workers=8,
)
print(summary)
