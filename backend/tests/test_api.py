import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.app import main
from scripts.generate_demo import FRAMES, generate


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "datasets"
        self.dataset = self.root / "demo_constellation"
        generate(self.dataset)
        self.settings = patch.multiple(
            main,
            DATASET_ROOT=str(self.root),
            DEFAULT_DATASET_DIR=str(self.dataset),
            DB_CACHE_ROOT=str(Path(self.temp.name) / "cache"),
            _store_cache={},
        )
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_health_and_dataset_list_do_not_expose_paths(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/api/datasets").json(), [{"name": "demo_constellation"}])

    def test_sample_playback_and_ranges(self):
        metadata = self.client.get("/api/metadata", params={"dataset": "demo_constellation"})
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(set(metadata.json()), {"dataset", "step_seconds", "iterations"})
        self.assertEqual(metadata.json()["step_seconds"], 60)
        self.assertEqual(len(metadata.json()["iterations"]), FRAMES)
        self.assertEqual(metadata.json()["iterations"][0]["satellite_count"], 96)
        self.assertEqual(metadata.json()["iterations"][0]["isl_edge_count"], 180)
        frames = self.client.get("/api/frames", params={"start": 8, "end": 9})
        self.assertEqual(frames.status_code, 200)
        self.assertEqual([frame["iteration"] for frame in frames.json()], [8, 9])
        frame = frames.json()[0]
        self.assertEqual(sum(edge["degraded"] for edge in frame["isl_edges"]), 2)
        self.assertTrue(any(edge["cross_shell"] for edge in frame["isl_edges"]))
        self.assertEqual(len(frame["routing_paths"]), 3)
        self.assertEqual(len(frame["gs_stations"]), 3)
        self.assertEqual(self.client.get("/api/frames/8").json(), frame)
        self.assertEqual(self.client.get("/api/frames/999").status_code, 404)
        self.assertEqual(self.client.get("/api/frames", params={"start": 5, "end": 4}).status_code, 404)

    def test_rejects_paths_and_symlinks_outside_dataset_root(self):
        for dataset in ("..", ".", "../datasets-other", "/etc", str(self.root) + "-other", "a\\b"):
            with self.subTest(dataset=dataset):
                self.assertEqual(self.client.get("/api/metadata", params={"dataset": dataset}).status_code, 400)
        outside = Path(self.temp.name) / "outside"
        generate(outside)
        (self.root / "external").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.client.get("/api/metadata", params={"dataset": "external"}).status_code, 400)
        self.assertEqual(self.client.get("/api/datasets").json(), [{"name": "demo_constellation"}])

    def test_dataset_error_does_not_expose_server_paths(self):
        with patch.object(main.SatelliteDataset, "load", side_effect=OSError(f"Cannot read {self.dataset}")):
            with self.assertLogs(main.logger, level="WARNING"):
                response = self.client.get("/api/metadata")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(str(self.root), response.text)
        self.assertIn("Check the server logs", response.json()["detail"])

    def test_modified_sources_rebuild_cached_frames(self):
        first = self.client.get("/api/frames/0").json()
        source = self.dataset / "sat_subloc_map.csv"
        source.write_text(source.read_text().replace("Demo-Sat-000", "Updated-Sat-000"))
        second = self.client.get("/api/frames/0").json()
        self.assertEqual(first["satellites"][0]["name"], "Demo-Sat-000")
        self.assertEqual(second["satellites"][0]["name"], "Updated-Sat-000")

    def test_routing_method_is_optional_and_updates_with_source(self):
        original = self.client.get("/api/frames/0").json()
        self.assertTrue(all(route["routing_method"] is None for route in original["routing_paths"]))
        source = self.dataset / "Routing_path.csv"
        routes = pd.read_csv(source)
        routes["routing_method"] = "VISUAL_DEMO"
        routes.loc[1, "routing_method"] = None
        routes.to_csv(source, index=False)
        updated = self.client.get("/api/frames/0")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["routing_paths"][0]["routing_method"], "VISUAL_DEMO")
        self.assertIsNone(updated.json()["routing_paths"][1]["routing_method"])

    def test_parquet_links_and_missing_optional_layers(self):
        links = self.dataset / "ISL_links.csv"
        pd.read_csv(links).to_parquet(self.dataset / "ISL_links.parquet", index=False)
        links.unlink()
        (self.dataset / "Routing_path.csv").unlink()
        (self.dataset / "gs_visibility.csv").unlink()
        response = self.client.get("/api/frames/0")
        self.assertEqual(response.status_code, 200)
        frame = response.json()
        self.assertEqual(len(frame["isl_edges"]), 180)
        self.assertEqual(frame["routing_paths"], [])
        self.assertEqual(frame["gs_stations"], [])

    def test_timestamps_override_config_and_step_changes_invalidate_cache(self):
        (self.dataset / "Hyperparameters.csv").write_text("group,name,value\nglobal,INTERVAL_LENGTH_MIN,10\n")
        self.assertEqual(self.client.get("/api/metadata").json()["step_seconds"], 60)
        links_path = self.dataset / "ISL_links.csv"
        links = pd.read_csv(links_path)
        start = pd.Timestamp("2025-01-01T00:00:00Z")
        links["sim_time_utc"] = [(start + pd.Timedelta(seconds=int(i) * 30)).isoformat() for i in links["iteration"]]
        # The positions still have every iteration, while only even iterations
        # have timestamps. Infer seconds per iteration rather than per stored row.
        links.loc[links["iteration"] % 2 == 0].to_csv(links_path, index=False)
        metadata = self.client.get("/api/metadata").json()
        self.assertEqual(metadata["step_seconds"], 30)
        self.assertEqual(metadata["iterations"][2]["sim_time_utc"], "2025-01-01T00:01:00+00:00")

    def test_timing_parameters_when_timestamps_are_missing(self):
        links = self.dataset / "ISL_links.csv"
        pd.read_csv(links).drop(columns=["sim_time_utc"]).to_csv(links, index=False)
        self.assertIsNone(self.client.get("/api/metadata").json()["step_seconds"])
        provenance = self.dataset / "demo_provenance.json"
        provenance.write_text(json.dumps({"step_seconds": 30}))
        self.assertEqual(self.client.get("/api/metadata").json()["step_seconds"], 30)
        parameters = self.dataset / "Hyperparameters.csv"
        parameters.write_text("group,name,value\nglobal,INTERVAL_LENGTH_MIN,0.5\n")
        self.assertEqual(self.client.get("/api/metadata").json()["step_seconds"], 30)
        parameters.write_text("group,name,value\nglobal,INTERVAL_LENGTH_MIN,2\n")
        self.assertEqual(self.client.get("/api/metadata").json()["step_seconds"], 120)

    def test_invalid_optional_timing_does_not_break_metadata(self):
        links = self.dataset / "ISL_links.csv"
        pd.read_csv(links).drop(columns=["sim_time_utc"]).to_csv(links, index=False)
        (self.dataset / "Hyperparameters.csv").write_text("")
        for value in (0, -30, "nan", True, None):
            with self.subTest(step=value):
                (self.dataset / "demo_provenance.json").write_text(json.dumps({"step_seconds": value}))
                response = self.client.get("/api/metadata")
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()["step_seconds"])


if __name__ == "__main__":
    unittest.main()
