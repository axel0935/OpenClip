# OpenClip

**Status: pre-alpha.** Free, open source, local-first alternative to cloud
clippers.

OpenClip turns one long video into several short vertical clips (9:16,
1080×1920) of the most engaging moments, with dynamic burned-in captions. It
runs entirely on your machine through a minimal web UI and a CLI.

<!-- Demo GIF placeholder: replace this line with docs/assets/demo.gif once the
     launch recording exists. Do not ship marketing copy in this README yet. -->
_Demo GIF coming with the launch README._

## What it does

1. Takes a local file or a URL you paste in.
2. Transcribes it locally with faster-whisper (word-level timestamps).
3. Finds candidate moments between 20 and 90 seconds, aligned on sentence ends
   and silences, and scores them with objective features (audio energy, speech
   rate, questions, scene changes, hooks).
4. Optionally re-ranks the best candidates with a local LLM judge (Ollama or any
   OpenAI-compatible endpoint). Without one, heuristic scoring always works.
5. Reframes each clip to 9:16 by tracking faces and keeping a smooth crop path.
6. Burns in captions from one of eight ASS styles rendered by FFmpeg/libass.
7. Exports MP4 (H.264/AAC), thumbnails and a ZIP.

## Requirements

- Python 3.12 or newer (or Docker).
- **FFmpeg with libass** (`ffmpeg` and `ffprobe` on `PATH`). `openclip doctor`
  verifies the `subtitles` filter and the `libx264`/`aac` encoders.
- ~7 GB RAM is enough; a GPU is optional. CPU-first, quantised int8 ASR.

## Install

Three paths, same application.

**pipx / uvx (recommended).** This installs the wheel, which already contains the
web UI, the caption presets and the fonts:

```sh
pipx install "openclip[all]"
# or, without a persistent install:
uvx --from "openclip[all]" openclip serve
```

`all` means the `asr` (faster-whisper) and `vision` (OpenCV, numpy) extras. From
a wheel file or a direct URL, the same form works:
`pipx install "openclip[all] @ file:///path/to/openclip-0.1.0-py3-none-any.whl"`.

**Docker.** See [docs/DOCKER.md](docs/DOCKER.md):

```sh
docker build -f docker/Dockerfile -t openclip:local .
docker run --rm -p 127.0.0.1:8000:8000 -v openclip-data:/data openclip:local
```

**From source.** Needs [uv](https://docs.astral.sh/uv/) and, for the frontend,
pnpm:

```sh
make install
make check
make dev-api      # API on 127.0.0.1:8000
make dev-web      # Vite on :5173, proxies the API
```

## First run

```sh
openclip doctor        # check tools, extras, models, fonts, hardware, LLM
openclip serve         # then open http://127.0.0.1:8000
```

`openclip doctor --json` gives the same report for scripts. It exits 1 only when
a check fails (a missing FFmpeg, for example); warnings such as a missing
optional extra or no built web UI are normal on a minimal install. It never
downloads anything.

The server binds to `127.0.0.1` and serves the bundled UI. If no web bundle is
found it serves the API only and prints how to build one.

## CLI

```sh
# One long video to a folder of clips, no server involved:
openclip run path/to/talk.mp4 --out ./clips --max-clips 5 --style clean-pop

# Each stage is usable on its own:
openclip ingest path/to/talk.mp4 --workdir ./job
openclip transcribe path/to/talk.mp4 --workdir ./job --profile fast
openclip analyze ./job/transcript.json --workdir ./job
openclip reframe ./job/clips.json --rank 1 --workdir ./job
openclip captions ./job/transcript.json --clips ./job/clips.json --all-styles ./previews

# Prefetch models (optional; they download on first use anyway):
openclip models download --profile fast --yunet
```

Useful profiles: `--profile fast|balanced|quality` (base.en, small.en,
large-v3-turbo) and `--mode heuristic|llm|auto`.

## Performance on a weak CPU

Reference machine: Intel N305 (8 low-power cores, ~7 GB RAM, no GPU). A full
pipeline run on a 179-second 1080p sample with the `fast` profile took **276 s
wall time, about 1.5× the video length**, peaking at **1.48 GB RSS**. Production
of 3 clips. Transcription alone is much faster than realtime (`fast`: RTF 0.08,
0.49 GB peak; `balanced`: RTF 0.33, 0.87 GB).

Expect roughly realtime-or-slower end to end on a weak machine, and much faster
with a GPU. The pipeline runs stages sequentially to stay under 3 GB; a job can
be cancelled and resumed.

## Privacy

Everything runs locally. There is no telemetry, no account, no mandatory API key
and no analytics. Network access happens only when you ask for it: downloading a
URL you pasted, fetching a Whisper or YuNet model on first use, or calling an LLM
endpoint **you** configured (for example Ollama on `127.0.0.1`). Your media never
leaves the machine on its own.

## Legal

You are responsible for the content you process and for how you use the output.
Only clip material you have the rights to use, and respect the terms of service
of the platform the video came from, as well as copyright and any personality
rights involved. OpenClip ships no media and no model weights; models are
downloaded from their upstream publishers under their own licenses.

## License

Apache-2.0. See [LICENSE](LICENSE). Bundled caption fonts are SIL OFL 1.1, with
their license texts in [`assets/licenses/`](assets/licenses/); the face detector
(YuNet, OpenCV Zoo) is MIT.

## Status / roadmap

Working today: ingest (file and URL), word-level transcription, heuristic and
LLM-assisted highlight detection, face-tracking reframing, eight caption styles,
rendered MP4/thumbnail/preview output, job persistence with SSE progress, trim
and style re-rendering, the web UI, the CLI, `openclip doctor`, a packaged wheel
and a Docker image.

Next: a longer-video benchmark on the reference machine, English-language
quality tuning, batch processing, translations, and the launch documentation.
APIs and data formats may still change before 0.1.
