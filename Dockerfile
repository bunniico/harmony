FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HARMONY_CONFIG=/app/config.json \
    HARMONY_PERSONA_DIR=/app/persona \
    HARMONY_DB=/data/harmony.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY harmony/ harmony/

RUN useradd --create-home --uid 10001 harmony \
    && mkdir -p /data && chown harmony:harmony /data
USER harmony

CMD ["python", "-m", "harmony"]
