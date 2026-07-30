from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from dataforge.config import Settings
from dataforge.web import create_app


class DataForgeWebTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        settings = Settings(
            project_root=self.root,
            state_dir=self.root / ".dataforge",
            dataflow_path=None,
        )
        self.client = TestClient(create_app(settings))

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def test_web_api_covers_source_to_asset_lifecycle(self):
        uploaded = self.client.post(
            "/api/sources",
            data={"name": "门诊随访", "kind": "medical_document"},
            files={"file": ("follow-up.txt", "患者血压稳定。\n建议一个月后复诊。", "text/plain")},
        )
        self.assertEqual(uploaded.status_code, 201)
        ingestion = uploaded.json()
        self.assertEqual(ingestion["source_version"]["original_filename"], "follow-up.txt")

        sources = self.client.get("/api/sources").json()
        self.assertEqual(sources[0]["version_count"], 1)
        self.assertEqual(len(sources[0]["versions"]), 1)

        started = self.client.post(
            "/api/runs",
            json={
                "source_version_id": ingestion["source_version"]["id"],
                "pipeline_id": "medical-document-v1",
                "engine": "native",
            },
        )
        self.assertEqual(started.status_code, 202)
        run_id = started.json()["id"]
        run_detail = self.client.get(f"/api/runs/{run_id}").json()
        self.assertEqual(run_detail["run"]["status"], "completed")
        self.assertEqual(run_detail["events"][-1]["event_type"], "completed")

        assets = self.client.get("/api/assets").json()
        self.assertEqual(len(assets), 1)
        asset_id = assets[0]["id"]
        versions = self.client.get(f"/api/assets/{asset_id}/versions").json()
        version_id = versions[0]["id"]

        preview = self.client.get(f"/api/asset-versions/{version_id}/preview").json()
        self.assertGreater(len(preview), 0)
        lineage = self.client.get(f"/api/asset-versions/{version_id}/lineage").json()
        self.assertEqual(lineage["run_id"], run_id)
        download = self.client.get(f"/api/asset-versions/{version_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertIn("患者血压稳定", download.text)

        dashboard = self.client.get("/api/dashboard").json()
        self.assertEqual(dashboard["counts"], {"sources": 1, "source_versions": 1, "runs": 1, "assets": 1})
        self.assertEqual(dashboard["run_summary"]["completed"], 1)

    def test_api_returns_structured_validation_errors(self):
        response = self.client.post(
            "/api/runs",
            json={"source_version_id": "missing", "engine": "native"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "NotFoundError")

    def test_knowledge_catalog_filters_compatible_standard_pipelines(self):
        types = self.client.get("/api/knowledge-types")
        self.assertEqual(types.status_code, 200)
        self.assertEqual(
            {item["id"] for item in types.json()},
            {"text_chunk", "faq", "knowledge_triple", "multi_turn_dialogue"},
        )

        text_pipelines = self.client.get(
            "/api/standard-pipelines", params={"knowledge_type_id": "text_chunk"}
        ).json()
        self.assertEqual([item["id"] for item in text_pipelines], ["std-text-chunk-v1"])
        self.assertEqual(text_pipelines[0]["validation_status"], "validated")

        triple_pipelines = self.client.get(
            "/api/standard-pipelines", params={"knowledge_type_id": "knowledge_triple"}
        ).json()
        self.assertEqual(triple_pipelines, [])

    def test_business_job_uses_default_published_pipeline(self):
        uploaded = self.client.post(
            "/api/sources",
            files={"file": ("guide.txt", "患者应按时复诊并记录症状。", "text/plain")},
        ).json()

        started = self.client.post(
            "/api/knowledge-jobs",
            json={
                "name": "复诊指南知识库",
                "knowledge_type_id": "text_chunk",
                "source_version_ids": [uploaded["source_version"]["id"]],
            },
        )

        self.assertEqual(started.status_code, 202)
        job = self.client.get(f"/api/knowledge-jobs/{started.json()['id']}").json()
        self.assertEqual(job["standard_pipeline_id"], "std-text-chunk-v1")
        self.assertEqual(job["status"], "completed")

    def test_knowledge_type_can_be_configured_without_frontend_changes(self):
        created = self.client.post(
            "/api/knowledge-types",
            json={
                "name": "术语知识库",
                "description": "保存术语及其解释。",
                "schema": {
                    "type": "object",
                    "required": ["term", "definition"],
                    "properties": {"term": "string", "definition": "string"},
                },
            },
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "术语知识库")
        self.assertIn(
            created.json()["id"],
            {item["id"] for item in self.client.get("/api/knowledge-types").json()},
        )

    def test_dataflow_studio_frontend_is_mounted_separately(self):
        project_root = Path(__file__).resolve().parents[1]
        client = TestClient(
            create_app(
                Settings(
                    project_root=project_root,
                    state_dir=self.root / ".studio-test",
                    dataflow_path=None,
                )
            )
        )
        response = client.get("/studio/")
        client.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Vite App", response.text)
        self.assertNotIn("DataForge 知识生产平台", response.text)


if __name__ == "__main__":
    unittest.main()
