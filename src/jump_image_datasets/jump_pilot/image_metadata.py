from __future__ import annotations

from pathlib import Path
import tempfile

from importlib import resources

import pandas as pd


METADATA_FILENAME = "2020_11_04_CPJUMP1_all_plates.parquet"
_METADATA_RESOURCE_RELATIVE_PATH = Path("data") / METADATA_FILENAME
_CACHED_RESOURCE_PATH: Path | None = None
_METADATA_DF_CACHE: pd.DataFrame | None = None


def get_metadata_path() -> Path:
    """Return a local filesystem path to the packaged metadata parquet file."""

    global _CACHED_RESOURCE_PATH

    if _CACHED_RESOURCE_PATH is not None and _CACHED_RESOURCE_PATH.exists():
        return _CACHED_RESOURCE_PATH

    resource = resources.files("jump_image_datasets.jump_pilot").joinpath(
        str(_METADATA_RESOURCE_RELATIVE_PATH)
    )

    if hasattr(resource, "is_file") and resource.is_file() and hasattr(resource, "path"):
        _CACHED_RESOURCE_PATH = Path(resource.path)
        return _CACHED_RESOURCE_PATH

    if hasattr(resource, "is_file") and resource.is_file():
        try:
            _CACHED_RESOURCE_PATH = Path(resource)
            if _CACHED_RESOURCE_PATH.exists():
                return _CACHED_RESOURCE_PATH
        except TypeError:
            pass

    cache_dir = Path(tempfile.gettempdir()) / "jump_image_datasets" / "jump_pilot"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_file = cache_dir / METADATA_FILENAME
    if not cached_file.exists():
        cached_file.write_bytes(resource.read_bytes())

    _CACHED_RESOURCE_PATH = cached_file
    return _CACHED_RESOURCE_PATH


def clear_metadata_cache() -> None:
    """Clear the in-memory DataFrame cache used by :func:`load_metadata`."""

    global _METADATA_DF_CACHE
    _METADATA_DF_CACHE = None


def load_metadata(
    *,
    use_cache: bool = True,
    copy_dataframe: bool = False,
) -> pd.DataFrame:
    """Load the packaged metadata parquet into a pandas DataFrame.

    Parameters
    ----------
    use_cache
        If ``True``, return a cached DataFrame on repeated calls and avoid
        re-reading parquet from disk.
    copy_dataframe
        If ``True``, return a copy of the DataFrame. This is useful when callers
        plan to mutate the returned DataFrame.
    """

    global _METADATA_DF_CACHE

    if use_cache and _METADATA_DF_CACHE is not None:
        return _METADATA_DF_CACHE.copy(deep=True) if copy_dataframe else _METADATA_DF_CACHE

    dataframe = pd.read_parquet(get_metadata_path())

    if use_cache:
        _METADATA_DF_CACHE = dataframe

    if copy_dataframe:
        return dataframe.copy(deep=True)
    return dataframe
