#!/usr/bin/env python3
"""Check that every recorded route belongs to its frame's satellite/link graph."""

import argparse
import ast
from collections import defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
from statistics import median


def csv_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def parsed_list(value):
    if isinstance(value, str) and not value.strip():
        return []
    parsed = ast.literal_eval(value) if isinstance(value, str) else value
    if parsed is None:
        return []
    if not isinstance(parsed, (list, tuple)):
        raise ValueError("Expected a serialized list of satellite IDs or edges.")
    return parsed


def validate(dataset: Path) -> dict:
    positions = dataset / "sat_subloc_map.csv"
    if not positions.is_file():
        raise ValueError("Missing sat_subloc_map.csv.")
    satellites = defaultdict(set)
    position_rows = 0
    for row in csv_rows(positions):
        iteration, sat_id = int(row["iteration"]), int(row["id"])
        if sat_id in satellites[iteration]:
            raise ValueError(f"Duplicate satellite {sat_id} in frame {iteration}.")
        satellites[iteration].add(sat_id)
        position_rows += 1
    if not satellites:
        raise ValueError("Position file has no frames.")

    route_path = dataset / "Routing_path.csv"
    routes = defaultdict(list)
    if route_path.is_file():
        for row in csv_rows(route_path):
            routes[int(row["iteration"])].append(row)
        if set(routes) != set(satellites):
            raise ValueError("Route and position frame indices differ.")

    parquet_path = dataset / "ISL_links.parquet"
    csv_path = dataset / "ISL_links.csv"
    if parquet_path.is_file():
        import pandas as pd
        import pyarrow.parquet as pq
        schema = pq.read_schema(parquet_path)
        columns = [name for name in ("iteration", "ISL_link", "sim_time_utc") if name in schema.names]
        link_rows = pd.read_parquet(parquet_path, columns=columns).to_dict("records")
    elif csv_path.is_file():
        link_rows = csv_rows(csv_path)
    else:
        raise ValueError("Missing ISL_links.csv or ISL_links.parquet.")

    link_frames, times, edge_counts = set(), {}, []
    previous_raw, previous_edges = None, None
    for row in link_rows:
        iteration = int(row["iteration"])
        if iteration in link_frames or iteration not in satellites:
            raise ValueError(f"Duplicate or unknown link frame {iteration}.")
        link_frames.add(iteration)
        raw = row["ISL_link"]
        if isinstance(raw, str) and raw == previous_raw:
            edges = previous_edges
        else:
            pairs = parsed_list(raw)
            if any(not isinstance(edge, (list, tuple)) or len(edge) != 2 for edge in pairs):
                raise ValueError(f"Malformed edge in frame {iteration}.")
            edges = {tuple(sorted((int(edge[0]), int(edge[1])))) for edge in pairs}
            previous_raw, previous_edges = raw, edges
        if any(a not in satellites[iteration] or b not in satellites[iteration] for a, b in edges):
            raise ValueError(f"Link endpoint absent from positions in frame {iteration}.")
        edge_counts.append(len(edges))
        for route in routes[iteration]:
            path = [int(sat_id) for sat_id in parsed_list(route.get("path"))]
            if route.get("task_state") == "SUCC" and not path:
                raise ValueError(f"Successful route has no path in frame {iteration}.")
            if any(sat_id not in satellites[iteration] for sat_id in path):
                raise ValueError(f"Route satellite absent from positions in frame {iteration}.")
            for a, b in zip(path, path[1:]):
                if tuple(sorted((a, b))) not in edges:
                    raise ValueError(f"Route edge ({a}, {b}) absent from links in frame {iteration}.")
            if path and (int(route["src_sat_id"]) != path[0] or int(route["dst_sat_id"]) != path[-1]):
                raise ValueError(f"Route endpoint IDs disagree with its path in frame {iteration}.")
        timestamp = row.get("sim_time_utc")
        if timestamp:
            times[iteration] = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    if link_frames != set(satellites):
        raise ValueError("Link and position frame indices differ.")

    intervals = []
    timed_frames = sorted(times)
    for left, right in zip(timed_frames, timed_frames[1:]):
        elapsed = (times[right] - times[left]).total_seconds()
        if elapsed <= 0:
            raise ValueError("Recorded timestamps must increase with iteration.")
        intervals.append(elapsed / (right - left))
    return {
        "frames": len(satellites), "position_rows": position_rows,
        "satellites_per_frame": {"min": min(map(len, satellites.values())), "max": max(map(len, satellites.values()))},
        "links_per_frame": {"min": min(edge_counts), "max": max(edge_counts)},
        "route_rows": sum(map(len, routes.values())),
        "step_seconds": median(intervals) if intervals else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.dataset), indent=2))
    except (KeyError, SyntaxError, TypeError, ValueError) as error:
        parser.exit(1, f"Invalid dataset: {error}\n")
