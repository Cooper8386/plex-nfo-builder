# syntax=docker/dockerfile:1.6

# The frontend build produces static JS/CSS/HTML that is identical for
# every target architecture. Pin this stage to the host's native arch
# ($BUILDPLATFORM) so npm runs natively on the GitHub runner instead of
# under QEMU emulation — Node + QEMU + arm64 reliably segfaults
# ("Illegal instruction") during npm install on large dependency trees.
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS backend
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY --from=frontend /app/dist /app/static

ENV MEDIA_ROOT=/media \
    CONFIG_DIR=/config \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8000

VOLUME ["/media", "/config"]
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
