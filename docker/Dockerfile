# syntax=docker/dockerfile:1

# OpenClip image: the FastAPI server plus the prebuilt web UI, FFmpeg with
# libass, and the optional ASR/vision extras. The container is the only place
# where the server binds to 0.0.0.0; the CLI default stays 127.0.0.1.
#
# Build from the repository root:
#     docker build -f docker/Dockerfile -t openclip .
# Run:
#     docker run --rm -p 127.0.0.1:8000:8000 -v openclip-data:/data openclip

# ---------------------------------------------------------------------------
# Stage 1: build the web UI. The lockfile is authoritative (--frozen-lockfile);
# the pnpm workspace lives at the repository root, so the root manifest files
# are copied before the frontend sources to keep this layer cacheable.
# ---------------------------------------------------------------------------
FROM node:22-slim AS web

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=1

RUN corepack enable && corepack prepare pnpm@10 --activate

WORKDIR /src

COPY pnpm-lock.yaml pnpm-workspace.yaml ./
COPY frontend/package.json frontend/package.json

RUN pnpm install --frozen-lockfile

COPY frontend/ frontend/

RUN pnpm --dir frontend build

# ---------------------------------------------------------------------------
# Stage 2: build the Python wheel. The frontend bundle is copied into the
# package before `uv build`, so the wheel ships openclip/web as package data;
# backend/hatch_build.py stages presets, fonts, licences and LICENSE.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv-cache

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /src

COPY presets/ presets/
COPY assets/ assets/
COPY LICENSE LICENSE
COPY README.md README.md

COPY backend/ backend/

COPY --from=web /src/frontend/dist backend/src/openclip/web/

RUN uv build --project backend --wheel --out-dir /dist

# ---------------------------------------------------------------------------
# Stage 3: runtime. Non-root, FFmpeg with libass, one writable data volume.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENCLIP_HOST=0.0.0.0 \
    OPENCLIP_PORT=8000 \
    OPENCLIP_DATA_DIR=/data

# The distro FFmpeg links libass (subtitles filter) and provides libx264 + AAC.
# `passwd` is listed explicitly because this hook adds a user and slim images
# have trimmed base packages before.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        passwd \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /dist/*.whl /tmp/openclip.whl

# "all" = asr + vision. The vision extra is already headless (no display here).
RUN pip install "/tmp/openclip.whl[all]" \
    && rm -f /tmp/openclip.whl

# Non-root runtime user; /data is the only writable path (models and jobs).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin openclip \
    && mkdir -p /data \
    && chown -R openclip:openclip /data

USER openclip

WORKDIR /home/openclip

VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('OPENCLIP_PORT','8000')+'/health',timeout=3)"

# No shell: the CLI starts uvicorn itself on OPENCLIP_HOST/OPENCLIP_PORT.
ENTRYPOINT ["openclip"]
CMD ["serve"]
