# Docker

OpenClip runs as a single container: a FastAPI server that also serves the
prebuilt web UI, with FFmpeg (libass, libx264, AAC) and the optional ASR/vision
extras already installed. No account, no telemetry, no outbound traffic unless
you explicitly download a URL or a model.

> **Status:** the Dockerfile is written and reviewed, but the image has **not**
> been built on the maintainer's machine (Docker is not installed there). CI
> builds it on pull requests that touch `docker/**` (`.github/workflows/docker.yml`),
> which is the first real build. Treat the GPU variant below as untested.

## Build and run

From the repository root:

```sh
docker build -f docker/Dockerfile -t openclip:local .
docker run --rm -p 127.0.0.1:8000:8000 -v openclip-data:/data openclip:local
```

Then open <http://127.0.0.1:8000>. The volume mounted on `/data` keeps Whisper
models, uploaded sources and job outputs, so a restart does not re-download
weights. `OPENCLIP_DATA_DIR=/data` is set inside the image.

Or with Compose:

```sh
docker compose -f docker/docker-compose.yml up --build
```

Compose publishes the port on `127.0.0.1` only, on purpose: the UI has no
authentication, so it must not be exposed to your local network or the internet.

### First start

```sh
docker run --rm openclip:local doctor      # full self-check inside the container
docker run --rm openclip:local --version
```

Models are downloaded on first use (or prefetched with
`openclip models download --profile fast`) into `/data/models`. Nothing else
leaves the machine.

### Resource limits

The pipeline peaks around 1.5 GB RSS on the reference machine. Leave the
memory limit unset unless the host is shared; a 4 GB cap is comfortable for a
single job with the `fast`/`balanced` profiles.

## Using Ollama on the host

Ollama runs on the host, OpenClip in the container. Compose wires the host
address for you:

```yaml
environment:
  OPENCLIP_LLM_BASE_URL: http://host.docker.internal:11434/v1
  OPENCLIP_LLM_MODEL: llama3.2
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Without Compose, add `--add-host=host.docker.internal:host-gateway` to the
`docker run` command and the same two environment variables. Make sure Ollama
listens on an interface the container can reach (`OLLAMA_HOST=0.0.0.0` on the
host). If nothing answers, analysis falls back to heuristic mode, and
`openclip doctor` says so.

## GPU variant (NVIDIA, untested)

Not tested: no NVIDIA hardware was available while writing this. The changes are:

1. Base image `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` instead of
   `python:3.12-slim`, with Python 3.12 installed on top.
2. `runtime: nvidia` in Compose (or `--gpus all` for `docker run`), plus the
   NVIDIA Container Toolkit on the host.
3. A CUDA build of faster-whisper: `pip install "ctranslate2[cuda]"` (or a
   `ctranslate2` wheel built with CUDA) alongside the `all` extra. CPU wheels
   still work, they just do not use the GPU.
4. A CUDA compute type: the transcription config accepts `device="cuda"` and
   `compute_type="float16"`, but no environment variable exposes them yet. The
   pipeline builds `TranscribeConfig` from `JobOptions.whisper_profile` only, so
   a GPU build currently needs a code change or a CLI call that passes the
   config directly (`openclip transcribe --profile quality`). `openclip doctor`
   reports hardware encoders and the profile `auto` resolves to, but it does
   not probe a GPU.

CPU is the supported path. A GPU only changes the speed of the ASR stage.
