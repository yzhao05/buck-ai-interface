FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    APP_ENV=production DEEPSEEK_MODEL=deepseek-flash \
    AGENT_RUNTIME_DIR=/mnt/workspace/buck-lab/agent ARCHIVE_DIR=/mnt/workspace/buck-lab/archive \
    AGENT_MAX_ACTIVE_TASKS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
ADD buck-lab.tar.gz /app/
RUN python -m pip install --no-cache-dir -r requirements-lock.txt && \
    python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch>=2.6,<3'
EXPOSE 7860
COPY studio_start.py /app/studio_start.py
CMD ["python", "/app/studio_start.py"]
