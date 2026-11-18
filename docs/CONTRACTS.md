# Contracts

`backend/src/openclip/core/models.py` is the source of truth. Times are seconds from the source start, colors use `#RRGGBB` or `#RRGGBBAA`, and IDs are strings.

## Data models

| Model | JSON example |
| --- | --- |
| `Word` | `{"text":"Hello","start":0.0,"end":0.2,"probability":0.99}` |
| `Segment` | `{"id":0,"start":0.0,"end":1.0,"text":"Hello world","words":[{"text":"Hello","start":0.0,"end":0.2,"probability":0.99}]}` |
| `Transcript` | `{"language":"en","duration":1.0,"model":"base.en","segments":[]}` |
| `MediaInfo` | `{"path":"/data/source.mp4","duration":60.0,"width":1920,"height":1080,"fps":30.0,"has_audio":true,"title":null,"source_url":null}` |
| `CandidateFeatures` | `{"speech_rate_wps":2.5,"audio_energy":0.6,"question_count":1,"scene_changes":2,"hook_score":0.8}` |
| `Candidate` | `{"id":"candidate-1","start":0.0,"end":30.0,"text":"A compelling moment.","features":{"speech_rate_wps":2.5,"audio_energy":0.6,"question_count":1,"scene_changes":2,"hook_score":0.8},"heuristic_score":82.0}` |
| `Clip` | `{"id":"clip-1","start":0.0,"end":30.0,"score":90.0,"title":"Key idea","hook":"Watch this","hashtags":["#openclip"],"rationale":"Strong hook","source":"heuristic","output_file":null,"thumbnail":null}` |
| `CropKeyframe` | `{"t":0.0,"x":656,"y":0,"w":608,"h":1080}` |
| `CropPath` | `{"layout":"fill","source_width":1920,"source_height":1080,"keyframes":[{"t":0.0,"x":656,"y":0,"w":608,"h":1080}]}` |
| `CaptionBackground` | `{"color":"#00000080","padding":8,"radius":4}` |
| `CaptionStyle` | `{"id":"clean-pop","name":"Clean Pop","description":"Readable captions","font_family":"Inter","bold":true,"italic":false,"font_size":64,"uppercase":false,"letter_spacing":0,"text_color":"#FFFFFF","highlight_color":"#00FF00","outline_color":"#000000","outline_width":2,"shadow_color":null,"shadow_offset":0,"background":null,"highlight_mode":"word_color","animation":"pop","words_per_caption":3,"max_lines":2,"position":"bottom","margin_v":80}` |
| `Stage` | `"transcribe"` (one of `ingest`, `transcribe`, `segment`, `score`, `select`, `reframe`, `captions`, `render`, `export`) |
| `JobStatus` | `"running"` (one of `queued`, `running`, `completed`, `failed`, `cancelled`) |
| `JobSource` | `{"type":"upload","value":"upload-123"}` |
| `JobOptions` | `{"min_clip_seconds":20,"max_clip_seconds":90,"max_clips":8,"caption_style_id":"clean-pop","layout":"auto","whisper_profile":"auto","analysis_mode":"auto"}` |
| `JobError` | `{"code":"render_failed","message":"FFmpeg exited with an error."}` |
| `Job` | `{"id":"job-1","status":"running","stage":"transcribe","progress":0.4,"message":"Transcribing","source":{"type":"upload","value":"upload-123"},"options":{"min_clip_seconds":20,"max_clip_seconds":90,"max_clips":8,"caption_style_id":"clean-pop","layout":"auto","whisper_profile":"auto","analysis_mode":"auto"},"media":null,"clips":[],"error":null,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}` |
| `HealthResponse` | `{"status":"ok","version":"0.1.0"}` |
| `UploadResponse` | `{"upload_id":"upload-123","filename":"source.mp4","size_bytes":1024}` |
| `JobCreate` | `{"source":{"type":"upload","value":"upload-123"},"options":{"min_clip_seconds":20,"max_clip_seconds":90,"max_clips":8,"caption_style_id":"clean-pop","layout":"auto","whisper_profile":"auto","analysis_mode":"auto"}}` |
| `ClipUpdate` | `{"start":2.0,"end":28.0,"caption_style_id":"clean-pop"}` |
| `ErrorResponse` | `{"code":"not_implemented","message":"not implemented yet"}` |

## Stage interfaces

```python
ingest(source: JobSource, workdir: Path) -> MediaInfo
transcribe(media: MediaInfo, cfg: JobOptions) -> Transcript
find_candidates(transcript: Transcript, media: MediaInfo, cfg: JobOptions) -> list[Candidate]
score_candidates(candidates: list[Candidate], transcript: Transcript, cfg: JobOptions) -> list[Clip]
select_clips(clips: list[Clip], cfg: JobOptions) -> list[Clip]
compute_crop_path(media: MediaInfo, clip: Clip, layout: str) -> CropPath
build_ass(transcript: Transcript, clip: Clip, style: CaptionStyle) -> str
render_clip(media: MediaInfo, clip: Clip, crop: CropPath, ass_path: Path, out_path: Path, cfg: JobOptions) -> Path
```

## HTTP API

| Method | Route | Success response | Scaffold behavior |
| --- | --- | --- | --- |
| GET | `/health` | `200 HealthResponse` | Implemented. |
| GET | `/api/caption-styles` | `200 CaptionStyle[]` | `501 ErrorResponse` |
| POST | `/api/uploads` | `201 UploadResponse` | `501 ErrorResponse` |
| POST | `/api/jobs` | `201 Job` | `501 ErrorResponse` |
| GET | `/api/jobs` | `200 Job[]` | `501 ErrorResponse` |
| GET | `/api/jobs/{job_id}` | `200 Job` | `501 ErrorResponse` |
| DELETE | `/api/jobs/{job_id}` | `204` | `501 ErrorResponse` |
| GET | `/api/jobs/{job_id}/events` | `200 text/event-stream` | `501 ErrorResponse` |
| PATCH | `/api/jobs/{job_id}/clips/{clip_id}` | `200 Clip` | `501 ErrorResponse` |
| GET | `/api/jobs/{job_id}/clips/{clip_id}/video` | `200 video/mp4` | `501 ErrorResponse` |
| GET | `/api/jobs/{job_id}/clips/{clip_id}/thumbnail` | `200 image/jpeg` | `501 ErrorResponse` |
| GET | `/api/jobs/{job_id}/clips/{clip_id}/preview` | `200 video/mp4` | `501 ErrorResponse` |
| GET | `/api/jobs/{job_id}/clips/{clip_id}/captions.ass` | `200 text/plain` | `501 ErrorResponse` |
| GET | `/api/jobs/{job_id}/download.zip` | `200 application/zip` | `501 ErrorResponse` |

The API allows CORS only from `http://localhost:5173` and `http://127.0.0.1:5173`.

## Server-sent events

`GET /api/jobs/{job_id}/events` uses `text/event-stream`. Every frame carries a monotonic `id:` line (per process) so a client can reconnect with the standard `Last-Event-ID` header; the last ~64 frames per job are replayed, and a reconnect with `Last-Event-ID: n` resumes after frame `n` (a fresh connection gets a `progress` snapshot first, a resuming one does not). Each event is UTF-8 JSON in a `data:` line. Job-scoped payloads carry `job_id`; clip events add `clip_id` and/or `clip`:

```text
id: 41
event: progress
data: {"job_id":"job-1","status":"running","stage":"transcribe","progress":0.4,"message":"Transcribing"}

id: 42
event: clip_ready
data: {"job_id":"job-1","clip":{"id":"clip-1","start":0.0,"end":30.0,"score":90.0,"title":"Key idea","hook":"Watch this","hashtags":[],"rationale":"Strong hook","source":"heuristic","output_file":".../clip.mp4","thumbnail":".../thumbnail.jpg"}}

id: 43
event: done
data: {"job_id":"job-1","status":"completed","stage":"export","progress":1.0,"message":"Render complete"}

id: 44
event: error
data: {"job_id":"job-1","code":"render_failed","message":"FFmpeg exited with an error."}

# clip-scoped re-render failure (stream stays open):
id: 45
event: error
data: {"job_id":"job-1","clip_id":"clip-1","code":"render_failed","message":"ffmpeg failed with exit code 1."}
```

`clip_ready` is emitted once per clip during the render stage and again after a successful `PATCH` re-render of that clip. `done` carries the final `status` — `completed` **or** `cancelled` — and always terminates the stream; a job-level `error` also terminates it, while a clip-scoped `error` does not.

## Job status

| Status | Meaning | `error` |
| --- | --- | --- |
| `queued` | Accepted, waiting for the single worker. | `null` |
| `running` | A stage is executing; `stage` and `progress` advance. | `null` |
| `completed` | All clips rendered (individual clip failures are reported in `message`). | `null` |
| `cancelled` | The user cancelled it; no terminal error. Resumable from its stage markers. | `null` |
| `failed` | Terminal failure (`interrupted`, `no_speech`, `ffmpeg_missing`, …). | set |

A job left `running`/`queued` by a previous process is marked `failed` with code `interrupted` at startup and can be resumed.

## HTTP errors and uploads

Successful responses use the documented models. Every error response is `ErrorResponse{code, message}` — including request validation: a malformed JSON body yields `422 {"code":"invalid_request", …}` instead of FastAPI's default `{"detail": [...]}`. `code` values used by the API: `not_found`, `invalid_source`, `too_large`, `io_error`, `disk_full`, `not_ready`, `rerender_in_progress`, `invalid_range`, `invalid_duration`, `out_of_range`, `unknown_style`, `invalid_request`, plus the job error codes above.

`POST /api/uploads` accepts one multipart field named `file` and enforces `OPENCLIP_MAX_UPLOAD_MB`. The stored name is the client base name reduced to `[A-Za-z0-9._-]`, dot-trimmed, capped at 180 characters, with `.`/`..` replaced by `upload.bin`; the response `filename` is that stored name. An upload that would exceed the limit is rejected with `413 too_large` and its partial directory removed; a filesystem `ENOSPC` answers `507 disk_full`, also with nothing left behind.

## Clip re-render (PATCH)

`PATCH /api/jobs/{job_id}/clips/{clip_id}` validates the merged trim (end > start, within the job's `min_clip_seconds`/`max_clip_seconds` with 10% tolerance, inside the source duration) and the caption style id, then re-renders captions, final MP4, preview proxy and thumbnail into temporary files swapped in atomically. Responses: `200 Clip` (re-render runs in background), `404` unknown job/clip, `409 {"code":"rerender_in_progress"}` when a re-render for the same clip is already running, `422` (`invalid_range`, `invalid_duration`, `out_of_range`, `unknown_style`) on bad input. The `/preview` route serves a ~360x640 proxy with the same burned-in captions as `/video`.
