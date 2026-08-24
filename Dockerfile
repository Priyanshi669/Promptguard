FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir uv && uv sync --frozen

CMD ["uv", "run", "promptguard"]
