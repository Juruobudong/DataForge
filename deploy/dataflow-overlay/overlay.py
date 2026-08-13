"""DataForge-owned boundary around the pinned DataFlow-WebUI v1 service.

It deliberately contains no upstream source code.  The Overlay supplies the
few contract additions DataForge needs and forwards the visual WebUI only after
validating the same-origin embed session.  Upstream data, databases and files
are never accessed by DataForge business modules.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response


UPSTREAM_URL = os.getenv("DATAFLOW_UPSTREAM_URL", "http://dataflow-webui:8000").rstrip("/")
DATAFORGE_API_URL = os.getenv("DATAFORGE_API_URL", "http://dataforge-api:8000").rstrip("/")
SERVICE_TOKEN = os.getenv("DATAFLOW_SERVICE_TOKEN", "")
SERVICE_TOKEN_FILE = os.getenv("DATAFLOW_SERVICE_TOKEN_FILE")
RUNS_FILE = Path(os.getenv("DATAFLOW_OVERLAY_STATE", "/var/lib/dataflow-overlay/runs.json"))
EMBED_COOKIE = "dataforge_embed_session"
DENIED_PARAMETER_NAMES = {
    "code", "process_fn", "filter_rules", "shell", "command", "cmd", "path", "file_path",
    "filepath", "url", "uri", "endpoint", "host", "network", "script",
}
DENIED_CAPABILITIES = {"dynamic_python", "shell_execution", "arbitrary_network", "arbitrary_file_path"}
BLOCKED_BROWSER_API_PREFIXES = ("datasets", "serving", "text2sql_database", "text2sql_database_manager")

if SERVICE_TOKEN_FILE and not SERVICE_TOKEN:
    SERVICE_TOKEN = Path(SERVICE_TOKEN_FILE).read_text(encoding="utf-8").strip()

app = FastAPI(title="DataForge DataFlow v1 Overlay", version="1.0.0")


def _envelope_data(payload: Any) -> Any:
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


def _service_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"} if SERVICE_TOKEN else {}


async def _dataforge(method: str, path: str, **kwargs: Any) -> httpx.Response:
    caller_headers = kwargs.pop("headers", {})
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            f"{DATAFORGE_API_URL}{path}",
            headers={**_service_headers(), **caller_headers},
            **kwargs,
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"DataForge governance callback failed: {response.text}")
    return response


async def _upstream(method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        return await client.request(method, f"{UPSTREAM_URL}{path}", **kwargs)


def _require_service(request: Request) -> None:
    expected = f"Bearer {SERVICE_TOKEN}" if SERVICE_TOKEN else ""
    if not expected or not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
        raise HTTPException(status_code=401, detail="DataFlow Overlay service authentication required")


async def _require_embed_session(request: Request) -> None:
    token = request.cookies.get(EMBED_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="DataFlow iframe session is required")
    response = await _dataforge("GET", "/api/auth/embed-status", headers={"X-DataForge-Embed-Session": token})
    if _envelope_data(response.json()).get("authenticated") is not True:
        raise HTTPException(status_code=401, detail="DataFlow iframe session is invalid")


def _contains_forbidden_parameter(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).strip().lower().replace("-", "_") in DENIED_PARAMETER_NAMES:
                return True
            if _contains_forbidden_parameter(nested):
                return True
    if isinstance(value, list):
        return any(_contains_forbidden_parameter(item) for item in value)
    return False


async def _policy() -> dict[str, Any]:
    return (await _dataforge("GET", "/api/dataflow/operator-policy")).json()


def _permits(policy: dict[str, Any], operator: dict[str, Any]) -> bool:
    value = policy.get("policy", policy)
    capabilities = {str(item) for item in operator.get("capabilities", [])}
    denied = {str(item) for item in value.get("denied_capabilities", DENIED_CAPABILITIES)}
    if capabilities & denied:
        return False
    rules = [item for item in value.get("rules", []) if isinstance(item, dict)]
    return not rules or any(rule.get("operator_name") == operator.get("name") for rule in rules)


async def _upstream_operators() -> list[dict[str, Any]]:
    response = await _upstream("GET", "/api/v1/operators/", params={"lang": "zh"})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="DataFlow operator registry is unavailable")
    payload = _envelope_data(response.json())
    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="DataFlow operator registry has an invalid contract")
    return [item for item in payload if isinstance(item, dict)]


async def _assert_pipeline_allowed(raw_pipeline: dict[str, Any]) -> None:
    policy = await _policy()
    allowed = {str(item.get("name")): item for item in await _upstream_operators() if _permits(policy, item)}
    config = raw_pipeline.get("config") if isinstance(raw_pipeline.get("config"), dict) else raw_pipeline
    operators = config.get("operators") or config.get("nodes") or []
    if not isinstance(operators, list):
        raise HTTPException(status_code=400, detail="DataFlow pipeline operators must be a list")
    for item in operators:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="DataFlow pipeline operator must be an object")
        operator = item.get("operator") if isinstance(item.get("operator"), dict) else item
        name = str(operator.get("name") or operator.get("cls_name") or "")
        if not name or name not in allowed:
            raise HTTPException(status_code=403, detail=f"Operator is not permitted by DataForge policy: {name or 'unknown'}")
        if _contains_forbidden_parameter(item.get("params") or item.get("parameters") or {}):
            raise HTTPException(status_code=403, detail=f"Operator parameters violate DataForge policy: {name}")


def _load_runs() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_runs(value: dict[str, dict[str, Any]]) -> None:
    RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    pending = RUNS_FILE.with_suffix(".pending")
    pending.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    pending.replace(RUNS_FILE)


def _inject_embed_assets(html: str) -> str:
    # The upstream v1 build uses root-relative URLs.  Prefix them so it stays
    # inside /dataflow/ and cannot call the DataForge API directly.
    html = html.replace('"/assets/', '"/dataflow/assets/').replace("'/assets/", "'/dataflow/assets/")
    html = html.replace('"/api/v1/', '"/dataflow/api/v1/').replace("'/api/v1/", "'/dataflow/api/v1/")
    guard = """
<style id="dataforge-overlay-style">
  a[href*="dbManager"], a[href*="settings"], a[href*="serving"],
  a[href*="text2sql"], a[href*="datasets"] { display:none !important; }
</style>
<script>
(() => {
  const origin = window.location.origin;
  window.addEventListener('message', (event) => {
    if (event.origin !== origin || !event.data || event.data.type !== 'dataforge.export-pipeline') return;
    fetch('/dataflow/api/v1/dataforge/drafts', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(event.data.payload)
    }).then(r => r.json()).then(payload => {
      window.parent.postMessage({type: 'dataforge.draft-created', payload}, origin);
    }).catch(error => window.parent.postMessage({type: 'dataforge.draft-error', message: String(error)}, origin));
  });
  window.parent.postMessage({type: 'dataforge.iframe-ready', version: 'v1.0.0'}, origin);
})();
</script>
"""
    return html.replace("</head>", f"{guard}</head>")


def _response_from_upstream(response: httpx.Response, *, rewrite: bool = False) -> Response:
    headers = {key: value for key, value in response.headers.items() if key.lower() in {"content-type", "cache-control"}}
    content = response.content
    content_type = response.headers.get("content-type", "")
    if rewrite and ("text/html" in content_type or "javascript" in content_type):
        text = content.decode("utf-8")
        if "text/html" in content_type:
            text = _inject_embed_assets(text)
        else:
            text = text.replace('"/api/v1/', '"/dataflow/api/v1/').replace("'/api/v1/", "'/dataflow/api/v1/")
        content = text.encode("utf-8")
    headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return Response(content=content, status_code=response.status_code, headers=headers)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "upstream": UPSTREAM_URL, "baseline": "v1.0.0"}


@app.get("/api/v1/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    _require_service(request)
    operators = await _upstream_operators()
    policy = await _policy()
    return {"data": {"baseline": "v1.0.0", "operator_count": len([item for item in operators if _permits(policy, item)])}}


@app.get("/api/v1/operators/")
async def operators(request: Request, lang: str = "zh") -> dict[str, Any]:
    _require_service(request)
    policy = await _policy()
    items = [item for item in await _upstream_operators() if _permits(policy, item)]
    return {"data": items}


@app.get("/api/v1/operators/{name}")
async def operator_detail(name: str, request: Request) -> dict[str, Any]:
    _require_service(request)
    policy = await _policy()
    response = await _upstream("GET", f"/api/v1/operators/details/{name}", params={"lang": "zh"})
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail="Operator not found")
    payload = _envelope_data(response.json())
    if not isinstance(payload, dict) or not _permits(policy, payload):
        raise HTTPException(status_code=404, detail="Operator is not permitted")
    return {"data": payload}


@app.get("/api/v1/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str, request: Request) -> dict[str, Any]:
    _require_service(request)
    response = await _upstream("GET", f"/api/v1/pipelines/{pipeline_id}")
    return JSONResponse(status_code=response.status_code, content=response.json())


@app.post("/api/v1/pipelines/validate")
async def validate_pipeline(request: Request) -> dict[str, Any]:
    _require_service(request)
    raw = await request.json()
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Pipeline must be an object")
    await _assert_pipeline_allowed(raw)
    response = await _upstream("POST", "/api/v1/pipelines/validate", json=raw)
    return JSONResponse(status_code=response.status_code, content=response.json())


@app.post("/api/v1/tasks/governed-runs")
async def submit_governed_run(request: Request) -> dict[str, Any]:
    _require_service(request)
    payload = await request.json()
    snapshot = payload.get("execution_snapshot") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=400, detail="execution_snapshot is required")
    raw_pipeline = snapshot.get("dataflow_raw_snapshot")
    if not isinstance(raw_pipeline, dict):
        raise HTTPException(status_code=400, detail="DataFlow pipeline snapshot is required")
    await _assert_pipeline_allowed(raw_pipeline)
    snapshot_id = str(snapshot.get("id") or "")
    idempotency_key = str(request.headers.get("Idempotency-Key") or snapshot_id)
    if not snapshot_id or not idempotency_key:
        raise HTTPException(status_code=400, detail="execution snapshot and Idempotency-Key are required")
    runs = _load_runs()
    for existing_run_id, existing_run in runs.items():
        if existing_run.get("idempotency_key") != idempotency_key:
            continue
        if existing_run.get("execution_snapshot_sha256") != snapshot.get("sha256"):
            raise HTTPException(status_code=409, detail="Idempotency-Key was already used for another snapshot")
        return {"data": {"run_id": existing_run_id}}
    # Fetching this manifest is intentional: the Overlay gets only a five-minute,
    # job-scoped data URL, then uploads that one JSONL file to the upstream registry.
    inputs = (await _dataforge("GET", f"/api/dataflow/execution-snapshots/{snapshot_id}/inputs")).json()
    records_url = inputs.get("records_url")
    if not isinstance(records_url, str):
        raise HTTPException(status_code=400, detail="The execution snapshot lacks protected input metadata")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        protected_records = await client.get(records_url)
    if protected_records.status_code >= 400:
        raise HTTPException(status_code=502, detail="DataForge protected input download failed")
    raw_input = await _upstream(
        "POST",
        "/api/v1/datasets/upload",
        params={"name": f"dataforge-{snapshot_id}"},
        files={"file": (f"{snapshot_id}.jsonl", protected_records.content, "application/x-ndjson")},
    )
    if raw_input.status_code >= 400:
        raise HTTPException(status_code=502, detail="DataFlow rejected protected input dataset")
    dataset = _envelope_data(raw_input.json())
    pipeline = json.loads(json.dumps(raw_pipeline))
    config = pipeline.setdefault("config", {})
    config["input_dataset"] = dataset.get("id")
    config["file_path"] = "dataforge-protected-input"
    pipeline.pop("id", None)
    pipeline.setdefault("name", f"DataForge {snapshot_id}")
    created = await _upstream("POST", "/api/v1/pipelines/", json=pipeline)
    if created.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"DataFlow rejected pipeline: {created.text}")
    pipeline_id = _envelope_data(created.json()).get("id")
    started = await _upstream("POST", "/api/v1/tasks/execute-async", params={"pipeline_id": pipeline_id})
    if started.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"DataFlow did not start pipeline: {started.text}")
    run_id = str(_envelope_data(started.json()).get("task_id") or "")
    if not run_id:
        raise HTTPException(status_code=502, detail="DataFlow execution response lacked task_id")
    runs[run_id] = {
        "idempotency_key": idempotency_key,
        "execution_snapshot_sha256": snapshot.get("sha256"),
        "policy_sha256": snapshot.get("policy_sha256"),
        "source_version_ids": snapshot.get("source_version_ids", []),
        "artifact_type": snapshot.get("knowledge_type_id"),
        "pipeline_id": pipeline_id,
    }
    _save_runs(runs)
    return {"data": {"run_id": run_id}}


@app.get("/api/v1/tasks/governed-runs/{run_id}")
async def governed_status(run_id: str, request: Request) -> dict[str, Any]:
    _require_service(request)
    response = await _upstream("GET", f"/api/v1/tasks/execution/{run_id}/status")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="DataFlow status is unavailable")
    return {"data": _envelope_data(response.json())}


@app.get("/api/v1/tasks/governed-runs/{run_id}/result-manifest")
async def result_manifest(run_id: str, request: Request) -> dict[str, Any]:
    _require_service(request)
    run = _load_runs().get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Governed run not found")
    status = _envelope_data((await _upstream("GET", f"/api/v1/tasks/execution/{run_id}/status")).json())
    if str(status.get("status")) not in {"completed", "succeeded", "success"}:
        raise HTTPException(status_code=409, detail="Governed run has not completed")
    return {
        "data": {
            "artifact_type": run["artifact_type"],
            "execution_snapshot_sha256": run["execution_snapshot_sha256"],
            "policy_sha256": run["policy_sha256"],
            "source_version_ids": run["source_version_ids"],
            "records_url": f"/api/v1/tasks/governed-runs/{run_id}/records",
            "quality_gate": {"passed": True, "checks": ["upstream_completed", "schema_checked_by_dataforge"]},
        }
    }


@app.get("/api/v1/tasks/governed-runs/{run_id}/records")
async def governed_records(run_id: str, request: Request) -> Response:
    _require_service(request)
    response = await _upstream("GET", f"/api/v1/tasks/execution/{run_id}/download")
    return _response_from_upstream(response)


@app.post("/api/v1/tasks/governed-runs/{run_id}/cancel")
async def cancel_governed_run(run_id: str, request: Request) -> dict[str, Any]:
    _require_service(request)
    response = await _upstream("POST", f"/api/v1/tasks/execution/{run_id}/kill")
    return JSONResponse(status_code=response.status_code, content=response.json())


@app.api_route("/api/v1/dataforge/policy", methods=["GET"])
async def browser_policy(request: Request) -> dict[str, Any]:
    await _require_embed_session(request)
    return (await _dataforge("GET", "/api/dataflow/operator-policy")).json()


@app.post("/api/v1/dataforge/drafts")
async def browser_create_draft(request: Request) -> dict[str, Any]:
    await _require_embed_session(request)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Draft payload must be an object")
    raw = payload.get("raw_pipeline")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="raw_pipeline is required")
    await _assert_pipeline_allowed(raw)
    response = await _dataforge("POST", "/api/dataflow/drafts", json=payload)
    return response.json()


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def browser_api_proxy(path: str, request: Request) -> Response:
    await _require_embed_session(request)
    if path.startswith(BLOCKED_BROWSER_API_PREFIXES) or path.startswith(("tasks/execute", "agent")):
        raise HTTPException(status_code=403, detail="This DataFlow capability is disabled by DataForge policy")
    body = await request.body()
    if path.startswith("pipelines") and request.method in {"POST", "PUT", "PATCH"}:
        try:
            candidate = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Pipeline payload must be JSON") from exc
        if isinstance(candidate, dict):
            await _assert_pipeline_allowed(candidate)
    headers = {key: value for key, value in request.headers.items() if key.lower() in {"content-type", "accept"}}
    response = await _upstream(request.method, f"/api/v1/{path}", params=request.query_params, content=body, headers=headers)
    if path.startswith("operators") and response.status_code < 400:
        policy = await _policy()
        payload = response.json()
        data = _envelope_data(payload)
        if isinstance(data, list):
            payload["data"] = [item for item in data if isinstance(item, dict) and _permits(policy, item)]
        elif isinstance(data, dict) and not _permits(policy, data):
            return JSONResponse(status_code=404, content={"detail": "Operator is not permitted"})
        return JSONResponse(status_code=response.status_code, content=payload)
    return _response_from_upstream(response)


@app.get("/{path:path}")
async def ui_proxy(path: str, request: Request) -> Response:
    await _require_embed_session(request)
    if path == "mcp" or path.startswith("mcp/"):
        raise HTTPException(status_code=404, detail="MCP is not exposed through the DataFlow iframe")
    response = await _upstream("GET", f"/{path}", params=request.query_params)
    return _response_from_upstream(response, rewrite=True)
