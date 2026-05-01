# jump-image-datasets

[![Tests](https://github.com/WayScience/jump_image_data_downloader/actions/workflows/test.yml/badge.svg)](https://github.com/WayScience/jump_image_data_downloader/actions/workflows/test.yml)
[![PyPI version](https://img.shields.io/pypi/v/jump-image-datasets.svg)](https://pypi.org/project/jump-image-datasets/)
[![Publish to PyPI](https://github.com/WayScience/jump_image_data_downloader/actions/workflows/publish.yml/badge.svg)](https://github.com/WayScience/jump_image_data_downloader/actions/workflows/publish.yml)

`jump-image-datasets` provides packaged JUMP pilot metadata and utilities for downloading image files from metadata tables.

## Install

### Install from PyPI

```bash
pip install jump-image-datasets
```

Install from PyPI for stable, versioned releases.

### Local development with uv

```bash
uv venv
uv sync --group test
```

### Editable install

```bash
uv pip install -e .
```

### Install from the GitHub repo with pip

```bash
pip install "git+https://github.com/WayScience/jump_image_data_downloader.git"
```

Install from GitHub if you want the latest unreleased changes.

## Usage

```python
from jump_image_datasets.jump_pilot import image_downloader, image_metadata

# Load packaged metadata parquet as a DataFrame.
metadata_df = image_metadata.load_metadata()

# Download a small subset.
summary = image_downloader.download_images_with_metadata(
    df=metadata_df.head(10),
    url_column="Metadata_FileUrl",
    default_output_dir="downloaded_jump_pilot_images",
    parallel=True,
    workers=8,
)
print(summary)
```

For a full runnable example, see `docs/download_images_examples.ipynb`.

## Packaged metadata provenance

This repository ships a packaged metadata table at:

- `src/jump_image_datasets/jump_pilot/data/2020_11_04_CPJUMP1_all_plates.parquet`

### Why this file exists

The file is included so users can immediately load a stable JUMP pilot metadata table (via `jump_image_datasets.jump_pilot.image_metadata`) without requiring a separate data-fetch or preprocessing step.

### How it was created

This parquet was generated from the JUMP Cell Painting Gallery using:

- https://github.com/WayScience/JUMP-single-cell/blob/main/0.download_data/2.download_image_metadata.ipynb

Upstream source pattern used by that notebook:

- `s3://cellpainting-gallery/cpg0000-jump-pilot/source_4/workspace/load_data_csv/2020_11_04_CPJUMP1/*/load_data.csv`

### Transform summary

The generation workflow in `2.download_image_metadata.ipynb`:

- Lists all per-plate `load_data.csv` files for run `2020_11_04_CPJUMP1` (51 files in the captured run) from public S3 (`anon=True`).
- Reads each plate CSV, appends provenance columns:
  - `source_plate` (plate ID parsed from path)
  - `source_s3_path` (full S3 CSV path)
- Concatenates all plate tables into one DataFrame.
- Reshapes channel URL columns from wide to long using `melt`:
  - URL columns become `Metadata_ChannelURLName`
  - URL values become `Metadata_FileUrl`
- Adds normalized channel/stain annotations by mapping URL column names:
  - `Metadata_ChannelName`: `ER`, `AGP`, `Mito`, `DNA`, `RNA`, `BF`, `HZ_BF`, `LZ_BF`
  - `Metadata_StainName`: corresponding stain labels (or `NA` for brightfield channels)
- Derives `Metadata_Filename` from the final path component of `Metadata_FileUrl`.
- Writes parquet with `index=False` as `data/2020_11_04_CPJUMP1_all_plates.parquet` (captured shape: `(1495400, 32)`).
