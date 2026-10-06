# fitlog-mcp — self-hosted MCP server (Python stdlib only, zero dependencies).
# Used by directory listings (e.g. Glama) for automated security/quality checks.
FROM python:3.12-slim

WORKDIR /app

COPY server.py store.py ./
COPY mcp ./mcp

# Listen on all interfaces inside the container; override with env at runtime.
ENV FITLOG_HOST=0.0.0.0
ENV FITLOG_PORT=8765

EXPOSE 8765

CMD ["python3", "server.py"]
