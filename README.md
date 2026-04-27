# jump-image-datasets

`jump-image-datasets` provides packaged JUMP pilot metadata and utilities for downloading image files from metadata tables.

## Install

### Local development with uv

```bash
uv venv
uv sync --group test
```

### Editable install

```bash
uv pip install -e .
```

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
