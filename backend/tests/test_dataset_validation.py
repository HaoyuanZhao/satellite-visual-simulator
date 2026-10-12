import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.generate_demo import FRAMES, generate
from scripts.validate_dataset import validate


class DatasetValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dataset = Path(self.temp.name)
        generate(self.dataset)

    def test_consistent_recording_has_matching_frames_and_timing(self):
        report = validate(self.dataset)
        self.assertEqual(report["frames"], FRAMES)
        self.assertEqual(report["route_rows"], FRAMES * 3)
        self.assertEqual(report["satellites_per_frame"], {"min": 96, "max": 96})
        self.assertEqual(report["step_seconds"], 60)

    def test_missing_link_frames_are_rejected(self):
        source = self.dataset / "ISL_links.csv"
        pd.read_csv(source).iloc[:-1].to_csv(source, index=False)
        with self.assertRaisesRegex(ValueError, "Link and position frame indices differ"):
            validate(self.dataset)

    def test_route_that_is_not_in_the_link_graph_is_rejected(self):
        source = self.dataset / "Routing_path.csv"
        routes = pd.read_csv(source)
        routes.loc[0, "path"] = json.dumps([0, 95])
        routes.loc[0, "dst_sat_id"] = 95
        routes.to_csv(source, index=False)
        with self.assertRaisesRegex(ValueError, "Route edge.*absent from links"):
            validate(self.dataset)

    def test_failed_routes_and_optional_parquet_timestamps(self):
        source = self.dataset / "Routing_path.csv"
        routes = pd.read_csv(source)
        routes.loc[0, "path"] = ""
        routes.loc[0, "task_state"] = "NO_ROUTING_PATH"
        routes.to_csv(source, index=False)
        links = pd.read_csv(self.dataset / "ISL_links.csv").drop(columns=["sim_time_utc"])
        links.to_parquet(self.dataset / "ISL_links.parquet", index=False)
        (self.dataset / "ISL_links.csv").unlink()
        report = validate(self.dataset)
        self.assertEqual(report["frames"], FRAMES)
        self.assertIsNone(report["step_seconds"])


if __name__ == "__main__":
    unittest.main()
