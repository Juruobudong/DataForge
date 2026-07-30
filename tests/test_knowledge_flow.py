from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dataforge.application import DataForge
from dataforge.config import Settings
from dataforge.errors import ValidationError
from dataforge.knowledge import KnowledgeService, validate_record


class KnowledgeFlowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = DataForge(
            Settings(project_root=self.root, state_dir=self.root / ".dataforge", dataflow_path=None)
        )
        self.service = KnowledgeService(self.app)

    def tearDown(self):
        self.temporary.cleanup()

    def _ingest(self, name: str, text: str) -> str:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return self.app.sources.ingest(path, name=path.stem).source_version["id"]

    def test_parallel_job_creates_traceable_knowledge_base(self):
        first = self._ingest("guide-a.txt", "高血压患者应定期监测血压并遵医嘱复诊。")
        second = self._ingest("guide-b.md", "# 随访建议\n\n记录症状变化，出现异常时及时就医。")

        job = self.service.create_job(
            name="临床指南文本库",
            knowledge_type_id="text_chunk",
            standard_pipeline_id="std-text-chunk-v1",
            source_version_ids=[first, second],
        )
        finished = self.service.execute_job(job["id"])

        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["progress"], 100)
        self.assertTrue(finished["validation"]["passed"])
        base = self.app.store.get_knowledge_base(finished["knowledge_base_id"])
        self.assertEqual(base["knowledge_type_id"], "text_chunk")
        records = self.app.store.list_knowledge_records(base["id"])
        self.assertEqual({record["source_version_id"] for record in records}, {first, second})
        lineage = self.service.get_record_lineage(records[0]["id"])
        self.assertEqual(lineage["knowledge_base_name"], "临床指南文本库")
        self.assertIn("chunk_index", lineage["source_locator"])
        self.assertTrue(lineage["source_locator"]["source_excerpt"])

        self.assertEqual(self.app.store.count_knowledge_records(base["id"]), len(records))
        searched = self.app.store.list_knowledge_records(base["id"], limit=10, query="高血压")
        self.assertEqual(len(searched), 1)
        self.assertIn("高血压", searched[0]["data"]["content"])

    def test_incompatible_or_unvalidated_pipeline_is_rejected(self):
        source = self._ingest("faq.txt", "如何复诊？请通过门诊预约。")
        with self.assertRaisesRegex(ValidationError, "不兼容"):
            self.service.create_job(
                name="错误组合",
                knowledge_type_id="faq",
                standard_pipeline_id="std-text-chunk-v1",
                source_version_ids=[source],
            )
        with self.assertRaisesRegex(ValidationError, "尚未通过"):
            self.service.create_job(
                name="待验证组合",
                knowledge_type_id="faq",
                standard_pipeline_id="std-faq-text2qa-v1",
                source_version_ids=[source],
            )

    def test_default_pipeline_is_resolved_by_knowledge_type(self):
        source = self._ingest("guide.txt", "患者应遵医嘱定期复诊。")
        job = self.service.create_job(
            name="自动匹配流程",
            knowledge_type_id="text_chunk",
            standard_pipeline_id=None,
            source_version_ids=[source],
        )

        self.assertEqual(job["standard_pipeline_id"], "std-text-chunk-v1")

    def test_fixed_schema_validation(self):
        schema = self.app.store.get_knowledge_type("knowledge_triple")["schema"]
        self.assertEqual(validate_record({"subject": "A", "predicate": "属于", "object": "B"}, schema), [])
        self.assertEqual(validate_record({"subject": "A", "predicate": "属于"}, schema), ["缺少字段：object"])


if __name__ == "__main__":
    unittest.main()
