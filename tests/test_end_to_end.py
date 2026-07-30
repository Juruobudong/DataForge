from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dataforge.application import DataForge
from dataforge.config import Settings


class DataForgeEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = DataForge(Settings.load(self.root))

    def tearDown(self):
        self.temporary.cleanup()

    def test_native_flow_creates_traceable_asset(self):
        source_file = self.root / "medical.txt"
        source_file.write_text("诊疗记录\n\n患者血压稳定。建议继续随访。", encoding="utf-8")

        result = self.app.flow(source_file, name="随访记录", engine_override="native")

        self.assertEqual(result.run["status"], "completed")
        self.assertEqual(result.asset_version["status"], "published")
        self.assertGreater(result.asset_version["record_count"], 0)
        lineage = self.app.lineage(result.asset_version["id"])
        self.assertEqual(lineage["source_version_id"], result.source_version["id"])
        self.assertEqual(lineage["run_id"], result.run["id"])
        self.assertEqual(
            [event["event_type"] for event in lineage["events"]],
            [
                "created",
                "input_materialized",
                "processing_started",
                "processing_completed",
                "asset_published",
                "completed",
            ],
        )
        exported = self.root / "exports" / "chunks.jsonl"
        receipt = self.app.export_asset(result.asset_version["id"], exported)
        self.assertTrue(exported.is_file())
        self.assertEqual(receipt["sha256"], result.asset_version["sha256"])
        self.assertEqual(len(exported.read_text(encoding="utf-8").splitlines()), 1)

    def test_ingestion_is_idempotent_and_versions_changes(self):
        source_file = self.root / "faq.txt"
        source_file.write_text("问题：如何预约？", encoding="utf-8")
        first = self.app.sources.ingest(source_file, name="FAQ")
        repeated = self.app.sources.ingest(source_file, source_id=first.source["id"])
        self.assertFalse(repeated.created)
        self.assertEqual(first.source_version["id"], repeated.source_version["id"])

        source_file.write_text("问题：如何预约？\n答案：通过医院小程序。", encoding="utf-8")
        changed = self.app.sources.ingest(source_file, source_id=first.source["id"])
        self.assertTrue(changed.created)
        self.assertEqual(changed.source_version["version_no"], 2)

    def test_engine_failure_is_recorded_with_terminal_event(self):
        isolated_root = self.root / "missing-engine"
        isolated_root.mkdir()
        app = DataForge(
            Settings(
                project_root=isolated_root,
                state_dir=isolated_root / ".dataforge",
                dataflow_path=None,
            )
        )
        source_file = self.root / "medical.txt"
        source_file.write_text("一条医疗记录。", encoding="utf-8")
        ingestion = app.sources.ingest(source_file)

        with self.assertRaises(Exception):
            app.run(ingestion.source_version["id"], engine_override="dataflow")

        failed_run = app.store.list_runs()[0]
        self.assertEqual(failed_run["status"], "failed")
        self.assertIn("DataFlow repository was not found", failed_run["error"])
        events = app.store.list_run_events(failed_run["id"])
        self.assertEqual(events[-1]["event_type"], "failed")

    @unittest.skipUnless(os.getenv("DATAFORGE_TEST_DATAFLOW") == "1", "DataFlow integration is opt-in")
    def test_dataflow_engine_executes_compiled_operators(self):
        source_file = self.root / "medical.txt"
        source_file.write_text("病历文本。\n\n这是第二段。", encoding="utf-8")
        dataflow_path = os.getenv("DATAFORGE_DATAFLOW_PATH")
        self.assertIsNotNone(
            dataflow_path,
            "DATAFORGE_DATAFLOW_PATH is required when DATAFORGE_TEST_DATAFLOW=1",
        )
        app = DataForge(Settings.load(self.root, dataflow_path))
        result = app.flow(source_file, engine_override="dataflow")
        self.assertEqual(result.run["status"], "completed")
        self.assertEqual(result.run["stats"]["engine"], "dataflow")
        self.assertEqual(
            result.run["stats"]["compiled_operators"],
            ["NormalizeMedicalTextOperator", "ChunkMedicalTextOperator"],
        )


if __name__ == "__main__":
    unittest.main()
