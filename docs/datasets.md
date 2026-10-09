# Dataset format

Each dataset is a directory containing the files below. `iteration` is an integer
frame index. Satellite IDs in links and paths must match IDs in the position file.
Latitude and longitude are degrees; altitude is **meters** above the globe.

## Required files

### `sat_subloc_map.csv`

Required columns: `iteration`, `id`, `name`, `sublat`, `sublong`, `altitude`.
Optional columns: `shell`, `orbit`, used for filtering.

```csv
id,name,shell,orbit,iteration,altitude,sublat,sublong
0,Demo-Sat-000,1,1,0,550000,0,0
1,Demo-Sat-001,1,1,0,550000,20,30
```

### `ISL_links.csv` or `ISL_links.parquet`

Required columns: `iteration`, `ISL_link`.
Optional columns: `sim_time_utc`, `degraded_ISL_link`.
Each frame has one row of satellite ID pairs, serialized as a list.
When both formats exist, Parquet is used for link data.

```csv
iteration,sim_time_utc,ISL_link,degraded_ISL_link
0,2025-01-01T00:00:00Z,"[[0, 1]]","[]"
```

## Optional files

| File | Columns | Purpose |
| --- | --- | --- |
| `Routing_path.csv` | `iteration`, `src_lat`, `src_lon`, `dst_lat`, `dst_lon`, `src_sat_id`, `dst_sat_id`, `path` | One row per route; `path` is a serialized list of satellite IDs. |
| `gs_visibility.csv` | `iteration`, `id`, `gs_name`, `lat`, `lon` | Ground station markers per frame. |
| `Sat_usage.csv` | `iteration`, `name`, `shell`, `orbit` | Shell and orbit information joined by satellite name and iteration. |

Routing rows can also include `routing_degradation_aware`, `task_state`, `dst_gs`,
`e2e_delay_s` (seconds), `e2e_bandwidth_mbps` (Mbps), `e2e_packet_loss`, and
`e2e_success_prob`. Packet loss and success probability use fractions from 0 to 1.
Leave missing numeric metrics empty. These values are returned by the API, not recalculated.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SATELLITE_DATA_ROOT` | Repository `demo_data/` locally; `/app/data` in Docker | Parent directory scanned for selectable datasets. |
| `SATELLITE_DATA_PATH` | `demo_constellation/` under the default root | Dataset used when no name is supplied. |
| `SATELLITE_DB_ROOT` | `/tmp/satellite_cache` locally; `/app/satellite_db_cache` in Docker | Generated SQLite cache directory. |
| `SATELLITE_DB_VOLUME` | `./satellite_db_cache` | Host cache mount in Docker Compose. |

For a custom root without a `demo_constellation/` child, set `SATELLITE_DATA_PATH`
explicitly as well. Environment paths used in Docker refer to paths inside the
container; the `docker-compose.data.yml` override mounts host `data/` at
`/app/local-data`. Requests such as `/api/metadata?dataset=my_dataset` use names
relative to the configured root.

Source files should remain stable while frames are loading. Replacing source files
invalidates the cache. Generated caches and local datasets are excluded from source
control and Docker build contexts.
