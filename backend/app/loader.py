import ast
import hashlib
import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path
import sqlite3

import pandas as pd

SCHEMA_VERSION = 2


def _safe_float(value, default=0.0):
    if default is None:
        default_out = None
    else:
        default_out = float(default)

    if value is None:
        return default_out
    if isinstance(value, (int, float)) and (math.isnan(value) or math.isinf(value)):
        return default_out
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default_out
    if math.isnan(numeric) or math.isinf(numeric):
        return default_out
    return numeric


def _safe_int(value, default=None):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    if isinstance(value, float) and math.isinf(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_stringified_list(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if not isinstance(value, str):
        return []
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return []
    if parsed is None:
        return []
    return list(parsed)


def _canonical_pair(edge):
    if not isinstance(edge, (list, tuple)) or len(edge) < 2:
        return None
    try:
        a = int(edge[0])
        b = int(edge[1])
    except (TypeError, ValueError):
        return None
    return (a, b)


def _canonical_path(value):
    parsed = _parse_stringified_list(value)
    ids = []
    for item in parsed:
        if item is None:
            continue
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _safe_read(path: Path, ext: str):
    if not path.exists():
        return None
    if ext == ".parquet":
        return pd.read_parquet(path)
    if ext == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported file extension: {ext}")


def _count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
        count = 0
        for count, _ in enumerate(fh, start=1):
            pass
    return max(count - 1, 0)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _signature_for_file(path: Path) -> tuple[str, int, int, str, int, int]:
    stat = path.stat()
    if path.suffix.lower() == ".csv":
        row_count = _count_csv_rows(path)
    else:
        row_count = int(_safe_read(path, path.suffix.lower()).shape[0])
    return (
        path.name,
        int(stat.st_size),
        int(stat.st_mtime_ns),
        _sha256_file(path),
        row_count,
        SCHEMA_VERSION,
    )


@dataclass
class DatasetFrame:
    iteration: int
    sim_time_utc: str | None
    satellites: list[dict]
    isl_edges: list[dict]
    routing_paths: list[dict]
    gs_stations: list[dict]


class SatelliteDataset:
    def __init__(self, dataset_path: str, db_root: str | None = None):
        self.dataset_path = Path(dataset_path)
        self.sat_df = pd.DataFrame()
        self.isl_df = pd.DataFrame()
        self.path_df = pd.DataFrame()
        self.gs_df = pd.DataFrame()
        self.sat_usage_df = pd.DataFrame()
        self.cross_orbit_df = pd.DataFrame()

        db_root_path = Path(db_root or "/tmp/satellite_cache")
        db_root_path.mkdir(parents=True, exist_ok=True)
        self.db_path = db_root_path / f"{self.dataset_path.as_posix().encode().hex()}_{SCHEMA_VERSION}.sqlite"
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            self._validate_required_files()
            if not self._is_cache_valid():
                self._build_cache()

    def _validate_required_files(self) -> None:
        sat_path = self.dataset_path / "sat_subloc_map.csv"
        if not sat_path.exists():
            raise FileNotFoundError(f"Missing required dataset file: {sat_path.name}")

        isl_parquet_path = self.dataset_path / "ISL_links.parquet"
        isl_csv_path = self.dataset_path / "ISL_links.csv"
        if not isl_parquet_path.exists() and not isl_csv_path.exists():
            raise FileNotFoundError("Missing required dataset file: ISL_links.csv or ISL_links.parquet")

    def _iterative_source_files(self) -> list[Path]:
        sat_path = self.dataset_path / "sat_subloc_map.csv"
        isl_parquet_path = self.dataset_path / "ISL_links.parquet"
        isl_csv_path = self.dataset_path / "ISL_links.csv"
        routing_path = self.dataset_path / "Routing_path.csv"
        usage_path = self.dataset_path / "Sat_usage.csv"
        gs_path = self.dataset_path / "gs_visibility.csv"
        cross_orbit_path = self.dataset_path / "Cross_orbit_ISL.csv"

        candidates: list[Path] = [sat_path]
        if isl_parquet_path.exists():
            candidates.append(isl_parquet_path)
        if isl_csv_path.exists():
            candidates.append(isl_csv_path)
        if routing_path.exists():
            candidates.append(routing_path)
        if usage_path.exists():
            candidates.append(usage_path)
        if gs_path.exists():
            candidates.append(gs_path)
        if cross_orbit_path.exists():
            candidates.append(cross_orbit_path)
        return [path for path in candidates if path.exists()]

    def _collect_current_signatures(self) -> list[tuple[str, int, int, str, int, int]]:
        signatures = []
        for path in self._iterative_source_files():
            signatures.append(_signature_for_file(path))
        signatures.sort(key=lambda item: item[0])
        return signatures

    def _read_cached_signatures(self) -> list[tuple[str, int, int, str, int, int]]:
        if not self.db_path.exists():
            return []
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dataset_manifest'").fetchone()
            if not row:
                return []
            rows = conn.execute(
                "SELECT file_name, size, mtime_ns, sha256, row_count, schema_version FROM dataset_manifest ORDER BY file_name"
            ).fetchall()
            return [
                (
                    row["file_name"],
                    int(row["size"]),
                    int(row["mtime_ns"]),
                    str(row["sha256"]),
                    int(row["row_count"]),
                    int(row["schema_version"]),
                )
                for row in rows
            ]
        finally:
            conn.close()

    def _is_cache_valid(self) -> bool:
        if not self.db_path.exists():
            return False

        try:
            cached = self._read_cached_signatures()
            current = self._collect_current_signatures()
            return cached == current
        except Exception:
            return False

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_manifest (
                file_name TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS frame_cache (
                iteration INTEGER PRIMARY KEY,
                sim_time_utc TEXT,
                satellites TEXT NOT NULL,
                isl_edges TEXT NOT NULL,
                routing_paths TEXT NOT NULL,
                gs_stations TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_metadata (
                iteration INTEGER PRIMARY KEY,
                sim_time_utc TEXT,
                satellite_count INTEGER NOT NULL,
                isl_edge_count INTEGER NOT NULL,
                routing_path_count INTEGER NOT NULL,
                gs_count INTEGER NOT NULL
            )
            """
        )

    def _clear_cache(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS dataset_manifest")
        conn.execute("DROP TABLE IF EXISTS frame_cache")
        conn.execute("DROP TABLE IF EXISTS dataset_metadata")

    def _build_cache(self) -> None:
        sat_path = self.dataset_path / "sat_subloc_map.csv"
        isl_parquet_path = self.dataset_path / "ISL_links.parquet"
        isl_csv_path = self.dataset_path / "ISL_links.csv"
        routing_path = self.dataset_path / "Routing_path.csv"
        usage_path = self.dataset_path / "Sat_usage.csv"
        gs_path = self.dataset_path / "gs_visibility.csv"
        cross_orbit_path = self.dataset_path / "Cross_orbit_ISL.csv"

        self.sat_df = _safe_read(sat_path, ".csv") if sat_path.exists() else pd.DataFrame()
        if isl_parquet_path.exists():
            self.isl_df = _safe_read(isl_parquet_path, ".parquet")
        elif isl_csv_path.exists():
            self.isl_df = _safe_read(isl_csv_path, ".csv")
        else:
            self.isl_df = pd.DataFrame()

        self.path_df = _safe_read(routing_path, ".csv") if routing_path.exists() else pd.DataFrame()
        self.sat_usage_df = _safe_read(usage_path, ".csv") if usage_path.exists() else pd.DataFrame()
        self.gs_df = _safe_read(gs_path, ".csv") if gs_path.exists() else pd.DataFrame()
        self.cross_orbit_df = _safe_read(cross_orbit_path, ".csv") if cross_orbit_path.exists() else pd.DataFrame()

        if not self.sat_df.empty and "iteration" in self.sat_df.columns:
            self.sat_df["iteration"] = self.sat_df["iteration"].astype(int)

        if not self.sat_usage_df.empty and "iteration" in self.sat_usage_df.columns:
            self.sat_usage_df["iteration"] = self.sat_usage_df["iteration"].astype(int)
            self.sat_df = self.sat_df.merge(
                self.sat_usage_df[["name", "iteration", "shell", "orbit"]].drop_duplicates(),
                on=["name", "iteration"],
                how="left",
                suffixes=("", "_usage"),
            )
            if "shell_usage" in self.sat_df.columns:
                self.sat_df["shell"] = self.sat_df["shell_usage"].fillna(self.sat_df["shell"])
                self.sat_df.drop(columns=["shell_usage"], inplace=True)
            if "orbit_usage" in self.sat_df.columns:
                self.sat_df["orbit"] = self.sat_df["orbit_usage"].fillna(self.sat_df["orbit"])
                self.sat_df.drop(columns=["orbit_usage"], inplace=True)

        if not self.isl_df.empty:
            self.isl_df["iteration"] = self.isl_df["iteration"].astype(int)
        if not self.path_df.empty:
            self.path_df["iteration"] = self.path_df["iteration"].astype(int)
        if not self.gs_df.empty:
            self.gs_df["iteration"] = self.gs_df["iteration"].astype(int)

        iterations = sorted(
            set(
                int(i)
                for i in pd.concat(
                    [
                        self.sat_df["iteration"] if not self.sat_df.empty else pd.Series(dtype=int),
                        self.isl_df["iteration"] if not self.isl_df.empty else pd.Series(dtype=int),
                        self.path_df["iteration"] if not self.path_df.empty else pd.Series(dtype=int),
                        self.gs_df["iteration"] if not self.gs_df.empty else pd.Series(dtype=int),
                    ],
                    ignore_index=True,
                ).dropna().unique()
            )
        )

        print(f"[cache] rebuild dataset cache -> {self.dataset_path}")

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            self._clear_cache(conn)
            self._ensure_schema(conn)

            for file_name, size, mtime_ns, sha256_digest, row_count, schema_version in self._collect_current_signatures():
                conn.execute(
                    "INSERT INTO dataset_manifest(file_name, size, mtime_ns, sha256, row_count, schema_version, updated_at) VALUES(?,?,?,?,?,?, strftime('%s', 'now'))",
                    (file_name, size, mtime_ns, sha256_digest, row_count, schema_version),
                )

            for iteration in iterations:
                frame = self._build_frame(iteration)
                metadata = self._to_metadata(frame)

                conn.execute(
                    "INSERT INTO frame_cache(iteration, sim_time_utc, satellites, isl_edges, routing_paths, gs_stations) VALUES(?,?,?,?,?,?)",
                    (
                        frame.iteration,
                        metadata["sim_time_utc"],
                        json.dumps(frame.satellites),
                        json.dumps(frame.isl_edges),
                        json.dumps(frame.routing_paths),
                        json.dumps(frame.gs_stations),
                    ),
                )
                conn.execute(
                    "INSERT INTO dataset_metadata(iteration, sim_time_utc, satellite_count, isl_edge_count, routing_path_count, gs_count) VALUES(?,?,?,?,?,?)",
                    (
                        metadata["iteration"],
                        metadata["sim_time_utc"],
                        metadata["satellite_count"],
                        metadata["isl_edge_count"],
                        metadata["routing_path_count"],
                        metadata["gs_count"],
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def _build_frame(self, iteration: int) -> DatasetFrame:
        sat_iter = self.sat_df[self.sat_df["iteration"] == iteration].copy() if not self.sat_df.empty else pd.DataFrame()
        isl_iter = self.isl_df[self.isl_df["iteration"] == iteration].copy() if not self.isl_df.empty else pd.DataFrame()
        path_iter = self.path_df[self.path_df["iteration"] == iteration].copy() if not self.path_df.empty else pd.DataFrame()
        gs_iter = self.gs_df[self.gs_df["iteration"] == iteration].copy() if not self.gs_df.empty else pd.DataFrame()

        satellites = []
        sat_lookup = {}
        for _, row in sat_iter.iterrows():
            sat_id = _safe_int(row.get("id"), default=None)
            if sat_id is None:
                continue
            sat_record = {
                "id": sat_id,
                "name": row.get("name"),
                "shell": _safe_int(row.get("shell"), default=None),
                "orbit": _safe_int(row.get("orbit"), default=None),
                "sublat": _safe_float(row.get("sublat")),
                "sublong": _safe_float(row.get("sublong")),
                "altitude": _safe_float(row.get("altitude")),
            }
            satellites.append(sat_record)
            sat_lookup[sat_id] = sat_record

        isl_edges = []
        if not isl_iter.empty:
            isl_row = isl_iter.iloc[0]
            links = _parse_stringified_list(isl_row.get("ISL_link"))
            degraded = set()
            degraded_raw = _parse_stringified_list(isl_row.get("degraded_ISL_link"))
            for edge in degraded_raw:
                canonical = _canonical_pair(edge)
                if canonical:
                    a, b = canonical
                    degraded.add((a, b))
                    degraded.add((b, a))
            if not degraded and "degraded_ISL_link_norad" in isl_iter.columns:
                degraded_raw = _parse_stringified_list(isl_row.get("degraded_ISL_link_norad"))
                for edge in degraded_raw:
                    canonical = _canonical_pair(edge)
                    if canonical:
                        a, b = canonical
                        degraded.add((a, b))
                        degraded.add((b, a))

            for edge in links:
                canonical = _canonical_pair(edge)
                if canonical is None:
                    continue
                src_id, dst_id = canonical
                if src_id not in sat_lookup or dst_id not in sat_lookup:
                    continue
                src_sat = sat_lookup[src_id]
                dst_sat = sat_lookup[dst_id]
                isl_edges.append(
                    {
                        "src_sat_id": src_id,
                        "dst_sat_id": dst_id,
                        "src_lat": src_sat["sublat"],
                        "src_lon": src_sat["sublong"],
                        "dst_lat": dst_sat["sublat"],
                        "dst_lon": dst_sat["sublong"],
                        "src_alt": src_sat["altitude"],
                        "dst_alt": dst_sat["altitude"],
                        "cross_shell": bool(
                            src_sat.get("shell") is not None
                            and dst_sat.get("shell") is not None
                            and src_sat["shell"] != dst_sat["shell"]
                        ),
                        "degraded": (src_id, dst_id) in degraded,
                    }
                )
        else:
            sim_time = None

        routing_paths = []
        for _, row in path_iter.iterrows():
            routing_paths.append(
                {
                    "src_lat": _safe_float(row.get("src_lat")),
                    "src_lon": _safe_float(row.get("src_lon")),
                    "dst_lat": _safe_float(row.get("dst_lat")),
                    "dst_lon": _safe_float(row.get("dst_lon")),
                    "src_sat_id": _safe_int(row.get("src_sat_id"), default=None),
                    "dst_sat_id": _safe_int(row.get("dst_sat_id"), default=None),
                    "routing_degradation_aware": bool(row.get("routing_degradation_aware", False)),
                    "task_state": row.get("task_state"),
                    "e2e_delay_s": _safe_float(row.get("e2e_delay_s"), default=None),
                    "e2e_bandwidth_mbps": _safe_float(row.get("e2e_bandwidth_mbps"), default=None),
                    "e2e_packet_loss": _safe_float(row.get("e2e_packet_loss"), default=None),
                    "e2e_success_prob": _safe_float(row.get("e2e_success_prob"), default=None),
                    "dst_gs": row.get("dst_gs", None),
                    "path": _canonical_path(row.get("path")),
                }
            )

        gs_stations = []
        for _, row in gs_iter.iterrows():
            gs_stations.append(
                {
                    "id": row.get("id", row.get("gs_name")),
                    "gs_name": row.get("gs_name", row.get("id")),
                    "lat": _safe_float(row.get("lat")),
                    "lon": _safe_float(row.get("lon")),
                }
            )

        sim_time = None
        if not isl_iter.empty:
            sim_time = isl_iter.iloc[0].get("sim_time_utc")

        return DatasetFrame(
            iteration=int(iteration),
            sim_time_utc=str(sim_time) if pd.notna(sim_time) else None,
            satellites=satellites,
            isl_edges=isl_edges,
            routing_paths=routing_paths,
            gs_stations=gs_stations,
        )

    def _to_metadata(self, frame: DatasetFrame) -> dict:
        return {
            "iteration": int(frame.iteration),
            "sim_time_utc": frame.sim_time_utc,
            "satellite_count": len(frame.satellites),
            "isl_edge_count": len(frame.isl_edges),
            "routing_path_count": len(frame.routing_paths),
            "gs_count": len(frame.gs_stations),
        }

    def _decode_frame(self, row: sqlite3.Row) -> DatasetFrame:
        sim_time = row["sim_time_utc"]
        if sim_time == "":
            sim_time = None
        return DatasetFrame(
            iteration=int(row["iteration"]),
            sim_time_utc=sim_time,
            satellites=json.loads(row["satellites"]),
            isl_edges=json.loads(row["isl_edges"]),
            routing_paths=json.loads(row["routing_paths"]),
            gs_stations=json.loads(row["gs_stations"]),
        )

    def get_metadata(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT iteration, sim_time_utc, satellite_count, isl_edge_count, routing_path_count, gs_count FROM dataset_metadata ORDER BY iteration"
            ).fetchall()
            return [
                {
                    "iteration": int(row["iteration"]),
                    "sim_time_utc": row["sim_time_utc"],
                    "satellite_count": int(row["satellite_count"]),
                    "isl_edge_count": int(row["isl_edge_count"]),
                    "routing_path_count": int(row["routing_path_count"]),
                    "gs_count": int(row["gs_count"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def list_iterations(self) -> list[int]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT iteration FROM dataset_metadata ORDER BY iteration").fetchall()
            return [int(row[0]) for row in rows]
        finally:
            conn.close()

    def get_frame(self, iteration: int) -> DatasetFrame | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT iteration, sim_time_utc, satellites, isl_edges, routing_paths, gs_stations FROM frame_cache WHERE iteration = ?",
                (iteration,),
            ).fetchone()
            if row is None:
                return None
            return self._decode_frame(row)
        finally:
            conn.close()

    def get_frames(self, start: int | None = None, end: int | None = None) -> list[DatasetFrame]:
        iterations = self.list_iterations()
        if not iterations:
            return []

        start_iter = iterations[0] if start is None else max(start, iterations[0])
        end_iter = iterations[-1] if end is None else min(end, iterations[-1])
        if start_iter > end_iter:
            return []

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT iteration, sim_time_utc, satellites, isl_edges, routing_paths, gs_stations FROM frame_cache WHERE iteration BETWEEN ? AND ? ORDER BY iteration",
                (start_iter, end_iter),
            ).fetchall()
            return [
                self._decode_frame(row)
                for row in rows
            ]
        finally:
            conn.close()

    def to_dict(self, frame: DatasetFrame) -> dict:
        return {
            "iteration": frame.iteration,
            "sim_time_utc": frame.sim_time_utc,
            "satellites": frame.satellites,
            "isl_edges": frame.isl_edges,
            "routing_paths": frame.routing_paths,
            "gs_stations": frame.gs_stations,
        }
