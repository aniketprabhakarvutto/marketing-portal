FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir cryptography==46.0.3 \
 && useradd --create-home --uid 10001 vortex
COPY . /app
RUN mkdir -p /data \
 && chown -R vortex:vortex /app /data

USER vortex
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).read()"]
CMD ["python", "-m", "portal.cli", "serve"]
