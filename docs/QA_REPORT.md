# QA Report — Phase 4, Pass A

**Date:** 2026-11-18 · **Branch:** `main` · **Role:** QA agent
**Scope:** robustness and known-bug pass over `backend/src/openclip` and `backend/tests`,
no product features. Every fix below ships with a regression test.

---

## 1. Method

1. Read `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, and the end of
   `docs/PROGRESS.md`, then skimmed `ingest/`, `transcribe/`, `analyze/`, `reframe/`,
   `captions/`, `render/`, `pipeline/`, `api/`.
2. Established a green baseline before touching anything (331 tests).
3. Fixed the three known bugs first, each with a regression test, then worked through the
   edge-case matrix below.
4. For every matrix row: reproduced the behaviour manually where possible (real CLI run,
   real HTTP server, real FFmpeg), then encoded the expectation as a test.
5. Defects were fixed in place only when small and local (all fixes are well under 50 lines
   of logic change and each has a regression test). Anything larger went to
   `docs/ISSUES_DRAFT.md`.

Media fixtures are generated on the fly with `ffmpeg -f lavfi` inside pytest `tmp_path`
directories (or `/tmp/ocqa2` for manual runs). Nothing was committed; the repository
contains no media, weights, or secrets.

## 2. Environment

| Item | Value |
| --- | --- |
| Machine | Intel N305, 8 logical cores, 6.9 GiB RAM, **no GPU** |
| Free RAM during the pass | 1.6 – 4.0 GiB (`MemAvailable`, fluctuates) |
| Disk free | ~3.4 GiB on the data volume |
| Python | 3.12.16 via `uv` (editable install, offline) |
| FFmpeg / ffprobe | 8.0.1 (libass `subtitles` filter, libx264, aac present) |
| Optional extras | `asr` (faster-whisper), `vision` (numpy, OpenCV), `yt-dlp` — all importable |
| LLM | none pulled or run in this pass (per instructions) |
| Frontend | **not tested** — `pnpm` is unavailable on this machine; the frontend leg of `make check` was skipped |
| Docker | **not tested** — Docker unavailable |

Whisper models `base.en` and `small.en` were already cached, so a short real transcription
was possible without a download; no long media was transcribed.

## 3. Known bugs — all fixed

### 3.1 Hashtags leak the product name (`#openclip`) — FIXED (P2)

`_hashtags()` and `_clean_hashtags()` padded short lists with a literal `#openclip`, and
`analyze/llm.py` padded LLM output the same way. A clip about the tool itself could also
produce `#openclip` from the transcript.

*Fix:* a new `analyze/tags.py` holds one banned vocabulary — engagement bait
(`#fyp`, `#viral`, …), product/platform meta words (`openclip`, `clip`, `video`,
`youtube`, `subscribe`, …) and spoken filler/quantifiers (`um`, `stuff`, `most`, `every`,
`whole`, …). Both the heuristic and the LLM paths consult it, and **lists are never padded**
— a short clean list beats a filler tag.

*Regression tests:* `test_phase4_bugs.py::test_openclip_word_never_becomes_a_hashtag`,
`::test_meta_and_filler_words_never_become_hashtags`,
`::test_hashtags_are_never_padded_with_a_fixed_tag`,
`::test_hashtags_skip_quantifiers_and_determiners`,
`::test_clean_hashtags_drops_llm_product_and_filler_tags`,
`::test_heuristic_metadata_hashtags_are_clean`.

*Verified on a real run:* a clip about "the most important thing about discipline" now
yields `['#discipline', '#early', '#important', '#people']`.

### 3.2 Non-English video yields garbage with no warning — FIXED (P2)

`fast` = `base.en` and `balanced` = `small.en` are English-only models. A German, Spanish
or Japanese video transcribes into confident nonsense and the job reports success.

*Fix:* `analyze/language.py` inspects **the transcript that already exists** — no second
model, no extra download, no extra pass. Four independent signals, each conservative:

| Signal | Threshold | Rationale |
| --- | --- | --- |
| non-Latin script share | ≥ 10 % of letters | CJK/Cyrillic/Arabic/… cannot be English |
| non-ASCII letter share | ≥ 6 % of letters | accented Latin languages land far above |
| dominant foreign function-word cluster | ≥ 3 hits and ≥ 8 % of words (es/fr/de/it/pt/nl) | catches Latin-script languages that share the ASCII alphabet |
| unknown-word share | ≥ 90 % with < 5 % English stopwords, ≥ 40 words | catches anything else |

Word-count guards (40 words, or 12 for the cluster check) keep short clips from producing
false warnings. A flagged source **keeps running**; `LANGUAGE_WARNING` is appended to the
job message and printed by `openclip run` and `openclip transcribe` on stderr. The check
wraps everything in `try/except` and can never fail a job.

*Regression tests:* `test_phase4_bugs.py::test_language_verdict` (en/de/es/zh),
`::test_language_warning_is_non_blocking_and_carries_a_message`,
`::test_language_check_never_raises_on_odd_input`,
`test_edge_pipeline.py::test_non_english_source_warning_reaches_the_job_message`,
`::test_english_source_has_no_warning`.

*Manual validation:* 13 languages (en, en-short, en-technical, es, fr, de, pt, it, nl, zh,
ru, ja) classified correctly, 0 false positives on English.

### 3.3 LLM judge exhausts RAM in `auto` mode — FIXED (P2)

On this machine a 70 s job took 10 extra minutes because the judge fought for RAM.

*Fix:* `core/memory.py` reads `MemAvailable` from `/proc/meminfo` (no new dependency) and
fails soft (unreadable → "unknown" → no restriction). In `analysis_mode=auto`, the judge is
skipped below **3 GiB**, `job.message` says so, and heuristic scores are used. An explicit
`analysis_mode=llm` still forces the judge. `openclip doctor` now reports when the guard
would fire.

*Regression tests:* `test_phase4_bugs.py::test_meminfo_parsing`,
`::test_meminfo_unreadable_fails_soft`, `::test_memory_guard_only_applies_to_auto_mode`,
`::test_auto_mode_skips_llm_when_memory_is_low`,
`::test_auto_mode_uses_llm_with_enough_memory`,
`::test_explicit_llm_mode_ignores_the_memory_guard`,
`test_edge_pipeline.py::test_low_memory_note_reaches_the_job_message`.

*Verified on a real run:* with `MemAvailable` at 1.6 GiB the job message read
`Low memory: 1.6 GB available (under the 3 GB LLM guard), so the LLM judge was skipped …`
and the job completed normally.

Documented in `docs/ARCHITECTURE.md` ("Resource guard (auto mode)" and "Highlights and
language").

## 4. Additional defects found and fixed

| # | Defect | Severity | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | Cancelling a job stored it as `failed` with code `cancelled`; the contract's `cancelled` status was unreachable and the UI showed an error banner for a user action | P2 | `Pipeline._finish_failure()` maps cancellations to `JobStatus.CANCELLED`, no error object | `test_edge_pipeline.py::test_cancel_during_each_stage_ends_as_cancelled` (ingest/transcribe/analyze/render), `test_edge_api_pipeline.py::test_cancelled_job_reports_cancelled_not_failed` || 2 | Cancelling during analysis or a clip render ended as `failed`: those stages raise a plain error and the runner only checked the error code | P2 | The runner also reads the cancel flag; a cancelled clip propagates instead of being logged as a render failure | same as above, `[analyze]` and `[render]` cases |
| 3 | Deleting a job while its worker ran re-inserted the SQLite row from the next checkpoint ("undelete") | P1 | `Pipeline` remembers deleted ids and drops every later `save_job` for them | `test_edge_api_pipeline.py::test_delete_during_run_does_not_resurrect_the_job`, `::test_delete_running_job_via_api_returns_204_and_leaves_no_row` |
| 4 | Upload named `..` (or `.`) resolved to the upload directory itself → `500 io_error` | P2 | `_safe_upload_name()` rejects `.`/`..` and maps them to `upload.bin` | `test_edge_api_pipeline.py::test_upload_names_are_sanitised[..]` |
| 5 | A 300-character upload name overflowed the filesystem → `500 io_error` | P2 | Names are capped at 180 characters, keeping the extension | `::test_very_long_upload_name_is_capped` |
| 6 | Upload hitting `ENOSPC` answered `500 io_error`, so the UI showed the wrong message | P2 | `507 disk_full` with the UI's own message; the partial upload directory is removed | `::test_upload_disk_full_answers_507_and_cleans_up` |
| 7 | Malformed JSON / wrong field types answered FastAPI's `{"detail": [...]}`, a different shape from every other error | P3 | A `RequestValidationError` handler answers `422 {"code":"invalid_request","message":…}` | `test_edge_api_pipeline.py::test_malformed_json_body_uses_the_shared_error_shape`, `::test_unknown_fields_and_bad_types_are_rejected_cleanly` |
| 8 | Subscribing to an **already finished** job left the SSE stream open until the first keepalive (browser spinner forever) | P1 | The stream emits `done`/`error` from the persisted state and closes immediately | `::test_sse_done_terminates_the_stream`, `::test_sse_failed_job_emits_a_terminal_error` |
| 9 | SSE had no `id:` and no `Last-Event-ID` support, so a reconnect either duplicated or lost events | P2 | Monotonic `id:` per frame, per-job replay buffer, `Last-Event-ID` resumes after the last seen frame | `::test_sse_frames_carry_ids_and_replay_from_last_event_id`, `::test_sse_ignores_a_garbage_last_event_id`, `::test_event_bus_replays_only_newer_frames` |
| 10 | A clip that failed to render was reported as a plain string with no code; `disk_full` was indistinguishable from `render_failed` | P2 | `RenderFailure(code, …)` and `RenderCancelled(code="cancelled")`; the message keeps `clip-001 (disk_full): …` | `test_edge_analyze_render.py::test_render_disk_full_is_detected_when_the_probe_fails`, `::test_render_failure_message_is_not_disk_full_on_other_errors`, `test_edge_pipeline.py::test_disk_full_during_render_is_reported_as_disk_full` |
| 11 | A source shorter than `min_clip_seconds` produced a short clip that **could never be re-styled**: every PATCH answered `422 invalid_duration` | P1 | Duration is validated only when the request actually moves the bounds, and a short source exempts its own length | `test_edge_api_pipeline.py::test_patch_style_change_works_when_the_source_is_shorter_than_min` |
| 12 | Silent media reached analysis and failed with a generic `pipeline_failed` ("Transcript contains no sentences"), so the UI never showed its dedicated no-speech message | P2 | The orchestrator asks transcription for `strict=True`; such jobs fail with `no_speech` | `test_edge_pipeline.py::test_empty_transcript_fails_with_no_speech` |
| 13 | The low-memory note was overwritten by the next stage message | P3 | Guard notes (language + memory) are collected and re-attached to the final job message | `test_edge_pipeline.py::test_low_memory_note_reaches_the_job_message` |
| 14 | Deleting a job never removed its upload copy, leaking one full source file per deleted job (two 148 KiB copies left in a real session) | P1 | The upload row and directory are removed on delete, but only when no other job references that upload | `test_edge_api_pipeline.py::test_delete_releases_the_upload_copy`, `::test_delete_keeps_an_upload_still_used_by_another_job` |
| 15 | A resumed job trusted its cached `media.json` and skipped `check_ingest_limits`, so media ingested under a looser limit kept running after the limit was lowered (a 2 h file resumed with a 1 min cap) | P1 | The guard runs on the resume path too | `test_edge_pipeline.py::test_resume_revalidates_limits_against_the_current_config` |

Contract impact: `docs/CONTRACTS.md` gained a "Job status" section, an "HTTP errors and
uploads" section, and the SSE section now documents `id:`/`Last-Event-ID`. `docs/openapi.json`
and `frontend/src/api/schema.d.ts` were regenerated (`make openapi` equivalent); the only
OpenAPI change is the SSE route's description string — **no path, model or status-code shape
changed**.

## 5. Edge-case matrix

Legend — **PASS**: behaved as expected, test added. **FIXED**: defect found and repaired in
this pass, test added. **OPEN**: documented in `docs/ISSUES_DRAFT.md`. Severity: P1 data
loss/crash, P2 wrong result, P3 cosmetic.

### 5.1 Ingest and media shape

| Case | Expected | Actual | Verdict | Sev |
| --- | --- | --- | --- | --- |
| Corrupted file (random bytes, `.mp4`) | clean `unsupported_file` | `unsupported_file`, ffprobe detail included | PASS | — |
| Truncated MP4 (moov atom missing) | clean error, no crash | `unsupported_file` | PASS | — |
| Zero-byte file | clean error | `unsupported_file` (probe of the copy) | PASS | — |
| Audio-only file (`.mp3`) | rejected: no video stream | `unsupported_file` "does not contain a video stream" | PASS | — |
| Video without an audio track | rejected with `no_audio` | `no_audio` | PASS | — |
| Silent audio (track present, no speech) | `no_speech`, no clips written | was `pipeline_failed`; now `no_speech` | FIXED | P2 |
| Extremely short video (0.6 s) | ingests; one short clip later | ingests, one clip, flagged in the rationale | PASS | — |
| Very long duration (> 240 min) | `too_long`, faked probe | `too_long`, message names the limit; exactly at the limit is allowed and the limit is configurable | PASS | — |
| Very large upload | `413 too_large`, nothing left on disk | `413` and the upload directory is removed | PASS | — |
| Odd filenames (accents, spaces, brackets, quotes, leading dash, very long) | ingest copies to a canonical name | all ingest; the work copy is always `media.mp4` | PASS | — |
| Unsupported extension, valid content | rejected by extension | `unsupported_file`, message names the extension | PASS | — |
| Valid extension, wrong content | rejected by probe | `unsupported_file` | PASS | — |
| Invalid / unreachable URL | `invalid_url`, no network | `invalid_url` (DNS failure path) | PASS | — |
| Non-HTTP scheme (`ftp://`, `file://`) | rejected | `invalid_url` at ingest; `file://` is additionally rejected by the `JobSource` contract (422) | PASS | — |
| Private / localhost URL | rejected | `http://127.0.0.1`, `http://localhost`, `http://[::1]` all rejected | PASS | — |
| Redirect/DNS re-binding to a private address | rejected | all resolved addresses are checked, so the rebinding case is rejected | PASS | — |
| Vertical (already 9:16) source | pass-through full frame | `crop_window()` returns `None` → full-frame `fill` | PASS | — |
| Very wide / very low resolution | a valid 9:16 window inside the frame | 640×64 and 64×48 both produce even-dimension windows | PASS | — |
| Variable frame rate | finite, positive fps | `r_frame_rate` → finite fps | PASS | — |
| No face in the video | `fit` fallback | `auto` → `fit`, centred `fill` when forced | PASS | — |
| Several faces | valid path, subject chosen with hysteresis | keyframes stay inside the frame; no invalid geometry | PASS | — |

### 5.2 Pipeline, queue, cancellation

| Case | Expected | Actual | Verdict | Sev |
| --- | --- | --- | --- | --- |
| Cancel during ingest | `cancelled`, no error object | `cancelled` | FIXED | P2 |
| Cancel during transcribe | `cancelled` | `cancelled` | FIXED | P2 |
| Cancel during analyze | `cancelled` | was `failed`; now `cancelled` | FIXED | P2 |
| Cancel during render | `cancelled` | was `completed` (the clip error was swallowed); now `cancelled` | FIXED | P2 |
| Cancel a queued job | never starts | never starts; stays `cancelled`/`queued` | PASS | — |
| Delete a job while it runs | gone, not resurrected | was resurrected as `failed`; now gone with its directory, and its upload copy is released when unused | FIXED | P1 |
| Two jobs queued | FIFO, one at a time | FIFO order asserted, peak concurrency 1 | PASS | — |
| Delete a job that used an upload | upload copy released | now removed when no other job references it; kept when shared | FIXED | P1 |
| Resume with a lowered duration limit | guard still enforced | now `too_long`; previously the cached media bypassed the check | FIXED | P1 |
| Server restart with a running job | `failed` / `interrupted`, resumable | verified with `kill -9` on a real server: row stayed `running`, restart marked it `interrupted`, artifacts kept | PASS | — |
| Resume after a mid-render interruption | no partial or duplicate outputs | resume reuses artifacts byte-for-byte; exactly four files per clip; ingest limits re-validated | PASS | — |
| Disk full (mocked write/space check) | clean coded error, no half files | render → `disk_full`; upload → `507 disk_full` with the directory removed; `.part` files removed | FIXED | P2 |
| One clip fails, others succeed | job completes, failure named | job `completed`, message names the clip and its code | PASS | — |
| Progress monotonic | never goes backwards, stays in 0–1 | asserted over a full run | PASS | — |
| `results.json` | one row per clip | matches `job.clips` | PASS | — |
| Crop retiming on PATCH | shift + clamp, `fit` when out of window | both asserted | PASS | — |

### 5.3 HTTP API

| Case | Expected | Actual | Verdict | Sev |
| --- | --- | --- | --- | --- |
| Malformed JSON body | `ErrorResponse` shape | was `{"detail": …}`; now `422 invalid_request` | FIXED | P3 |
| Wrong field types / unknown fields | 422 `ErrorResponse` | `422 invalid_request`; extra `JobOptions` keys are ignored by the model | FIXED | P3 |
| Huge JSON body (~12 MB) | rejected, no job created | `400 invalid_source`, no row created | PASS | — |
| Wrong ids (`notanid`, 31/33 hex, `%2e%2e%2f`) | 404 everywhere | 404 on job, events, zip and DELETE | PASS | — |
| Path traversal on every file route | 404/400/422 | rejected on all four artifact routes | PASS | — |
| Range requests | 206 with `Content-Range`, 416 past the end, 400 on garbage | all four shapes asserted | PASS | — |
| Conditional GET | 304 on matching ETag | 304 | PASS | — |
| ZIP of an unfinished job | 409 `not_ready` | 409 | PASS | — |
| ZIP with a missing artifact | skips it, still a valid zip | valid zip | PASS | — |
| Upload odd names | sanitised, stored | `.`/`..` → `upload.bin`, accents → underscores, 400 chars capped at 180 | FIXED | P2 |
| Upload over the limit | 413, nothing left | 413, directory removed | PASS | — |
| SSE on a running job | snapshot first, then live events | confirmed with a real server | PASS | — |
| SSE reconnect with `Last-Event-ID` | resumes after the last seen frame, no duplicate snapshot | `id: 3` first for `Last-Event-ID: 2` | FIXED | P2 |
| SSE on a finished job | terminal event and close | was hanging; now `done`/`error` immediately | FIXED | P1 |
| Clip-scoped SSE error | stream stays open | `clip_id` error is not terminal; a later `done` closes it | FIXED | P2 |
| PATCH same bounds | 200, re-render | 200 | PASS | — |
| PATCH minimal / maximal length | 422 at both extremes | 422 `invalid_duration` | PASS | — |
| PATCH unknown style | 422 `unknown_style` | 422 `unknown_style` | PASS | — |
| PATCH style on a short source | 200 | was 422 on every request; now 200 and re-renders | FIXED | P1 |
| PATCH concurrent request | 409 `rerender_in_progress` | 409 (checked manually against a live server too) | PASS | — |
| PATCH failed re-render | old artifacts kept, no temp dir left | byte-identical old file, no `.rerender-*` left | PASS | — |
| PATCH re-render result | new bytes, new ASS style | real run: Anton style present, new 1080×1920 MP4 | PASS | — |

Totals: **PASS 45 · FIXED 15 · OPEN 0** across the matrix rows above
(P1: 5 fixed, P2: 9 fixed, P3: 1 fixed).

Separately, `docs/ISSUES_DRAFT.md` holds **9 OPEN items that are outside this pass's fix
budget** (all low/medium severity, none a release blocker): orphaned uploads with no job,
no pre-flight disk-space check, cached stages ignoring changed `JobOptions`,
`openclip run --resume` being a no-op, Latin-only glyph coverage in captions, no
between-clip memory check, `openclip transcribe` lacking `--language`/`--model`, no
frontend contract test, and the stale `#openclip` example in the contract docs.

## 6. Test suite state

| Metric | Before | After |
| --- | --- | --- |
| Backend tests collected | 331 | 470 (467 run, 3 deselected `slow`) |
| New matrix files | — | `test_edge_ingest.py` (25), `test_edge_analyze_render.py` (19), `test_edge_api_pipeline.py` (50), `test_edge_pipeline.py` (22), `test_phase4_bugs.py` (19) |
| `ruff check` / `ruff format --check` / `pyright` | green | green (0 errors) |

Commands run: `ruff check src tests`, `ruff format --check src tests`, `pyright`,
`pytest -q` (all offline, `UV_CACHE_DIR=../.uv-cache uv run --offline`).

Manual end-to-end checks (all with a temporary `/tmp/ocqa2` data dir, no committed media):

* `openclip run` on an 11 s synthetic speech clip → 1 clip, 1080×1920 H.264 + AAC, ASS
  captions burned in, preview 360×640, thumbnail 540×960, hashtags clean.
* Real server (`openclip serve`) → `health`, `caption-styles`, job polling, Range 206,
  traversal 404, `download.zip` (5 entries), SSE snapshot + immediate `done`, PATCH style
  200 + 409 on the concurrent retry, DELETE while running → 204 then 404.
* `kill -9` mid-job → restart marked the job `failed`/`interrupted` with its artifacts
  intact; re-submitting the same job resumed and completed with no duplicate outputs.
* Silent 30 s source → `no_speech`.

## 7. What could not be tested

| Area | Why | Risk |
| --- | --- | --- |
| **Frontend** (vitest, Playwright, typecheck) | `pnpm` is not installed on this machine | The TS client must be regenerated and its tests run where `pnpm` exists; `make check`'s frontend leg is unverified here |
| **Docker build / compose** | Docker is not available | `docker/Dockerfile` and the compose file remain unbuilt |
| **GPU encoders (VAAPI/QSV)** | no GPU on the reference machine | hardware encode path still exercised only by the capability probe, which correctly falls back to x264 |
| **Real LLM judge** | explicitly out of scope for this pass | the memory guard and the judge call path are covered with fakes; judge quality is unchanged from Step 10 |
| **Long media (> 10 min) real transcription** | instruction: no long runs, no model pulls | the duration guard was tested with a faked probe; sustained-memory behaviour on a 4-hour input is unverified |
| **True disk-full filesystem** | can't shrink the volume safely | `ENOSPC` is simulated via patched writes; `_is_disk_full()` also verified against a read-only directory (returns False, so EACCES is not mistaken for a full disk) |
| **Windows / macOS filesystem behaviour** | Linux-only host | name-length cap (180) and non-ASCII sanitisation are chosen to be portable but unverified off Linux |

## 8. Assumptions recorded

1. **English-only v0.1** is a hard product statement, so the language safeguard warns and
   never blocks. A user who really wants a non-English video can still get output (garbage
   in, garbage out) but is told why.
2. **3 GiB** is the right `MemAvailable` floor for a 2–3 GB judge on this machine; the
   constant is `LLM_MIN_AVAILABLE_BYTES` in `analyze/service.py` if it needs retuning.
3. **`done` carries `cancelled`** as well as `completed`. That keeps one terminal event for
   the stream while the `status` field stays truthful; `docs/CONTRACTS.md` now says so.
4. A 64-frame replay buffer per job is enough for SSE reconnects; a client offline longer
   than that falls back to the snapshot, which is always correct (never stale data, just
   fewer intermediate events).
5. Upload names are sanitised **at rest** (the stored name), not only at display time, so
   the download `filename` and any later filesystem use agree.
6. The pre-existing `# openclip` values in `docs/CONTRACTS.md`'s `Clip` example and in two
   test fixtures are illustrative JSON, not runtime output; they were left alone in this
   pass to avoid touching unrelated contract examples, and are tracked as issue #9.
7. "Small and local" was read as "one behaviour, one guarded path, one regression test".
   Fixes 14 and 15 touch storage and resume semantics but each is a dozen lines and has
   its own test, so they were done in place rather than deferred.

## 9. Maintainer actions

1. Run `make check` (or at least `pnpm lint && pnpm typecheck && pnpm test --run`) on a
   machine with `pnpm` — the regenerated `frontend/src/api/schema.d.ts` is committed but
   untested. The frontend's SSE consumer tolerates both a bare `Clip` and the wrapped
   `{"clip": ...}` payload, so the added `id:` lines should be transparent to it.
2. Confirm the OpenAPI regeneration is what you expect: it appears in the `fix(api,pipeline)`
   commit and is a description-only change on the SSE route — no path, model or status-code
   shape moved.
3. Decide whether `no_speech` should be treated as `failed` or a distinct terminal state;
   the current implementation keeps `failed` + error code, which matches the existing UI
   mapping (`UI_SPEC §8`).
4. Review `docs/ISSUES_DRAFT.md`. None are release blockers, but issue #3 (cached stages
   ignore changed `JobOptions`) and issue #4 (`--resume` does nothing) are user-visible
   honesty problems worth scheduling before the 0.1.0 tag.
5. If the 3 GiB memory floor is wrong for your machine, the single constant to tune is
   `LLM_MIN_AVAILABLE_BYTES` in `backend/src/openclip/analyze/service.py`.
