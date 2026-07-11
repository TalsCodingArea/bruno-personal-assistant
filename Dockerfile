FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# libpango/libgdk-pixbuf/libffi: required by WeasyPrint for PDF generation
# tzdata: lets the TZ env var (set in docker-compose.yml) affect date logic
# nodejs/npm: required by the Notion MCP fallback (spawned via npx)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        tzdata \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pre-install the pinned Notion MCP server so the first fallback call doesn't
# download it at runtime. Keep the version in sync with
# personal_assistant/tools/mcp/notion_mcp.py (a mismatch is safe -- npx just
# downloads the pinned version on first use instead).
RUN npm install -g @notionhq/notion-mcp-server@2.4.1

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

CMD ["python", "-u", "app.py"]
