FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir cryptography==46.0.3 \
 && useradd --create-home --uid 10001 vortex
COPY . /app
RUN chown -R vortex:vortex /app

USER vortex
EXPOSE 8787
CMD ["python", "-m", "portal.cli", "serve"]
