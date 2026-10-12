# Demonstration dataset

`demo_constellation/` contains a generated-constellation recording exported from
a separate simulator. It includes 1,584 satellites in shell 0, with 72 orbital
planes and 22 satellites per plane. There are 480 frames at 30-second intervals,
covering four hours. The first timestamp is `2026-02-15T00:00:30Z` and the last is
`2026-02-15T04:00:00Z`.

Every frame contains one fixed Vancouver → Washington, D.C. connection using the
`VISUAL_DEMO` method and an illustrative destination gateway. The constellation
uses generated TLEs; satellite names and IDs are internal simulation identifiers.
The gateway is not a verified operational installation. The recording was prepared
for visualization, without maneuver overlays or random link-quality variation.

The included files are:

| File | Contents |
| --- | --- |
| `sat_subloc_map.csv` | All satellite positions, shell IDs, and orbit IDs for all frames. |
| `ISL_links.parquet` | The matching link graph and timestamps for all frames; only `iteration`, `sim_time_utc`, `ISL_link`, and `degraded_ISL_link` are retained. |
| `Routing_path.csv` | Selected paths, method names, task states, and simulated route metrics. |
| `gs_visibility.csv` | The illustrative destination ground-station marker. |

The dataset occupies about 64.58 MiB. The satellite position CSV is 64.46 MiB;
the link file uses Parquet dictionary encoding and Zstandard compression. No
redundant satellite usage table is needed because positions already include shell
and orbit IDs. Full simulator configuration, research logs, and per-edge diagnostic
exports are kept outside this sample.

The viewer replays this export and does not calculate orbits or routes. Bandwidth
is the simulated path's bottleneck capacity and packet loss is a modeled probability,
rather than an operational measurement. Validate the files with:

```bash
python scripts/validate_dataset.py demo_data/demo_constellation
```

`python scripts/generate_demo.py` creates a separate compact fixture under ignored
`data/synthetic_constellation/` for development and tests. That fixture has 96
satellites, 24 frames, three routes, and three fictional ground stations; it does
not regenerate or overwrite the bundled four-hour recording.
