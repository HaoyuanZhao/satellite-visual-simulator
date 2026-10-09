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
        self.assertEqual(set(metadata.json()), {"dataset", "iterations"})
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


if __name__ == "__main__":
    unittest.main()
