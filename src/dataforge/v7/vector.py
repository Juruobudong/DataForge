"""V7-owned Milvus synchronization scoped to ``kl_<library-id>`` partitions."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from .store import V7Store


class ManagedCollectionIncompatible(ValueError):
    """A same-name Milvus collection exists but violates its frozen contract."""


class EmbeddingProvider(Protocol):
    def embed(self, inputs: list[str], *, model: str, dimension: int) -> list[list[float]]: ...


class OpenAILikeEmbeddingProvider:
    """QA Agent-compatible OpenAILikeEmbedding boundary for V7 profiles."""
    def __init__(self, api_base: str, api_key: str, batch_size: int, *, embedding_factory=None):
        if embedding_factory is None:
            try:
                from llama_index.embeddings.openai_like import OpenAILikeEmbedding
            except ImportError as exc:
                raise RuntimeError("未安装 llama-index-embeddings-openai-like") from exc
            embedding_factory = OpenAILikeEmbedding
        self.api_base, self.api_key, self.batch_size = api_base, api_key, batch_size
        self._factory = embedding_factory

    def embed(self, inputs: list[str], *, model: str, dimension: int) -> list[list[float]]:
        provider = self._factory(
            model_name=model,
            api_base=self.api_base,
            api_key=self.api_key,
            embed_batch_size=self.batch_size,
        )
        values = provider.get_text_embedding_batch(inputs)
        if len(values) != len(inputs) or any(len(vector) != dimension for vector in values):
            raise RuntimeError(f"Embedding 服务没有返回 {len(inputs)} 个 {dimension} 维向量")
        return values


@dataclass(frozen=True)
class MilvusCapacity:
    collection_name: str
    entity_count: int | None
    capacity_limit: int | None
    threshold: float

    @property
    def alert(self) -> bool:
        return bool(self.capacity_limit and self.entity_count is not None and self.entity_count >= self.capacity_limit * self.threshold)


class V7Milvus:
    def __init__(self, uri: str, token: str | None = None, *, capacity_limit: int | None = None, capacity_threshold: float = 0.8):
        self.uri, self.token = uri, token
        self.capacity_limit, self.capacity_threshold = capacity_limit, capacity_threshold
        self._client = None

    def client(self):
        if self._client is None:
            from pymilvus import MilvusClient
            options: dict[str, Any] = {"uri": self.uri}
            if self.token:
                options["token"] = self.token
            self._client = MilvusClient(**options)
        return self._client

    @staticmethod
    def _assert_v7_partition(partition_name: str) -> None:
        if not partition_name.startswith("kl_"):
            raise ValueError("拒绝访问非 V7 知识库 Partition")

    def validate_collection(self, collection_name: str, fields: dict[str, Any], dimension: int) -> None:
        """Reject a missing or incompatible administrator-selected Collection.

        This method is the validate-only path for an external Collection.
        DataForge owns only its ``kl_`` partitions after validation succeeds.
        """
        client = self.client()
        if not client.has_collection(collection_name=collection_name):
            raise ValueError(f"Milvus Collection {collection_name} 不存在")
        description = client.describe_collection(collection_name=collection_name)
        schema_fields = description.get("fields", []) if isinstance(description, dict) else []
        names = {str(item.get("name")) for item in schema_fields if isinstance(item, dict)}
        required = {str(value) for value in fields.values()}
        if names and not required.issubset(names):
            raise ValueError("Milvus Collection 缺少 Index Profile 映射字段")
        vector_name = str(fields["vector"])
        vector_field = next((item for item in schema_fields if isinstance(item, dict) and item.get("name") == vector_name), None)
        params = vector_field.get("params", {}) if isinstance(vector_field, dict) else {}
        actual_dimension = params.get("dim") if isinstance(params, dict) else None
        if actual_dimension is not None and int(actual_dimension) != dimension:
            raise ValueError(f"Milvus 向量维度为 {actual_dimension}，与 Embedding 配置 {dimension} 不兼容")

    def ensure_managed_collection(self, collection_name: str, schema_spec: dict[str, Any], dimension: int,
                                  metric_type: str, index_spec: dict[str, Any], description: str) -> str:
        """Create one owned Collection or verify its immutable ownership marker."""
        client = self.client()
        if client.has_collection(collection_name=collection_name):
            current = client.describe_collection(collection_name=collection_name)
            observed = str(current.get("description", "")) if isinstance(current, dict) else ""
            if observed != description:
                return observed
            fields = {item["name"]: item["name"] for item in schema_spec.get("fields", [])}
            try:
                self.validate_collection(collection_name, fields, dimension)
            except ValueError as exc:
                raise ManagedCollectionIncompatible(str(exc)) from exc
            actual_fields = current.get("fields", []) if isinstance(current, dict) else []
            actual_names = {str(item.get("name")) for item in actual_fields if isinstance(item, dict)}
            expected_names = {str(item.get("name")) for item in schema_spec.get("fields", [])}
            if actual_names and actual_names != expected_names:
                raise ManagedCollectionIncompatible("Milvus Collection 字段集合与 Storage Contract 不一致")
            for expected in schema_spec.get("fields", []):
                actual = next((item for item in actual_fields if item.get("name") == expected.get("name")), {})
                params = actual.get("params") or actual.get("type_params") or {}
                if expected.get("max_length") and params.get("max_length") is not None \
                        and int(params["max_length"]) != int(expected["max_length"]):
                    raise ManagedCollectionIncompatible(f"Milvus 字段 {expected['name']} 长度与 Storage Contract 不一致")
            return observed
        from pymilvus import DataType
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False, description=description)
        type_map = {
            "VARCHAR": DataType.VARCHAR, "FLOAT_VECTOR": DataType.FLOAT_VECTOR,
            "JSON": DataType.JSON, "DOUBLE": DataType.DOUBLE, "INT64": DataType.INT64,
            "BOOL": DataType.BOOL,
        }
        for field in schema_spec.get("fields", []):
            kwargs: dict[str, Any] = {
                "field_name": field["name"], "datatype": type_map[field["type"]],
            }
            if field.get("primary"):
                kwargs["is_primary"] = True
            if field.get("nullable"):
                kwargs["nullable"] = True
            if field["type"] == "VARCHAR":
                kwargs["max_length"] = int(field["max_length"])
            if field["type"] == "FLOAT_VECTOR":
                kwargs["dim"] = dimension
            schema.add_field(**kwargs)
        indexes = client.prepare_index_params()
        indexes.add_index(field_name="vector", index_type=index_spec.get("index_type", "AUTOINDEX"), metric_type=metric_type)
        client.create_collection(collection_name=collection_name, schema=schema, index_params=indexes)
        return description

    def ensure_partition(self, collection_name: str, partition_name: str) -> None:
        self._assert_v7_partition(partition_name)
        client = self.client()
        if not client.has_partition(collection_name=collection_name, partition_name=partition_name):
            client.create_partition(collection_name=collection_name, partition_name=partition_name)

    def partition_exists(self, collection_name: str, partition_name: str) -> bool:
        self._assert_v7_partition(partition_name)
        client = self.client()
        return bool(client.has_collection(collection_name=collection_name) and
                    client.has_partition(collection_name=collection_name, partition_name=partition_name))

    def upsert(self, collection_name: str, partition_name: str, rows: list[dict[str, Any]]) -> None:
        self._assert_v7_partition(partition_name)
        self.client().upsert(collection_name=collection_name, partition_name=partition_name, data=rows)

    def load_partition(self, collection_name: str, partition_name: str) -> None:
        self._assert_v7_partition(partition_name)
        self.client().load_partitions(collection_name=collection_name, partition_names=[partition_name])

    def release_partition(self, collection_name: str, partition_name: str) -> None:
        self._assert_v7_partition(partition_name)
        self.client().release_partitions(collection_name=collection_name, partition_names=[partition_name])

    def drop_partition(self, collection_name: str, partition_name: str) -> None:
        """Drop one verified V7 library partition without touching its Collection.

        Milvus rejects dropping a partition that is currently loaded, so release it
        first. Releasing an already-released partition is a no-op; if the release
        itself fails we still attempt the drop so the underlying error surfaces.
        """
        self._assert_v7_partition(partition_name)
        client = self.client()
        if not client.has_partition(collection_name=collection_name, partition_name=partition_name):
            return
        try:
            client.release_partitions(collection_name=collection_name, partition_names=[partition_name])
        except Exception:
            pass
        client.drop_partition(collection_name=collection_name, partition_name=partition_name)

    def inspect_managed_collection(self, collection_name: str, expected_description: str) -> dict[str, Any]:
        """Read the facts required by the fail-closed managed deletion preflight."""
        client = self.client()
        if not client.has_collection(collection_name=collection_name):
            return {"exists": False, "ownership_valid": True, "partitions": [], "entity_count": 0}
        current = client.describe_collection(collection_name=collection_name)
        description = str(current.get("description", "")) if isinstance(current, dict) else ""
        # Partition enumeration is part of the deletion safety proof.  Fail
        # closed when Milvus cannot provide it instead of treating it as empty.
        partitions = list(client.list_partitions(collection_name=collection_name))
        capacity = self.capacity(collection_name)
        return {"exists": True, "description": description,
                "ownership_valid": description == expected_description,
                "partitions": partitions, "entity_count": capacity.entity_count}

    def drop_managed_collection(self, collection_name: str, expected_description: str) -> bool:
        """Drop only a Collection whose immutable DataForge ownership marker still matches."""
        observed = self.inspect_managed_collection(collection_name, expected_description)
        if not observed["exists"]:
            return False
        if not observed["ownership_valid"]:
            raise ValueError("Collection ownership marker 不匹配，拒绝删除")
        external = [name for name in observed.get("partitions", []) if name != "_default" and not str(name).startswith("kl_")]
        if external:
            raise ValueError("Collection 存在非 DataForge Partition，拒绝删除")
        self.client().drop_collection(collection_name=collection_name)
        return True

    def search(self, collection_name: str, partition_name: str, vector: list[float], limit: int = 10) -> Any:
        self._assert_v7_partition(partition_name)
        return self.client().search(collection_name=collection_name, partition_names=[partition_name], data=[vector], anns_field="vector", limit=limit, output_fields=["knowledge_library_id", "source_knowledge_id"])

    def capacity(self, collection_name: str) -> MilvusCapacity:
        try:
            stats = self.client().get_collection_stats(collection_name=collection_name)
            value = stats.get("row_count") or stats.get("num_entities") if isinstance(stats, dict) else None
            count = int(value) if value is not None else None
        except Exception:
            count = None
        return MilvusCapacity(collection_name, count, self.capacity_limit, self.capacity_threshold)

    def delete(self, collection_name: str, partition_name: str, field_name: str, vector_ids: list[str]) -> None:
        self._assert_v7_partition(partition_name)
        if not vector_ids:
            return
        expr = f'{field_name} in [{", ".join(json.dumps(item) for item in vector_ids)}]'
        self.client().delete(collection_name=collection_name, partition_name=partition_name, filter=expr)

    def _primary_field(self, collection_name: str) -> str:
        description = self.client().describe_collection(collection_name=collection_name)
        for field in description.get("fields", []) if isinstance(description, dict) else []:
            if field.get("is_primary") or field.get("primary"):
                return str(field["name"])
        return "id"

    def iter_partition(self, collection_name: str, partition_name: str,
                       batch_size: int = 1000) -> Iterable[list[dict[str, Any]]]:
        self._assert_v7_partition(partition_name)
        client = self.client()
        if not client.has_partition(collection_name=collection_name, partition_name=partition_name):
            raise ValueError(f"Milvus Partition {partition_name} 不存在")
        iterator_factory = getattr(client, "query_iterator", None)
        if iterator_factory:
            iterator = iterator_factory(collection_name=collection_name, partition_names=[partition_name],
                                        filter="", output_fields=["*"], batch_size=batch_size)
            try:
                while True:
                    rows = iterator.next()
                    if not rows: break
                    yield [dict(row) for row in rows]
            finally:
                close = getattr(iterator, "close", None)
                if close: close()
            return
        offset = 0
        while True:
            rows = client.query(collection_name=collection_name, partition_names=[partition_name], filter="",
                                output_fields=["*"], limit=batch_size, offset=offset)
            if not rows: break
            yield [dict(row) for row in rows]
            if len(rows) < batch_size: break
            offset += len(rows)

    @staticmethod
    def _row_digest(rows: Iterable[dict[str, Any]], primary_field: str) -> str:
        digest = hashlib.sha256()
        for row in sorted(rows, key=lambda item: str(item.get(primary_field, ""))):
            digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                     default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value)).encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    @staticmethod
    def _update_row_digest(digest, row: dict[str, Any]) -> None:
        digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                 default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value)).encode("utf-8"))
        digest.update(b"\n")

    @contextmanager
    def _sorted_partition_spool(self, collection_name: str, partition_name: str,
                                primary_field: str, batch_size: int, parent: Path):
        """Spill a Partition to disk and expose rows in stable primary-key order."""
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="dataforge-vector-sort-", dir=parent) as temporary:
            connection = sqlite3.connect(str(Path(temporary) / "rows.sqlite3"))
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("CREATE TABLE rows (sort_key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            count, json_fields = 0, set()
            try:
                for batch in self.iter_partition(collection_name, partition_name, batch_size):
                    values = []
                    for row in batch:
                        sort_key = str(row.get(primary_field, ""))
                        if not sort_key: raise ValueError(f"Partition row 缺少主键字段 {primary_field}")
                        json_fields.update(key for key, value in row.items()
                                           if key != "vector" and isinstance(value, (dict, list)))
                        payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                             default=lambda value: value.tolist() if hasattr(value, "tolist") else str(value))
                        values.append((sort_key, payload)); count += 1
                    try:
                        connection.executemany("INSERT INTO rows(sort_key, payload) VALUES (?, ?)", values)
                    except sqlite3.IntegrityError as exc:
                        raise ValueError(f"Partition {partition_name} 存在重复主键") from exc
                connection.commit()
                yield connection, count, sorted(json_fields)
            finally:
                connection.close()

    def export_partition(self, collection_name: str, partition_name: str,
                         output_path: Path, batch_size: int = 1000) -> dict[str, Any]:
        import pyarrow as pa
        import pyarrow.parquet as pq
        primary_field = self._primary_field(collection_name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with self._sorted_partition_spool(collection_name, partition_name, primary_field,
                                          batch_size, output_path.parent) as (spool, count, json_fields):
            cursor = spool.execute("SELECT payload FROM rows ORDER BY sort_key")
            writer = None
            try:
                while raw_rows := cursor.fetchmany(batch_size):
                    rows = [json.loads(raw[0]) for raw in raw_rows]
                    for row in rows: self._update_row_digest(digest, row)
                    encoded = [{key: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                                if key in json_fields and value is not None else value
                                for key, value in row.items()} for row in rows]
                    table = pa.Table.from_pylist(encoded, schema=writer.schema if writer else None)
                    if writer is None:
                        metadata = dict(table.schema.metadata or {})
                        metadata[b"dataforge_primary_field"] = primary_field.encode("utf-8")
                        metadata[b"dataforge_json_fields"] = json.dumps(json_fields).encode("utf-8")
                        table = table.replace_schema_metadata(metadata)
                        writer = pq.ParquetWriter(output_path, table.schema, compression="zstd")
                    writer.write_table(table)
                if writer is None:
                    empty = pa.table({primary_field: pa.array([], type=pa.string())})
                    metadata = {b"dataforge_primary_field": primary_field.encode("utf-8"),
                                b"dataforge_json_fields": b"[]"}
                    writer = pq.ParquetWriter(output_path, empty.replace_schema_metadata(metadata).schema,
                                              compression="zstd")
            finally:
                if writer is not None: writer.close()
        return {"collection_name": collection_name, "partition_name": partition_name,
                "count": count, "primary_field": primary_field, "digest": digest.hexdigest()}

    def reset_partition(self, collection_name: str, partition_name: str) -> None:
        self._assert_v7_partition(partition_name)
        self.drop_partition(collection_name, partition_name)
        self.ensure_partition(collection_name, partition_name)

    def import_partition(self, collection_name: str, partition_name: str,
                         input_path: Path, batch_size: int = 1000) -> dict[str, Any]:
        import pyarrow.parquet as pq
        parquet = pq.ParquetFile(input_path)
        metadata = parquet.schema_arrow.metadata or {}
        primary_field = metadata.get(b"dataforge_primary_field", b"id").decode("utf-8")
        json_fields = set(json.loads(metadata.get(b"dataforge_json_fields", b"[]")))
        count, digest = 0, hashlib.sha256()
        for batch in parquet.iter_batches(batch_size=batch_size):
            rows = batch.to_pylist()
            decoded = [{key: json.loads(value) if key in json_fields and value is not None else value
                        for key, value in row.items()} for row in rows]
            if decoded: self.upsert(collection_name, partition_name, decoded)
            for row in decoded: self._update_row_digest(digest, row)
            count += len(decoded)
        return {"count": count, "primary_field": primary_field,
                "digest": digest.hexdigest()}

    def count_partition(self, collection_name: str, partition_name: str) -> int:
        return sum(len(batch) for batch in self.iter_partition(collection_name, partition_name))

    def verify_partition(self, collection_name: str, partition_name: str,
                         expected_count: int | None = None, expected_digest: str | None = None,
                         primary_field: str | None = None) -> dict[str, Any]:
        primary_field = primary_field or self._primary_field(collection_name)
        digest = hashlib.sha256()
        with self._sorted_partition_spool(collection_name, partition_name, primary_field, 1000,
                                          Path(tempfile.gettempdir())) as (spool, count, _):
            cursor = spool.execute("SELECT payload FROM rows ORDER BY sort_key")
            while raw_rows := cursor.fetchmany(1000):
                for raw in raw_rows: self._update_row_digest(digest, json.loads(raw[0]))
        actual_digest = digest.hexdigest()
        if expected_count is None and expected_digest is None:
            return {"count": count, "digest": actual_digest, "primary_field": primary_field}
        valid = count == expected_count and actual_digest == expected_digest
        return {"valid": valid, "expected_count": expected_count, "target_count": count,
                "expected_digest": expected_digest, "target_digest": actual_digest}


def vector_id(profile_code: str, knowledge_library_id: str, source_knowledge_id: str) -> str:
    """Stable V7 vector key: profile + library + business source knowledge id."""
    return hashlib.sha256(f"{profile_code}|{knowledge_library_id}|{source_knowledge_id}".encode("utf-8")).hexdigest()


class VectorSyncService:
    def __init__(self, store: V7Store, *, milvus: V7Milvus | None = None, embeddings: EmbeddingProvider | None = None):
        self.store = store
        self.milvus = milvus
        self.embeddings = embeddings

    @classmethod
    def from_environment(cls, store: V7Store) -> "VectorSyncService":
        uri = os.getenv("DATAFORGE_MILVUS_URI")
        api_base = os.getenv("EMBEDDING_API_BASE")
        if not uri or not api_base:
            return cls(store)
        try:
            batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
        except ValueError as exc:
            raise ValueError("EMBEDDING_BATCH_SIZE 必须是整数") from exc
        if batch_size <= 0:
            raise ValueError("EMBEDDING_BATCH_SIZE 必须为正整数")
        capacity_limit = int(os.getenv("DATAFORGE_COLLECTION_CAPACITY_LIMIT", "0")) or None
        threshold = float(os.getenv("DATAFORGE_COLLECTION_CAPACITY_THRESHOLD", "0.8"))
        return cls(store, milvus=V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN"), capacity_limit=capacity_limit, capacity_threshold=threshold), embeddings=OpenAILikeEmbeddingProvider(api_base, os.getenv("EMBEDDING_API_KEY", "fake"), batch_size))

    @staticmethod
    def _input(profile_code: str, item) -> str:
        if profile_code == "qa-question":
            return str((item.data_json or {}).get("question", ""))
        if profile_code == "qa-full":
            data = item.data_json or {}
            return f"{data.get('question', '')}\n{data.get('answer', '')}".strip()
        return item.canonical_content

    @staticmethod
    def _materialized_graph_fields(graph_mode: str | None, data: dict[str, Any]) -> dict[str, Any]:
        if graph_mode == "triple":
            return {key: data.get(key) for key in ("subject", "predicate", "object", "subject_type", "object_type")}
        if graph_mode == "semantic":
            source, target, relation = (data.get(key) or {} for key in ("source_entity", "target_entity", "relation"))
            return {
                "source_entity_name": source.get("name"), "source_entity_type": source.get("type"),
                "source_entity_description": source.get("description"), "target_entity_name": target.get("name"),
                "target_entity_type": target.get("type"), "target_entity_description": target.get("description"),
                "relation_description": relation.get("description"), "relation_keywords": relation.get("keywords"),
                "relation_weight": relation.get("weight"), "evidence": data.get("evidence"),
            }
        return {}

    @staticmethod
    def _materialized_profile_fields(profile_code: str, data: dict[str, Any]) -> dict[str, Any]:
        if profile_code == "qa-question":
            return {"question": data.get("question")}
        if profile_code == "qa-full":
            return {"question": data.get("question"), "answer": data.get("answer")}
        return {}

    def run(self, sync_job_id: str) -> dict[str, Any]:
        context = self.store.vector_sync_context(sync_job_id)
        if not self.milvus or not self.embeddings:
            return self.store.finish_vector_sync(sync_job_id, [], "未配置 DATAFORGE_MILVUS_URI 或 EMBEDDING_API_BASE，不能标记 Vector Ready")
        job, library, profile, embedding, items = context["job"], context["library"], context["profile"], context["embedding"], context["items"]
        try:
            self.milvus.validate_collection(profile.collection_name, profile.fields_json, embedding.dimension)
            self.milvus.ensure_partition(profile.collection_name, library.partition_name)
            inputs = [self._input(profile.code, item) for item in items]
            vectors = self.embeddings.embed(inputs, model=embedding.model, dimension=embedding.dimension)
            rows = []
            states = []
            for item, vector in zip(items, vectors, strict=True):
                stable_id = vector_id(profile.code, library.id, item.source_knowledge_id)
                fields = profile.fields_json
                row = {fields["id"]: stable_id, fields["vector"]: vector,
                    fields["knowledge_library_id"]: library.id, fields["source_knowledge_id"]: item.source_knowledge_id,
                    fields["content"]: item.canonical_content, fields["data"]: item.data_json}
                row.update({key: value for key, value in self._materialized_profile_fields(profile.code, item.data_json or {}).items() if value is not None})
                row.update({key: value for key, value in self._materialized_graph_fields(library.graph_mode, item.data_json or {}).items() if value is not None})
                rows.append(row)
                states.append({"knowledge_item_id": item.id, "vector_id": stable_id, "content_hash": item.content_hash})
            if rows:
                self.milvus.upsert(profile.collection_name, library.partition_name, rows)
            return self.store.finish_vector_sync(job.id, states)
        except Exception as exc:
            return self.store.finish_vector_sync(job.id, [], str(exc))

    def capacity_report(self) -> list[dict[str, Any]]:
        profiles = self.store.list_index_profiles()
        active = [item for item in profiles if item["status"] == "active"]
        skipped = [{
            "collection_name": item["collection_name"],
            "available": False,
            "reason": "旧外部 Profile，不参与容量监控",
        } for item in active if item["code"] == "graph"]
        names = sorted({item["collection_name"] for item in active if item["code"] != "graph"})
        if not self.milvus:
            return skipped + [{"collection_name": name, "available": False, "reason": "Milvus 未配置"} for name in names]
        checked = [{"collection_name": item.collection_name, "entity_count": item.entity_count, "capacity_limit": item.capacity_limit, "threshold": item.threshold, "alert": item.alert, "available": True} for item in (self.milvus.capacity(name) for name in names)]
        return skipped + checked


class VectorDeletionService:
    def __init__(self, store: V7Store, milvus: V7Milvus | None = None):
        self.store, self.milvus = store, milvus

    @classmethod
    def from_environment(cls, store: V7Store) -> "VectorDeletionService":
        uri = os.getenv("DATAFORGE_MILVUS_URI")
        return cls(store, V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN")) if uri else None)

    def run(self, deletion_job_id: str) -> dict[str, Any]:
        try:
            context = self.store.vector_deletion_context(deletion_job_id)
            if not self.milvus:
                return self.store.finish_vector_deletion(deletion_job_id, "未配置 DATAFORGE_MILVUS_URI，不能清理向量记录")
            self.milvus.delete(context["profile"].collection_name, context["library"].partition_name,
                               context["profile"].fields_json["id"], context["job"].vector_ids)
            return self.store.finish_vector_deletion(deletion_job_id)
        except Exception as exc:
            return self.store.finish_vector_deletion(deletion_job_id, str(exc))


class LibraryDeletionService:
    """Physical vector cleanup for a soft-deleted V7 knowledge library."""
    def __init__(self, store: V7Store, milvus: V7Milvus | None = None):
        self.store, self.milvus = store, milvus

    @classmethod
    def from_environment(cls, store: V7Store) -> "LibraryDeletionService":
        uri = os.getenv("DATAFORGE_MILVUS_URI")
        if not uri:
            return cls(store)
        return cls(store, V7Milvus(uri, os.getenv("DATAFORGE_MILVUS_TOKEN")))

    def run(self, deletion_job_id: str) -> dict[str, Any]:
        try:
            context = self.store.library_deletion_context(deletion_job_id)
            if not self.milvus:
                return self.store.finish_library_deletion(deletion_job_id, "未配置 DATAFORGE_MILVUS_URI，不能清理 V7 Partition")
            library = context["library"]
            for profile in context["profiles"]:
                self.milvus.drop_partition(profile.collection_name, library.partition_name)
            return self.store.finish_library_deletion(deletion_job_id)
        except Exception as exc:
            return self.store.finish_library_deletion(deletion_job_id, str(exc))
