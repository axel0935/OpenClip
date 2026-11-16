# AGENTS.md — OpenClip

Read this file completely before doing anything. Then read `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md` and `docs/PROGRESS.md` once they exist.

## 1. Project
OpenClip is a free, open-source, local-first alternative to Opus Clip.
Input: a long video (local file or URL). Output: several short vertical clips (9:16, 1080×1920) of the most engaging moments, with dynamic burned-in captions, through a minimal web UI and a CLI.
- Repo: https://github.com/axel0935/OpenClip — License: Apache-2.0
- Maintainer: axel0935 <axel.llm54@gmail.com>
- Product language: **English only** (code, comments, UI text, docs, errors, commit messages). v0.1 targets English-language videos.
- No accounts, no telemetry, no mandatory API key. Everything runs locally by default.

## 2. Non-negotiable rules
1. **Contracts first.** The data models (§6) and `docs/CONTRACTS.md` are the contract between modules and between agents. Never change them silently: if a change is needed, edit `core/models.py`, update `docs/CONTRACTS.md`, regenerate the OpenAPI file and the TypeScript client, and mention it in your final report.
2. **Stay in your lane.** Only edit files inside the scope of your task. If you need a change outside it, do not make it: report it in your final message.
3. **Never** commit secrets, tokens, videos, model weights or copyrighted media. Test fixtures must be tiny and royalty-free (CC0 / public domain) or generated programmatically.
4. **Local and private.** No telemetry. Network access only for explicit user actions (URL download, model download, optional user-configured LLM endpoint).
5. **Safe by default.** The server binds to `127.0.0.1`. External programs (ffmpeg, ffprobe, yt-dlp) are always called with argument lists — never `shell=True`, never string-built commands. User-supplied paths and URLs are validated.
6. **Licenses.** Dependencies must be permissive (MIT, BSD, Apache-2.0, ISC, MPL-2.0). No GPL/AGPL Python dependency (so no Ultralytics/YOLO). FFmpeg is only invoked as a subprocess. Bundled fonts must be OFL or Apache-2.0.
7. **Every stage is testable alone.** Each pipeline module is usable from the CLI and covered by unit tests, without the web UI.
8. **Git.** Commit locally, in small commits, using Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `ci:`, `refactor:`). Stage files explicitly by path (never `git add -A` / `git add .`). Never push, never force-push, never rewrite history unless the task says so.
9. **Done means green.** Finish every task by running `make check`, adding at most 5 lines to `docs/PROGRESS.md` (status, decisions, next step), and committing.
10. **No questions mid-task.** Take the most reasonable assumption, record it in your final report, and keep going. Stop and report only if truly blocked.

## 3. Stack
- **Backend:** Python 3.12, uv, FastAPI, Uvicorn, Pydantic v2, pydantic-settings, SQLModel + SQLite (jobs), Typer (CLI), platformdirs, httpx.
- **Media:** FFmpeg / ffprobe, yt-dlp.
- **Transcription:** faster-whisper (CTranslate2), word-level timestamps, VAD. Profiles: `fast` = base.en, `balanced` = small.en, `quality` = large-v3-turbo. `auto` = `balanced` on CPU.
- **Highlight detection (3 layers):** (1) candidate windows of 20–90 s aligned on sentence ends and silences; (2) objective features (audio energy, speech rate, questions, scene changes, hooks); (3) LLM judge through a pluggable provider (Ollama or any OpenAI-compatible endpoint) returning validated JSON. A heuristic-only fallback must always work.
- **Vertical reframing:** OpenCV + YuNet face detector (MIT), smoothed crop trajectory, `fit` layout with blurred background as fallback.
- **Captions:** ASS subtitle files rendered by FFmpeg/libass. Styles are JSON presets in `presets/captions/`.
- **Frontend:** React, Vite, TypeScript (strict), Tailwind, shadcn/ui, TanStack Query, JASSUB (ASS preview in the browser), vitest, Playwright. API types are generated from the OpenAPI file, never hand-written.
- **Quality:** ruff, pyright, pytest, pre-commit, GitHub Actions.

## 4. Repository layout
```
OpenClip/
├─ AGENTS.md  README.md  LICENSE  Makefile  .pre-commit-config.yaml
├─ backend/
│  ├─ pyproject.toml
│  ├─ src/openclip/
│  │  ├─ core/       config, models, errors, interfaces
│  │  ├─ ingest/     transcribe/   analyze/   reframe/   captions/   render/
│  │  ├─ pipeline/   orchestrator, job queue
│  │  ├─ api/        FastAPI app and routes
│  │  └─ cli/        Typer app
│  └─ tests/
├─ frontend/         React app
├─ presets/captions/ caption styles (JSON)
├─ assets/fonts/     OFL / Apache-2.0 fonts only
├─ docs/             ARCHITECTURE.md CONTRACTS.md PROGRESS.md UI_SPEC.md openapi.json
├─ scripts/          export_openapi.py
└─ .github/workflows/
```

## 5. Pipeline and ownership
`ingest → transcribe → segment → score → select → reframe → captions → render → export`

| Stage | Package | Contract (see `core/interfaces.py`) |
|---|---|---|
| ingest | `ingest` | `ingest(source, workdir) -> MediaInfo` |
| transcribe | `transcribe` | `transcribe(media, cfg) -> Transcript` |
| segment / score / select | `analyze` | `find_candidates(transcript, media, cfg) -> list[Candidate]`, `score_candidates(...) -> list[Clip]`, `select_clips(...) -> list[Clip]` |
| reframe | `reframe` | `compute_crop_path(media, clip, layout) -> CropPath` |
| captions | `captions` | `build_ass(transcript, clip, style) -> str` |
| render | `render` | `render_clip(media, clip, crop, ass_path, out_path, cfg) -> Path` |

## 6. Data contracts (source of truth once implemented: `backend/src/openclip/core/models.py`)
Times are `float` seconds from the start of the source video unless stated. Colors are `"#RRGGBB"` or `"#RRGGBBAA"`. Ids are `str`.
```
Word:        text, start, end, probability: float|None
Segment:     id: int, start, end, text, words: list[Word]
Transcript:  language, duration, model, segments: list[Segment]
MediaInfo:   path, duration, width: int, height: int, fps, has_audio: bool, title: str|None, source_url: str|None
CandidateFeatures: speech_rate_wps, audio_energy (0-1), question_count: int, scene_changes: int, hook_score (0-1)
Candidate:   id, start, end, text, features: CandidateFeatures, heuristic_score (0-100)
Clip:        id, start, end, score (0-100), title, hook, hashtags: list[str], rationale,
             source: "llm"|"heuristic", output_file: str|None, thumbnail: str|None
CropKeyframe: t (seconds from clip start), x, y, w, h  (source pixels, 9:16 aspect)
CropPath:    layout: "fill"|"fit", source_width, source_height, keyframes: list[CropKeyframe]
CaptionStyle:
  id (kebab-case), name, description
  font_family, bold: bool, italic: bool = false, font_size: int (px on a 1080×1920 canvas),
  uppercase: bool, letter_spacing: float = 0
  text_color, highlight_color, outline_color, outline_width: float,
  shadow_color: str|None, shadow_offset: float = 0
  background: {color, padding: int, radius: int} | None
  highlight_mode: "none"|"word_color"|"word_box"|"karaoke_fill"
  animation: "none"|"pop"|"fade"|"slide_up"|"bounce"
  words_per_caption: int (1-6), max_lines: int (1-3)
  position: "top"|"middle"|"bottom", margin_v: int (px from the edge)
Stage (enum):     ingest, transcribe, segment, score, select, reframe, captions, render, export
JobStatus (enum): queued, running, completed, failed, cancelled
JobSource:   type: "url"|"upload", value
JobOptions:  min_clip_seconds = 20, max_clip_seconds = 90, max_clips = 8,
             caption_style_id = "clean-pop", layout: "auto"|"fill"|"fit" = "auto",
             whisper_profile: "auto"|"fast"|"balanced"|"quality" = "auto",
             analysis_mode: "auto"|"heuristic"|"llm" = "auto"
Job:         id, status, stage: Stage|None, progress (0-1), message: str|None, source, options,
             media: MediaInfo|None, clips: list[Clip], error: JobError|None, created_at, updated_at
JobError:    code, message
API-only:    HealthResponse{status, version}, UploadResponse{upload_id, filename, size_bytes},
             JobCreate{source, options}, ClipUpdate{start?, end?, caption_style_id?}, ErrorResponse{code, message}
```

## 7. Resource budget (reference machine: Intel N305, 8 low-power cores, ~7 GB RAM, no GPU)
- Peak RAM of the whole pipeline < 3 GB. Run stages sequentially; never keep Whisper and an LLM loaded at the same time; release models after use.
- Never load a whole video in memory. Stream with ffmpeg. Analyze video at ≤ 5 fps and reduced resolution.
- CPU first, GPU optional: int8 on CPU; detect NVENC / VAAPI / QSV, with software x264 as fallback.
- Heavy dependencies live in optional extras so that the base install and CI stay light.
- Every long operation reports progress (0–1) and is cancellable.
- Cache stage outputs in the job directory (keyed by input hash + parameters) so an interrupted job can resume.
- At most 2 heavy processes at once.

## 8. Conventions
- **Python:** typed, Pydantic v2 models, `pathlib`, `logging` (no `print` outside the CLI), public functions documented, errors derive from `OpenClipError`, no hidden global state. ruff and pyright must pass.
- **Frontend:** TypeScript strict, functional components, TanStack Query for server state, Tailwind + shadcn/ui (no other UI library without justification), accessible (labels, focus states, contrast).
- **Data directory:** `platformdirs` user data dir `/openclip`, one sub-directory per job.
- Config through env vars prefixed `OPENCLIP_` (pydantic-settings).

## 9. Commands (Makefile)
```
make install    # backend (uv sync) + frontend (pnpm install)
make dev-api    # FastAPI on 127.0.0.1:8000 with reload
make dev-web    # Vite dev server on :5173 (proxies the API)
make check      # lint + format check + typecheck + tests, backend and frontend
make test       # tests only
make openapi    # regenerate docs/openapi.json and frontend/src/api/schema.d.ts
make build-web  # production build of the frontend
```

## 10. Handoff between agents
Start every task by reading this file, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md` and the end of `docs/PROGRESS.md`. End every task with a short final report: files created/changed, commands run and their result, assumptions, anything that needs the maintainer's attention.