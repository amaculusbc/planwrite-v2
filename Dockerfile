# PlanWrite v2 Dockerfile
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create app directory
WORKDIR /app

# Install system dependencies. BC Core is reached directly over its external HTTPS API
# (BC_CORE_BASE_URL), so no VPN/tunnel is needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY README.md .

# Install Python dependencies (from pyproject via pip)
RUN pip install --no-cache-dir .

# Copy application code
COPY app/ app/
COPY data/ data/
COPY railway-entrypoint.sh /railway-entrypoint.sh
COPY scripts/ scripts/
COPY storage/ storage/

# Create storage directory
RUN mkdir -p storage/exports \
    && sed -i 's/\r$//' /railway-entrypoint.sh \
    && chmod +x /railway-entrypoint.sh

# Expose app port (Railway sets PORT at runtime)
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen(f\"http://localhost:{os.getenv('PORT','8000')}/health\")"]

ENTRYPOINT ["/railway-entrypoint.sh"]

# Run the application (honor Railway PORT env var)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
