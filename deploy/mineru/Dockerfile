FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

ARG PYPI_INDEX_URL=https://pypi.org/simple
ARG MINERU_MODEL_SOURCE=modelscope

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl fontconfig fonts-noto-core fonts-noto-cjk libgl1 libglib2.0-0 \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --index-url "${PYPI_INDEX_URL}" "mineru[pipeline]==3.4.4" \
    && python -c "import cv2, importlib.util; from importlib.metadata import version; assert version('mineru') == '3.4.4'; assert importlib.util.find_spec('vllm') is None; assert importlib.util.find_spec('sglang') is None" \
    && MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE}" mineru-models-download -s "${MINERU_MODEL_SOURCE}" -m pipeline

ENV MINERU_MODEL_SOURCE=local

EXPOSE 8000
ENTRYPOINT ["mineru-api"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
