# Progress

- Step 1 (2026-11-16): scaffolded local-first repository hygiene, typed contracts, API/CLI stubs, and minimal web shell.
- Decision: OpenAPI and generated TypeScript types are committed and checked for synchronization.
- Step 2 (2026-11-16): implemented `ingest` (local copy + SSRF-guarded yt-dlp, ffprobe `MediaInfo`, `audio.wav` extraction) and `transcribe` (lazy faster-whisper, `transcript.json` cache, `TranscribeConfig`), plus `openclip ingest|transcribe` CLI. `make check` green; slow ASR test passes with real `base.en`.
- Decision: `beam_size=5` kept for `balanced` — measured faster than greedy on N305 (see table); `fast`=1, `quality`=5. `av<19` pinned (faster-whisper 1.2.1 breaks on PyAV 19). Sample `/home/othman/Videos/sample.mp4` was missing, so benchmark used a 57 s flite speech WAV instead.

Benchmark (N305, small.en int8, cpu_threads=6, 57.2 s audio):

| profile | beam | elapsed | RTF | peak RSS |
| --- | --- | --- | --- | --- |
| balanced | 1 | 96.9 s | 1.69x | 1.63 GB |
| balanced | 5 | 84.7 s | 1.48x | 1.39 GB |

- Next: persistence/job queue, then segment/score/select in `analyze`.
