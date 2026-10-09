#!/usr/bin/env python3
"""Generate a deterministic, fictional constellation using only the standard library."""

import argparse
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "demo_data" / "demo_constellation"
FRAMES = 24
PLANES_PER_SHELL = 4
SATELLITES_PER_PLANE = 12
SHELLS = ((1, 53.0, 550_000), (2, 70.0, 650_000))


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    satellites, links, routes, stations = [], [], [], []
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    per_shell = PLANES_PER_SHELL * SATELLITES_PER_PLANE
    for iteration in range(FRAMES):
        positions = {}
        edges = []
        for shell_index, (shell, inclination, altitude) in enumerate(SHELLS):
            inc = math.radians(inclination)
            for plane in range(PLANES_PER_SHELL):
                raan = 2 * math.pi * plane / PLANES_PER_SHELL + shell_index * math.pi / 4
                for slot in range(SATELLITES_PER_PLANE):
                    sat_id = shell_index * per_shell + plane * SATELLITES_PER_PLANE + slot
                    phase = 2 * math.pi * (slot / SATELLITES_PER_PLANE + iteration / 90) + plane * 0.15
                    x = math.cos(raan) * math.cos(phase) - math.sin(raan) * math.sin(phase) * math.cos(inc)
                    y = math.sin(raan) * math.cos(phase) + math.cos(raan) * math.sin(phase) * math.cos(inc)
                    z = math.sin(phase) * math.sin(inc)
                    lat = round(math.degrees(math.asin(z)), 6)
                    lon = round((math.degrees(math.atan2(y, x)) - iteration * 0.25 + 180) % 360 - 180, 6)
                    positions[sat_id] = (lat, lon)
                    satellites.append({
                        "id": sat_id, "name": f"Demo-Sat-{sat_id:03d}", "shell": shell,
                        "orbit": plane + 1, "iteration": iteration, "altitude": altitude,
                        "sublat": lat, "sublong": lon,
                    })
                    neighbor = shell_index * per_shell + plane * SATELLITES_PER_PLANE + (slot + 1) % SATELLITES_PER_PLANE
                    edges.append([sat_id, neighbor])
                    if plane < PLANES_PER_SHELL - 1:
                        edges.append([sat_id, sat_id + SATELLITES_PER_PLANE])

        # Illustrative cross-shell edges and degradation; no operational link model.
        edges.extend([[slot, per_shell + slot] for slot in range(SATELLITES_PER_PLANE)])
        degraded = [[0, 1], [13, 14]] if 8 <= iteration <= 15 else []
        sim_time = (start + timedelta(minutes=iteration)).isoformat().replace("+00:00", "Z")
        links.append({
            "iteration": iteration, "sim_time_utc": sim_time,
            "ISL_link": json.dumps(edges), "degraded_ISL_link": json.dumps(degraded),
        })
        for route_index, path in enumerate(([0, 1, 2, 3], [24, 36, 37, 38], [6, 54, 55, 56])):
            src_lat, src_lon = positions[path[0]]
            dst_lat, dst_lon = positions[path[-1]]
            impacted = route_index == 0 and bool(degraded)
            routes.append({
                "iteration": iteration, "src_lat": src_lat, "src_lon": src_lon,
                "dst_lat": dst_lat, "dst_lon": dst_lon, "src_sat_id": path[0], "dst_sat_id": path[-1],
                "routing_degradation_aware": False, "task_state": "SUCC", "path": json.dumps(path),
                "e2e_delay_s": 0.04 if impacted else 0.015,
                "e2e_bandwidth_mbps": 300 if impacted else 1000,
                "e2e_packet_loss": 0.05 if impacted else 0.0,
                "e2e_success_prob": 0.95 if impacted else 1.0,
            })
        for station_id, (lat, lon) in enumerate(((35, -120), (45, 15), (-25, 130))):
            stations.append({
                "iteration": iteration, "id": station_id, "gs_name": f"Demo-GS-{station_id}",
                "lat": lat, "lon": lon,
            })

    write_csv(output / "sat_subloc_map.csv", ["id", "name", "shell", "orbit", "iteration", "altitude", "sublat", "sublong"], satellites)
    write_csv(output / "ISL_links.csv", ["iteration", "sim_time_utc", "ISL_link", "degraded_ISL_link"], links)
    write_csv(output / "Routing_path.csv", list(routes[0]), routes)
    write_csv(output / "gs_visibility.csv", ["iteration", "id", "gs_name", "lat", "lon"], stations)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.output)
    print(f"Generated {FRAMES} frames with {len(SHELLS) * PLANES_PER_SHELL * SATELLITES_PER_PLANE} satellites per frame.")
