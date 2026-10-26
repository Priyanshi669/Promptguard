FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY . .
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

ENV PATH="/app/.venv/bin:$PATH"

USER appuser

CMD ["promptguard"]
