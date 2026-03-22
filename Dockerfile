FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

EXPOSE 4000

CMD ["python", "-m", "llm_otel_sidecar"]
