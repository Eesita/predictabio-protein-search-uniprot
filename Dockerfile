# Dockerfile for combined deployment
FROM python:3.12-slim

WORKDIR /app

# Install Node.js (and clean apt cache to keep image small)
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better layer caching
COPY client/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY client/ .

# Copy MCP server (build from source to include latest changes)
COPY mcp-server ./mcp-server

# Copy the UI files
COPY chat_ui.html ./
COPY evalsview.html ./

# Install MCP server deps and build
WORKDIR /app/mcp-server
RUN npm ci && npm run build && npm prune --production

# Back to app directory
WORKDIR /app

# Set environment variables (PORT is set by Cloud Run; don't override here)
ENV MCP_SERVER_PATH="/app/mcp-server/build/index.js"

# Security: run as non-root user
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

# Python runtime tweaks
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Optional: document ports
EXPOSE 8001 8081

# Healthcheck (useful outside Cloud Run)
# Note: Cloud Run sets PORT env var dynamically, healthcheck uses script to read it
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD sh -c 'curl -fsS http://localhost:${PORT:-8080}/health || exit 1'

# OCI Labels
LABEL org.opencontainers.image.title="uniprot-app" \
      org.opencontainers.image.description="Conversational UniProt API + MCP server + UI" \
      org.opencontainers.image.licenses="MIT"

# Start the FastAPI server (which serves both API and UI)
CMD ["python", "api_service.py"]