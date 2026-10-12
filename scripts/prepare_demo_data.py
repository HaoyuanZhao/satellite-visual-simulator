#!/usr/bin/env python3
"""Copy only viewer inputs from an existing simulator export into a public demo."""

import argparse
from pathlib import Path
import shutil

import pandas as pd

from validate_dataset import validate


def prepare(source, output):
    if source.resolve() == output.resolve():
        raise ValueError("Source and output must be different directories.")
    for name in ("sat_subloc_map.csv", "ISL_links.parquet", "Routing_path.csv", "gs_visibility.csv"):
        if not (source / name).is_file():
            raise ValueError(f"Missing required public demo input: {name}.")
    validate(source)
    for name in ("ISL_links.csv", "Sat_usage.csv"):
        if (output / name).exists():
            raise ValueError(f"Move obsolete {name} out of the output directory before preparing the export.")
    # Shell/orbit fields are required here because the redundant usage table is
    # intentionally excluded from this public export.
    columns = pd.read_csv(source / "sat_subloc_map.csv", nrows=0).columns
    if not {"shell", "orbit"}.issubset(columns):
        raise ValueError("Position rows must include shell and orbit before omitting Sat_usage.csv.")
    viewer_columns = ["iteration", "sim_time_utc", "ISL_link", "degraded_ISL_link"]
    links = pd.read_parquet(source / "ISL_links.parquet", columns=viewer_columns)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("sat_subloc_map.csv", "Routing_path.csv", "gs_visibility.csv"):
        shutil.copy2(source / name, output / name)
    links.to_parquet(output / "ISL_links.parquet", index=False, compression="zstd")
    validate(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "demo_data/demo_constellation")
    args = parser.parse_args()
    prepare(args.source, args.output)
    print(f"Prepared viewer data in {args.output}")
