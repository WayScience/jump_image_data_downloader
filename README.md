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

```python
from jump_image_datasets.cpg0016 import CPG0016LoadDataWithIllumDownloader

downloader = CPG0016LoadDataWithIllumDownloader(
    csv_download_dir="downloaded_cpg0016_csvs",
)

metadata_df = downloader.get_dataframe()

filtered_df = metadata_df.iloc[:10].copy()
filtered_df["OutputDir"] = (
    filtered_df["Metadata_Plate"].astype(str).radd("downloaded_cpg0016_images/")
)

downloader.download_illumination_files(
    dataframe=filtered_df,
    output_dir_column="OutputDir",
)

downloader.download_files_from_column(
    dataframe=filtered_df,
    column_name="URL_OrigDNA",
    output_dir_column="OutputDir",
)
```

```python
from jump_image_datasets.cpg0016 import CPG0016AnalysisCSVDownloader

# Discover all CPG0016 analysis CSVs from S3 and download them into an
# organized local directory tree.
downloader = CPG0016AnalysisCSVDownloader(
    output_dir="downloaded_cpg0016_profiles",
    parallel=True,
    workers=8,
)

# Omit csv_names to download all standard profile CSVs.
summary = downloader.download_all_csv_profiles()
print(summary)

# Or download only a selected subset.
nuclei_and_image_summary = downloader.download_all_csv_profiles(
    csv_names=["nuclei", "image"],
)
print(nuclei_and_image_summary)

# Later, reuse the existing local CSV tree without checking S3.
local_only_downloader = CPG0016AnalysisCSVDownloader(
    output_dir="downloaded_cpg0016_profiles",
    use_existing_csvs_without_s3_check=True,
)

# Iterate through one analysis folder at a time and choose your own operations,
# such as reading Image.csv and Nuclei.csv and merging them on ImageNumber.
for csv_set in local_only_downloader.iter_analysis_csv_sets():
    print(csv_set.folder_local_path)
    print(csv_set.image_local_path)
    print(csv_set.nuclei_local_path)
    break
```

For full runnable examples, see `docs/download_images_examples.ipynb`, `docs/download_cpg0016_examples.ipynb`, and `docs/download_cpg0016_profiles_examples.ipynb`.

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
