FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    APP_ENV=production BUCK_APP_AUTH_HEADER=X-Buck-Authorization DEEPSEEK_MODEL=deepseek-flash \
    AGENT_RUNTIME_DIR=/mnt/workspace/buck-lab/agent ARCHIVE_DIR=/mnt/workspace/buck-lab/archive \
    AGENT_MAX_ACTIVE_TASKS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements-lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements-lock.txt && \
    python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch>=2.6,<3'
EXPOSE 7860
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY examples/ ./examples/
COPY LICENSE THIRD_PARTY_NOTICES.md studio_start.py ./
CMD ["python", "/app/studio_start.py"]
