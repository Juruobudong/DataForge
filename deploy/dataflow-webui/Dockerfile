# Build a pinned upstream release without copying its source into DataForge.
FROM node:22-bookworm AS upstream-source
ARG DATAFLOW_REPOSITORY=https://github.com/OpenDCAI/DataFlow-WebUI.git
ARG DATAFLOW_COMMIT=b64f2b9d4de5792cb9fb3faedef07c0377b9c878
RUN apt-get update && apt-get install -y --no-install-recommends git python3 make g++ \
    && rm -rf /var/lib/apt/lists/* \
    && git clone "$DATAFLOW_REPOSITORY" /opt/dataflow-webui \
    && git -C /opt/dataflow-webui checkout "$DATAFLOW_COMMIT" \
    && test "$(git -C /opt/dataflow-webui rev-parse HEAD)" = "$DATAFLOW_COMMIT"
WORKDIR /opt/dataflow-webui/frontend
RUN npm install --no-audit --no-fund && npm run build

FROM python:3.11-slim
COPY --from=upstream-source /opt/dataflow-webui /opt/dataflow-webui
WORKDIR /opt/dataflow-webui/backend
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
