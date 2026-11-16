# Architecture

OpenClip is a local-first, contract-first pipeline for turning a source video into selected vertical clips. The v0.1 product supports English-language media only.

```mermaid
flowchart LR
    I[ingest] --> T[transcribe]
    T --> S[segment]
    S --> C[score]
    C --> L[select]
    L --> R[reframe]
    L --> A[captions]
    R --> E[render]
    A --> E
    E --> X[export]
```

## Package responsibilities

| Package | Responsibility |
| --- | --- |
| `core` | Pydantic contracts, settings, errors, and stage interfaces. |
| `ingest` | Safely acquire and inspect a local file or explicitly requested URL. |
| `transcribe` | Generate word-level English transcripts. |
| `analyze` | Segment, score, and select candidate moments. |
| `reframe` | Calculate smooth 9:16 crop paths. |
| `captions` | Build ASS subtitle documents from transcripts and presets. |
| `render` | Invoke FFmpeg with argument lists to produce clips. |
| `pipeline` | Persist jobs and orchestrate sequential, cancellable stages. |
| `api` | Expose the local HTTP contract. |
| `cli` | Make every pipeline stage usable without the web UI. |

## Highlight scoring

`find_candidates` builds sentence-aligned windows of `min_clip_seconds`–`max_clip_seconds`, prefers boundaries at pauses ≥ 0.4 s and topic shifts (pure-Python bag-of-words cosine between adjacent blocks), then scores each window 0–100 with `HeuristicWeights` in `analyze/scoring.py`:

| Component | Weight | Meaning |
| --- | --- | --- |
| `hook` | 30 | Opening-grab rules on the first ~3 s (question, numbers, strong claims, second person, contrast; filler/pronoun/greeting penalties). |
| `speech_rate` | 15 | Triangular score peaking at ~3 words/second. |
| `audio_energy` | 10 | Per-second RMS of `audio.wav`, normalized by the 95th percentile. |
| `question` | 5 | Scaled by question count in the window. |
| `scene_change` | 5 | Scaled by ffmpeg scene-cut count (2 fps, threshold 0.3). |
| `boundary` | 10 | Sentence alignment + edge pauses + topic-shift quality. |
| `silence_penalty` | 10 | Longest internal word gap beyond ~1.5 s. |
| `filler_penalty` | 10 | Density of filler words ("um", "like", "basically"…). |

The optional LLM judge rescores only the top 12 heuristic candidates; final score is `0.6·llm + 0.4·heuristic` when it answers, heuristic-only otherwise.

## Resource budget

- The whole pipeline must remain under 3 GB peak RAM on the reference N305 machine.
- Stages run sequentially; Whisper and an LLM are never held in memory together.
- Video is streamed through FFmpeg. Analysis samples at no more than 5 fps and reduced resolution.
- CPU int8 is the default. Hardware encoders are optional; software x264 is the fallback.
- Heavy dependencies are optional extras. At most two heavy processes run concurrently.
- Stage artifacts live in a job directory and are cacheable by input hash and parameters.

## Key decisions

- **SQLite instead of Redis:** one local process needs durable, dependency-free job storage.
- **ASS plus libass instead of a JavaScript renderer:** FFmpeg produces repeatable burned-in captions.
- **YuNet instead of YOLO:** it satisfies the permissive-license requirement for face detection.
- **Apache-2.0:** it keeps the project broadly reusable.
- **Local-first:** no account, telemetry, or mandatory API key is required.
- **Contract-first:** models and OpenAPI are the shared boundary between pipeline, API, and UI work.
