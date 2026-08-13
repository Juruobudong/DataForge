FROM python:3.11-slim

WORKDIR /opt/dataflow-overlay
COPY deploy/dataflow-overlay/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY deploy/dataflow-overlay/overlay.py ./

EXPOSE 8080
CMD ["uvicorn", "overlay:app", "--host", "0.0.0.0", "--port", "8080"]
