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

`GET /api/jobs/{job_id}/events` uses `text/event-stream`. Each event is UTF-8 JSON in a `data:` line:

```text
event: progress
data: {"job_id":"job-1","stage":"transcribe","progress":0.4,"message":"Transcribing"}

event: clip_ready
data: {"id":"clip-1","start":0.0,"end":30.0,"score":90.0,"title":"Key idea","hook":"Watch this","hashtags":[],"rationale":"Strong hook","source":"heuristic","output_file":null,"thumbnail":null}

event: done
data: {}

event: error
data: {"code":"render_failed","message":"FFmpeg exited with an error."}
```
