import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .loader import SatelliteDataset
from .settings import DATASET_ROOT, DEFAULT_DATASET_DIR, DB_CACHE_ROOT


app = FastAPI(title="Satellite Network Globe API")
logger = logging.getLogger(__name__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _safe_dataset_path(dataset: str | None) -> Path:
    base = Path(DATASET_ROOT).resolve()
    if not dataset:
        return Path(DEFAULT_DATASET_DIR).resolve()

    if dataset in {".", ".."} or "/" in dataset or "\\" in dataset:
        raise HTTPException(status_code=400, detail="dataset must be a directory name, not a path.")
    path = (base / dataset).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(status_code=400, detail="dataset must be located under configured dataset root.")

    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=404, detail="Dataset not found.")
    return path


_store_cache: dict[str, SatelliteDataset] = {}


def _get_store(dataset: str | None) -> SatelliteDataset:
    path = _safe_dataset_path(dataset)
    key = str(path)
    store = _store_cache.get(key)
    if store is not None:
        try:
            store.load()
        except (FileNotFoundError, ValueError, OSError) as exc:
            logger.warning("Dataset could not be loaded", exc_info=True)
            raise HTTPException(status_code=400, detail="Dataset could not be loaded. Check the server logs.") from exc
        return store
    store = SatelliteDataset(key, db_root=DB_CACHE_ROOT)
    try:
        store.load()
    except (FileNotFoundError, ValueError, OSError) as exc:
        logger.warning("Dataset could not be loaded", exc_info=True)
        raise HTTPException(status_code=400, detail="Dataset could not be loaded. Check the server logs.") from exc
    _store_cache[key] = store
    return store


def _required_dataset_files(dataset_path: Path) -> bool:
    isl_exists = (dataset_path / "ISL_links.csv").exists() or (dataset_path / "ISL_links.parquet").exists()
    return (dataset_path / "sat_subloc_map.csv").exists() and isl_exists


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/datasets")
def list_datasets():
    root = Path(DATASET_ROOT).resolve()
    if not root.exists():
        raise HTTPException(status_code=500, detail="Dataset root does not exist.")

    datasets = []
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        if not candidate.resolve().is_relative_to(root):
            continue
        if not _required_dataset_files(candidate):
            continue
        datasets.append({"name": candidate.name})

    if not datasets and _required_dataset_files(Path(DEFAULT_DATASET_DIR)):
        fallback = Path(DEFAULT_DATASET_DIR).resolve()
        if fallback.is_relative_to(root) and fallback.parent == root:
            datasets = [{"name": fallback.name}]
    return datasets


@app.get("/api/metadata")
def metadata(dataset: str | None = Query(default=None, description="Dataset name, under configured root")):
    store = _get_store(dataset)
    iterations = store.get_metadata()
    return {
        "dataset": _safe_dataset_path(dataset).name,
        "step_seconds": store.get_step_seconds(iterations),
        "iterations": iterations,
    }


@app.get("/api/frames")
def frames(
    dataset: str | None = Query(default=None, description="Dataset name, under configured root"),
    start: int | None = Query(default=None, description="Start iteration (inclusive)"),
    end: int | None = Query(default=None, description="End iteration (inclusive)"),
):
    store = _get_store(dataset)
    payload = [store.to_dict(frame) for frame in store.get_frames(start=start, end=end)]
    if not payload:
        raise HTTPException(status_code=404, detail="No frames found for the requested range.")
    # Frames are already decoded JSON from SQLite. Serialize them directly rather
    # than recursively converting every coordinate and edge a second time.
    return JSONResponse(payload)


@app.get("/api/frames/{iteration}")
def frame(iteration: int, dataset: str | None = Query(default=None, description="Dataset name, under configured root")):
    store = _get_store(dataset)
    frame = store.get_frame(iteration)
    if frame is None:
        raise HTTPException(status_code=404, detail=f"Iteration {iteration} not found.")
    return JSONResponse(store.to_dict(frame))
