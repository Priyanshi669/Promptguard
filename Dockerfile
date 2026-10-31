FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app \
    && mkdir -p /app/runs \
    && chown appuser:appuser /app/runs

ENV PATH="/app/.venv/bin:$PATH"

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"]

CMD ["uvicorn", "promptguard.interfaces.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
