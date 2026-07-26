# Container for the live dashboard. Works unchanged on Fly.io, Render,
# Railway, Google Cloud Run, or any VPS with a container runtime.
FROM python:3.12-slim

# Dependencies first, so code edits do not invalidate the wheel layer.
WORKDIR /app
COPY requirements-live.txt .
RUN pip install --no-cache-dir -r requirements-live.txt

COPY live.py dashboard.py recorder.py ./

# Run unprivileged. Nothing here needs root, and the process is reachable
# from the public internet.
RUN useradd --create-home --uid 10001 app
USER app

# Bind all interfaces so the platform's router can reach us. PORT is
# overridden by most platforms at runtime; live.py reads it from the
# environment. VANTAGE labels the page with where the measurement is taken
# from -- set it per region at deploy time so remote viewers are not misled
# into reading these as their own latency.
ENV HOST=0.0.0.0 \
    PORT=8080 \
    VANTAGE="this server" \
    PYTHONUNBUFFERED=1
EXPOSE 8080

# /healthz is deliberately cheap: it does not touch the stats cache, so a
# health check never becomes a source of load.
HEALTHCHECK --interval=30s --timeout=4s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz',timeout=3)"

CMD ["python", "live.py"]
