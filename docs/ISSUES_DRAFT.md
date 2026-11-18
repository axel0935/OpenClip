# Issue drafts — Phase 4 / Pass A

Every OPEN item from `docs/QA_REPORT.md`, written ready to paste into GitHub.
Nothing here is a release blocker for 0.1.0; the "good first issue" label marks the
self-contained ones.

Labels used: `bug`, `enhancement`, `good first issue`, `area:api`, `area:pipeline`,
`area:storage`, `area:captions`, `area:docs`, `severity:low`, `severity:medium`.

---

## 1. Uploads that never become a job are never cleaned up

**Labels:** `bug`, `area:storage`, `severity:medium`, `good first issue`

### Description

`POST /api/uploads` writes a full copy of the source into `<data_dir>/uploads/<id>/`. The
copy is only released when a job created from that upload is deleted. If the user uploads
and then reloads the page, cancels the import, or the `POST /api/jobs` call fails, the
upload stays on disk forever: there is no deletion route, no TTL and no `openclip` command
to prune it. Uploads are capped at 4 GB each, so a handful of abandoned imports can fill
the data volume — which then surfaces as `disk_full` during rendering.

### Steps to reproduce

1. Start the server with a fresh `OPENCLIP_DATA_DIR`.
2. Upload a file: `curl -F "file=@video.mp4" http://127.0.0.1:8000/api/uploads`.
3. Do **not** create a job from the returned `upload_id`.
4. Restart the server and inspect `$OPENCLIP_DATA_DIR/uploads/`.

### Expected

Unused uploads are removed after a bounded period (or by an explicit command), and the
disk usage of the data directory is observable.

### Actual

The upload directory and the full-size file remain indefinitely. Nothing lists them and
nothing deletes them.

### Suggested fix

Pick one (or both):

* Add an age-based sweep at startup next to `store.mark_interrupted()`: delete upload
  directories with no matching job row and an mtime older than, say, 24 h. Put the cutoff
  in `Settings` (`upload_retention_hours`) so it is configurable.
* Add `openclip clean [--dry-run]` that reports and removes orphaned uploads, plus the
  matching data-directory size in `openclip doctor`.

Add a test with two uploads (one referenced by a job, one not) asserting only the orphan is
removed.

---

## 2. Rendering does not verify free disk space before encoding

**Labels:** `enhancement`, `area:pipeline`, `severity:medium`

### Description

Render only learns that the disk is full when FFmpeg fails, at which point an encode that
may have run for minutes is thrown away. The error is now reported correctly
(`disk_full`, see the Pass A fixes), but a pre-flight check could fail in milliseconds
instead.

### Steps to reproduce

1. Fill the data volume to under ~200 MB free.
2. Run a job on a 10-minute source with a clip of 60 s.
3. Watch the render stage run and then fail.

### Expected

The job fails fast with `disk_full` and a message naming the free space and the requirement,
before spending CPU time.

### Actual

FFmpeg encodes until the filesystem refuses writes, the partial file is discarded
(correct), and the job reports `disk_full` (correct) after the wasted work.

### Suggested fix

In `render_clip`, before spawning FFmpeg, estimate the requirement as
`duration * bitrate_estimate` (1080×1920 H.264 CRF 21 ≈ 6–9 Mbit/s, so ~1 MB/s is a safe
bound) and compare with `shutil.disk_usage(out_path.parent).free`. Fail with
`RenderFailure("disk_full", …)` when the estimate does not fit. `openclip doctor` already
reports free space, so the message can reuse its wording.

---

## 3. `JobOptions` changes are not re-applied to cached stages

**Labels:** `bug`, `area:pipeline`, `severity:medium`

### Description

Stage caches are keyed by input hash plus the parameters that stage actually used
(transcription does this correctly — see `transcribe/cache.py`). The orchestrator's own
markers, however, are plain file existence checks: `media.json`, `clips.json` and
`render`'s output files. A job resumed with different `JobOptions` (say `max_clips` 8 → 3,
or `analysis_mode` `auto` → `heuristic`) reuses `clips.json` unchanged, so the new options
silently have no effect. Only PATCH re-renders honour a style change.

### Steps to reproduce

1. Run a job to completion, producing `clips.json` with 4 clips.
2. Edit the persisted job row's `options.max_clips` to 1 (or re-submit the same job with
   different options through the CLI/API).
3. Resume it.

### Expected

The stage is recomputed (or the job reports that the cached result does not match the
requested options).

### Actual

The job completes with the cached 4 clips; `max_clips` was ignored.

### Suggested fix

Write a small `stage_marker.json` next to each stage artifact containing the parameters
that affect it (a dict of the relevant `JobOptions` fields plus a schema version), and on
resume compare it with the current options; recompute on mismatch. `clips.json` should be
keyed on `min_clip_seconds`, `max_clip_seconds`, `max_clips` and `analysis_mode`; `media.json`
on nothing (ingest has no options beyond limits, which are re-validated now); rendered
outputs on the clip bounds and the caption style id.

---

## 4. `openclip run --resume` is accepted but ignored

**Labels:** `bug`, `cli`, `severity:low`, `good first issue`

### Description

`openclip run` declares `--resume`, with help text "Resume an interrupted job.", but the
flag is never read: the command always creates a new job id and a fresh job directory. A
user who follows the help text gets a full re-run (and a second copy of every artifact)
instead of a resume.

### Steps to reproduce

1. Interrupt a run (Ctrl-C).
2. Re-run with `--resume`.

### Expected

The interrupted job is resumed (its completed stages are reused) or the command clearly
says a new job is being started.

### Actual

A new job id is created; `--resume` has no effect and is not mentioned in the output.

### Suggested fix

Either implement it — resume needs a job id, so change the flag to
`--resume <job-id>` and call `Pipeline.execute` on the loaded job — or drop the flag and
document `openclip run` as always starting fresh. Removing a flag that does nothing is
acceptable for 0.1.0; the important part is that the help text stops promising behaviour
that does not exist.

---

## 5. Captions assume the primary font can draw every script

**Labels:** `enhancement`, `area:captions`, `severity:low`

### Description

`build_ass` emits text verbatim and libass resolves glyphs through the bundled fonts
(12 OFL TTFs, all Latin-focused). CJK, Arabic, Hebrew and Devanagari text therefore render
as blank boxes (missing glyphs) in the burned-in captions. The generator does not crash and
the ASS file is valid — verified in this pass with Chinese and Arabic strings — but the
output is unusable for such sources.

### Steps to reproduce

1. Build captions for a transcript containing Chinese text:
   `build_ass(transcript_with_cjk, clip, load_presets()["clean-pop"])`.
2. Render it (or open the ASS in a player configured with only the bundled fonts).

### Expected

Either the text renders, or OpenClip says up front that the script is not supported by the
bundled fonts.

### Actual

Boxes/tofu; no warning anywhere.

### Suggested fix

Short term (cheap): the language safeguard already flags non-English input; extend the
warning to mention that caption glyph coverage is Latin-only. Medium term: detect a
non-Latin script share above a threshold in `captions/text.py` and prepend a note to the
job message. Long term: add a CJK/Arabic OFL font and a per-script `font_family` fallback
list in the style preset (libass honours a comma-separated family list).

---

## 6. Long jobs never release memory between clips

**Labels:** `enhancement`, `area:pipeline`, `severity:low`

### Description

The pipeline is sequential and each stage releases its own model (Whisper via `gc.collect()`
after transcription, the LLM via `keep_alive: 0`). The per-clip loop then imports OpenCV
once and keeps the YuNet detector for the whole run. On the 8 GB reference machine that is
fine, but a 90-minute source with 8 clips holds the detector plus FFmpeg plus the page cache
until the very end, and the loop has no memory-pressure check between clips.

### Steps to reproduce

1. Run a job with `--max-clips 8` on a long source.
2. Sample `MemAvailable` during the render loop.

### Expected

Memory pressure is checked between clips, and the run degrades gracefully (for example by
skipping the face detector and using `fit`) rather than entering swap.

### Actual

The detector stays resident; no check exists. (Not observed to fail on this machine — the
measured peak was 1.48 GB for a 3-minute source — but nothing prevents it on longer input.)

### Suggested fix

Reuse `core/memory.py`: between clips, if `MemAvailable` falls below a floor, drop the
detector reference and let the next clip use the `fit` path, recording the reason in the job
message. This is a natural extension of the LLM guard added in Pass A.

---

## 7. `openclip transcribe` cannot force a specific model

**Labels:** `enhancement`, `cli`, `severity:low`, `good first issue`

### Description

`openclip run` accepts `--profile fast|balanced|quality` and `analysis_mode`, but
`openclip transcribe` only exposes `--profile`. There is no way to test a specific
multilingual model (for example `large-v3` without the `.en` suffix) through the CLI, and
`TranscribeConfig` already has a `language` field that the CLI never sets.

### Steps to reproduce

1. `openclip transcribe video.mp4 --workdir w --profile quality`
2. Try to select a non-English model or a language hint.

### Expected

`--model` (or `--language`) is available for the standalone stage command, as it is for the
full pipeline.

### Actual

Only `--profile` exists; `language` is always the default `en`.

### Suggested fix

Add `--language` (default `en`) and optionally `--model` to the `transcribe` command, map
them onto `TranscribeConfig`, and mention the multilingual path in the help text. Small,
self-contained, and it makes the language safeguard testable by hand.

---

## 8. No test covers the frontend contract against the real API

**Labels:** `test`, `area:api`, `severity:low`

### Description

`frontend/src/api/schema.d.ts` is generated from `docs/openapi.json` and committed, but
nothing in CI compares the generated client against the server's actual responses. The
Pass A changes (cancelled status, `invalid_request` errors, SSE `id:` frames) were verified
by hand against a live server, and the generated file is committed, but the frontend tests
that would catch a mismatch cannot run on a machine without `pnpm`.

### Steps to reproduce

1. Change an error code or a response field in `api/app.py`.
2. Run `make openapi` and `make check` on a machine with `pnpm`.

### Expected

A failing test names the mismatch.

### Actual

`test_openapi_matches_docs` catches OpenAPI drift, but nothing asserts that the TypeScript
client's parsing still matches real payloads.

### Suggested fix

Add a small Playwright or vitest integration test that boots the FastAPI app with a fake
pipeline (the fixtures in `test_pipeline_api.py` show how) and asserts the client parses a
`cancelled` job, an `invalid_request` error and an SSE frame with an `id:`. Alternatively
add an `openapi-typescript --check` step so a stale `schema.d.ts` fails CI.

---

## 9. `docs/CONTRACTS.md` example clips still show `#openclip`

**Labels:** `docs`, `severity:low`, `good first issue`

### Description

The `Clip` example in `docs/CONTRACTS.md` and two test fixtures still contain
`"hashtags": ["#openclip"]`. Runtime output can no longer produce that tag (Pass A), so the
contract example now contradicts the product rule it documents.

### Steps to reproduce

Read the `Clip` row of the model table in `docs/CONTRACTS.md`.

### Expected

The example shows a realistic topic tag, e.g. `["#discipline", "#focus"]`.

### Actual

It shows the product name, which is exactly what the hashtag rules forbid.

### Suggested fix

Update the example in `docs/CONTRACTS.md` (and the two fixtures in `tests/test_models.py`
and `tests/captions_helpers.py`) to a neutral topic tag. Behaviour does not change; only the
documentation stops contradicting itself.
