from pathlib import Path
import os


def resolve_db_root() -> str:
    env_root = os.getenv("SATELLITE_DB_ROOT")
    if env_root:
        return env_root
    return "/tmp/satellite_cache"


def resolve_dataset_root() -> str:
    env_root = os.getenv("SATELLITE_DATA_ROOT")
    if env_root:
        return env_root

    project_root = Path(__file__).resolve().parents[2]
    return str(project_root / "demo_data")


def resolve_dataset_path() -> str:
    env_path = os.getenv("SATELLITE_DATA_PATH")
    if env_path:
        return env_path

    root = Path(resolve_dataset_root())
    fallback_dataset = root / "demo_constellation"
    if fallback_dataset.exists():
        return str(fallback_dataset)

    return str(root)


DATASET_ROOT = resolve_dataset_root()
DEFAULT_DATASET_DIR = resolve_dataset_path()
DB_CACHE_ROOT = resolve_db_root()
