FROM python:3.11-slim

WORKDIR /app

ARG CACHE_BUSTER=2026-03-06-001

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install playwright && playwright install chromium
RUN python -c "from camoufox import install; install()" || echo "Camoufox install may take time on first run"

COPY server.py .

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
