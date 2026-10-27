# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/var/lib/moss/data/models/.hf \
    TRANSFORMERS_CACHE=/var/lib/moss/data/models/.hf/transformers

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/moss
COPY backend/requirements.txt /tmp/backend-requirements.txt
RUN pip install --no-cache-dir -r /tmp/backend-requirements.txt

COPY backend/ /opt/moss/backend/
COPY --from=frontend-build /build/frontend/dist/ /opt/moss/frontend/dist/
COPY docker/entrypoint.sh /usr/local/bin/moss-entrypoint
RUN chmod 0755 /usr/local/bin/moss-entrypoint \
    && mkdir -p /var/lib/moss/data /tmp/moss \
    && chmod 1777 /tmp/moss

USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; host=os.environ.get('MOSS_HOST', '127.0.0.1'); port=os.environ.get('MOSS_PORT', '8000'); urllib.request.urlopen('http://' + host + ':' + port + '/', timeout=3)"]
ENTRYPOINT ["/usr/local/bin/moss-entrypoint"]
