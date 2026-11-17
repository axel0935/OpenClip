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
- Step 5a (2026-11-16): benchmarked the real 179 s sample — `fast` (base.en): 15.1 s, RTF 0.08, 0.49 GB peak, 847 words; `balanced` (small.en): 59.9 s, RTF 0.33, 0.87 GB, 853 words; diffs were punctuation/compound-word splits only. New `auto` rule: `fast` unless ≥12 logical cores or caller-measured RTF < 0.5 (`resolve_profile(measured_rtf=...)`).
- Step 5b (2026-11-16): implemented `analyze` — sentence-aligned candidates, objective features (RMS energy, ffmpeg scene cuts, questions, hook rules), named heuristic weights, optional OpenAI-compatible LLM judge (top-12, validated JSON, heuristic fallback), overlap/gap selection — plus `openclip analyze`. Analyze peak RSS 0.32 GB on the sample.

Clips found on the real sample (heuristic mode, `/tmp/oc-bench-fast`):

| # | start-end | dur | score | title |
| --- | --- | --- | --- | --- |
| 1 | 0:00-0:21 | 21s | 41.9 | When you make a new channel, the first thing that happens |
| 2 | 1:03-1:25 | 22s | 41.1 | But if you're in the 10k to low 20k range, it's due to one |
| 3 | 1:50-2:11 | 21s | 40.0 | This is actually against their policies. |
| 4 | 0:32-0:54 | 22s | 31.5 | Low trust means YouTube would rather push out someone |
| 5 | 2:37-2:59 | 22s | 27.6 | View-jailed more often, or only pushed to subscribers. |
- Step 4 (2026-11-16): implemented 3 UI screens (Import, Processing, Results) matching UI_SPEC tokens and copy, isolated behind `ApiClient` with default `MockApiClient` (9-stage simulation, SVG thumbnails, cancel, ?mock=error) and `HttpApiClient`.
- Caption style picker previews all 8 presets with Fontsource OFL fonts, live sample highlight, and roving tabindex keyboard navigation.
- Hash-based navigation (`#/`, `#/jobs/:id`), TanStack Query state, dark/light/system theme, trim nudges, results sorting, vitest suite (16 tests).
- Part 0/Step 6 (2026-11-16): only one sample exists (no >10 min video), so analyze was re-run on the 3-min sample.
- Scoring: added named `duration` term (weight 8; 0 below 30 s, bonus for 30-60 s sentence-aligned windows, penalty above ~75 s); new clips: 0:00-1:00.2 (49.6), 1:49.8-2:40.6 (45.1), 1:14.6-1:37.8 (32.9), 2:36.8-2:58.9 (27.6).
- Step 6 reframe: YuNet face tracking + smoothed 9:16 crop path (`reframe/`, `openclip reframe`); on the sample auto->fit (no persistent faces), fill = stable centred crop (3 keyframes), ~0.57x realtime, 0.32 GB peak RSS.
- Step 3 (2026-11-16): `captions` stage: `build_ass` (ASS 1080x1920, `none|word_color|word_box|karaoke_fill`, pop/fade/slide_up/bounce, plates, glow), `load_presets`, `fonts`, `openclip captions --out/--preview/--all-styles`, Pillow measurement.
- Decision: libass fits `Fontsize` to usWinAscent+usWinDescent, not the em — generator emits `font_size * ratio` (read from the TTF header), else text is 18-45% small; `fontsdir` must stay flat because libass never recurses.
- Decision: `Shadow` renders as a hard offset glyph copy over display type, so presets keep `shadow_*` but the generator draws a blurred lower layer instead.
- Fonts: 8 families (12 static TTFs, all OFL-1.1) in `assets/fonts/`, flat, licence + README next to them; largest file 420 KB, so no pre-commit threshold change. `make check` backend green (231 tests).
- Next: Step 7 render stage consumes `ffmpeg_subtitles_filter`; frontend `pnpm` is absent on this machine, so `make check` stops at the frontend leg.
- Step 7 (2026-11-16): `render/` (piecewise crop-path ffmpeg expression, fit blurred backdrop, burned ASS captions, cached encoder probe, thumbnails + 360x640 preview proxies), `pipeline/` (stage weights ingest .10 transcribe .25 analyze .15 reframe .10 captions .05 render .30 export .05, SQLite store, FIFO queue + SSE bus, resume markers), real API routes, `openclip run`/`serve`.
- Real run on the 179 s sample: 3 clips (60/51/23 s), 276 s wall, peak RSS 1.48 GB, encoder x264 (hw probe did not beat it); all outputs 1080x1920 h264+aac yuv420p, A/V sync <=20 ms, captions verified burned vs captionless previews.
- API smoke on the 70 s Goggins clip: upload -> job -> SSE (61 progress, clip_ready, done) -> Range 206 video, thumbnail, download.zip; server stopped cleanly, no leftover processes.

- Part 0 (2026-11-17): CI backend job failed — test_render.py shells out to ffmpeg/ffprobe, absent on ubuntu-latest. CI now apt-installs ffmpeg; the 3 render tests also got the standard shutil.which skip guard.
- Step 8 (2026-11-17): frontend wired to the real API — HttpApiClient default (VITE_API_MODE=mock for tests), SSE progress/clip_ready/done with reconnect+poll fallback, PATCH trim/style re-render, history open/delete, unreachable banner, focus-managed sheets, reduced-motion, vitest coverage (32 tests).
- Known backend bugs found during validation (not fixed, out of scope): PATCH re-render always fails — transcript.json is stored wrapped as {"transcript": ...} but rerender_clip validates it as a bare Transcript; and _render_one_clip skips render when clip.mp4/preview/thumbnail already exist, so trims would not re-encode anyway. clip_ready SSE payload is {"clip": ...} while CONTRACTS.md implies a bare Clip.
- PATCH re-render fixed (2026-11-17): transcript read via cache-envelope helper, artifacts rendered to a temp dir and swapped atomically (failure keeps old files), per-clip lock -> 409, input validation -> 422, crop path retimed/clamped to new bounds, clip_ready/error SSE events, ETag+no-cache on media routes.
- /preview is now a ~360x640 proxy WITH the same burned-in captions as /video; drawer player defaults to /video with a preview toggle, fake HTML caption overlay removed (kept only in style-picker cards).
- Contract sync: SSE payloads now carry job_id (+clip for clip_ready, +clip_id for clip-scoped errors); CONTRACTS.md + openapi.json + schema.d.ts regenerated; create_app accepts an optional pipeline for tests.
- Validation: real 70 s job, PATCH style -> new bytes/frame (~21 s), PATCH trim +2 s -> 45.7 s 1080x1920 (~22 s), peak RSS ~0.7 GB, preview frame differs from captionless render, 409/422 verified, make check green (290+35).

- Step 9 (2026-11-17): packaging — `openclip doctor [--json]` + `models download`, wheel bundles presets/fonts/licences/web (hatch hook), serve resolves OPENCLIP_WEB_DIR → `openclip/web` → `frontend/dist`; extras `asr|vision|llm|all`; Dockerfile/compose (unbuilt locally), release + docker workflows, `make build-wheel|doctor|docker-build`, README/DOCKER.md.
- Decision: font licences moved to `assets/licenses/` (libass tries every name in fontsdir); an API-only wheel is allowed, CI stages `frontend/dist` into `openclip/web`.
- Verified: wheel in a clean venv (8 presets, 12 TTFs, `doctor` exit 0 in 1.0 s); `make check` backend 318 tests green — pnpm absent, frontend leg skipped. Unverified: Docker build, workflows, frontend build.
