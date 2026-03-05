FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv pip install --system --no-cache \
    "agno[os]" openai httpx "pydantic>=2.0" pydantic-settings \
    python-dotenv rich lancedb tantivy psycopg2-binary mcp

COPY . .

EXPOSE 6443

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "6443"]
