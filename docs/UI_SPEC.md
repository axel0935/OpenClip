# OpenClip — UI Specification (v0.1)

Three-screen web UI. Single local user, English only, dark by default. For Step 4 (React + Vite + Tailwind + shadcn/ui). Sizes are CSS px; the video canvas is 1080×1920.

## 1. Principles

- **Three states, one route.** Import, Processing and Results are states of `/` and `/jobs/:id`, never a wizard.
- **Honest about time.** Anything over 10 s shows a rough cost before it starts and elapsed time while it runs.
- **The clip is the interface.** Video leads, chrome recedes. Colour is reserved for score badges, status dots and presets.
- **Quiet local promise.** No account, no sync, no "upload". "On this machine" appears once per screen, never as a banner.

## 2. Design tokens

Paste into `frontend/src/index.css`, replacing the scaffold's shadcn zinc defaults and five-colour `@theme inline` map. Ratios are measured (WCAG 2.2), not estimated.

| Token | Use | Dark | Light | Ratio D/L |
|---|---|---|---|---|
| `--background` | app canvas | `#0B0C0E` | `#FAFAFA` | — |
| `--card` (surface) | cards, sheets, rows | `#141619` | `#FFFFFF` | text on card 16.5 / 17.8 |
| `--popover` | menus, tooltips | `#17191D` | `#FFFFFF` | — |
| `--foreground` | primary text | `#F3F4F6` | `#17181A` | 17.8 / 17.0 on bg |
| `--muted-foreground` | labels, secondary | `#9BA2AB` | `#63676D` | 7.0 / 5.7 on card |
| `--muted` | hover fills, chips | `#1B1E22` | `#F1F1F2` | — |
| `--border` | hairlines | `#2A2E34` | `#E5E5E7` | decorative |
| `--input` | control boundary | `#616A75` | `#8E949C` | 3.3 / 3.1 on card |
| `--primary` | primary button, progress | `#F3F4F6` | `#17181A` | — |
| `--primary-foreground` | text on primary | `#0B0C0E` | `#FAFAFA` | 17.8 / 17.0 |
| `--ring` | focus ring | `#F3F4F6` | `#17181A` | 16.5 / 17.8 |
| `--success` | done step | `#4CC38A` | `#146046` | 8.2 / 7.5 on card |
| `--warning` | long-video notice | `#E3A63C` | `#8A5A0F` | 8.5 / 5.9 on card |
| `--destructive` (`--danger`) | errors | `#F26874` | `#B4242F` | 6.1 / 6.5 on card |
| `--score-low` 0–39 | badge | `#A6ADB5` | `#5A5F66` | 6.5 / 5.4 |
| `--score-medium` 40–69 | badge | `#E9AE45` | `#7A4F0C` | 7.4 / 5.9 |
| `--score-high` 70–100 | badge | `#57D19B` | `#16704A` | 7.6 / 5.1 |

Badges are the tier colour on a 12 % tint of itself over `--card`. Keep `--accent` as shadcn's hover surface.

**Shape, space, type.** `--radius` 10 (inputs 8, cards 14, sheets 16, pills 999). Spacing 4·8·12·16·24·32·48·64; gutter 24/16; content 1120, results 1280. `--font-sans`: Inter (OFL, self-hosted, swap) → system stack; `--font-mono` for ids and timecodes. Scale 12/13/14/16/20/24/30/38; weights 400·500·600; line-height 1.35, 1.15 above 24 px. Motion 120 hover · 180 enter · 240 sheet · 320 step, `cubic-bezier(.2,.8,.2,1)`.

## 3. Layout and navigation

- **Top bar 56 px, sticky:** wordmark left (never icon-only ≥ 640 px); `History` and `Settings` Sheets right, theme inside Settings.
- **Canvas** centred, 24 px gutters. No sidebar, no global nav — History is the only navigation.
- **Mobile < 640:** two icon buttons, drawers become 92 vh sheets, primary action in a sticky bottom bar.

```
┌────────────────────────────────────────────────────┐
│ OpenClip                      [History] [Settings] │
│     Paste a link, or drop a file.                  │
│  ┌──────────────────────────────────────────────┐  │
│  │ https://youtube.com/watch?v=…  [Fetch]       │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │ Drop a video · MP4 MOV MKV WebM M4V          │  │
│  └──────────────────────────────────────────────┘  │
│ ▸ Options · 20–90 s · 8 clips · Auto · Clean Pop   │
│                                 [ Start clipping ] │
└────────────────────────────────────────────────────┘
```

## 4. Screen 1 — Import

- **URL field:** placeholder `https://youtube.com/watch?v=…`; Enter fetches. The client checks `http(s)`, the server validates the rest.
- **Drop zone:** MP4, MOV, MKV, WebM, M4V; ≤ 4 GB, ≤ 3 h. A dropped file posts to `POST /api/uploads` and becomes a chip (filename · size · duration) over a determinate progress bar — it never leaves the machine, so the UI says "copying", not "uploading".
- **Options (collapsed),** summary keeps values visible:
  - Clip length: two-thumb slider 20–90 s + numeric inputs; min > max swaps with a toast.
  - Clips: stepper 1–8 (default 8).
  - Reframing: Auto · Follow face · Fit with blurred background.
  - Caption style: 120 px snap-scroll row of picker tiles (§7).
- **Primary:** "Start clipping", disabled until the source is valid, reason printed above it.
- **CPU notice** at ≥ 45 min: "This video is 1 h 12 min. Expect roughly 35–55 min on this CPU." + "Why so long?" popover.

## 5. Screen 2 — Processing

Nine `Stage` values → five steps. Only overall `progress` is shown: it is the one number the backend guarantees.

| # | Stage(s) | Label | Sub-line |
|---|---|---|---|
| 1 | ingest | Getting the video | Reading duration, audio, frame rate |
| 2 | transcribe | Transcribing speech | Word-level timing, English |
| 3 | segment, score, select | Finding the best moments | Hooks, energy, questions |
| 4 | reframe | Following the speaker | Tracking faces, smoothing the crop |
| 5 | captions, render, export | Rendering clips | Burning captions into 1080×1920 |

- **Progress:** determinate bar + five-row step list (done/active/pending) + `job.message`.
- **Time:** elapsed `m:ss`. ETA only after 20 s and 5 %: a range under 25 % ("18–30 min left"), else a rounded value, always "· rough estimate".
- **Cancel:** → "Stop this job? Clips already rendered are kept."
- **Leave notice:** "You can close this tab — the job keeps running on this machine." Muted: "Sleep pauses the job." Job id in mono, copyable.

```
┌────────────────────────────────────────────────────┐
│ interview-ep-42.mp4                        1:12:04 │
│ ████████████████████░░░░░░░░░░░░░░   54% · 18:42   │
│ ✓ Getting the video                                │
│ ✓ Transcribing speech                              │
│ ● Finding the best moments    hooks, energy…       │
│ ○ Following the speaker                            │
│ ○ Rendering clips                    ≈ 12 min left │
│ Close this tab — the job keeps running.            │
│                                     [ Cancel job ] │
└────────────────────────────────────────────────────┘
```

## 6. Screen 3 — Results / Editor

- **Header:** source title (ellipsised), "8 clips · 41 min saved", sort Select (Score ↓ · Longest · Shortest · Source order), "Download all (.zip)", "New video".
- **Grid:** 9:16 cards — thumbnail, score badge, duration pill, title, hook, footer "Clean Pop · Edit". Hover: border → 20 % foreground, play glyph fades in. 3 / 2 / 1 columns at 1280 / 768 / below.
- **Detail Sheet** (520 px; bottom sheet on mobile): player ≤ 60 vh, live caption preview, trim, style switcher, actions.
  - **Trim:** ±0.1 s nudge (±0.5 s with Shift) + slider; readout turns `--warning` under 20 s. Local until re-render.
  - **Preview:** JASSUB draws the chosen preset instantly — "Preview only — captions are burned in on re-render."
  - **Re-render:** "Re-render clip · ≈ 15 s", then "Rendering…" with inline progress; trim and style lock meanwhile.
  - **Actions:** Download MP4, Copy title, Copy hashtags.
- **Download all:** server zip, button shows %, then "8 clips · 214 MB".

```
┌────────────────────────────────────────────────────┐
│ interview-ep-42.mp4   8 clips · 41 min saved       │
│ Sort [Score ↓]   [Download .zip]   [+ New]         │
│ ┌────┐ ┌────┐ ┌────┐                               │
│ │ 92 │ │ 87 │ │ 74 │   9:16 cards: score           │
│ │ ▷  │ │ ▷  │ │ ▷  │   badge, duration,            │
│ │0:47│ │0:38│ │1:12│   title, hook, style          │
│ └────┘ └────┘ └────┘                               │
└────────────────────────────────────────────────────┘
```

## 7. Caption style picker

- **Cards:** 8 tiles — 4×2 ≥ 1024, 2×2 mobile, 120 px snap row in Import.
- **Preview:** every tile mounts a small JASSUB canvas running one sample line — "this is the part that hooks them" — with "hooks" active, looping 2.4 s. Identical text keeps presets comparable.
- **States:** default `--border`; hover border 30 % foreground + loop plays; selected 2 px `--ring` + check badge + name at 600.
- **Keyboard:** roving tabindex, arrows move, Home/End jump, Enter/Space select.
- **Tile:** name 14/600 + one-line intent ("Clean Pop — everyday talking-head"). Missing font → Inter + muted "Font missing".

## 8. States

| State | Where | Copy / behaviour |
|---|---|---|
| Empty | History | "No jobs yet. Import a video to start." + ghost button |
| Loading | History, results, picker | Skeletons matching final geometry; no spinners |
| Invalid URL | Import | "That doesn't look like a link. Use an http(s) URL or drop a file." |
| Unsupported file | Import | "OpenClip reads MP4, MOV, MKV, WebM and M4V. Convert this file first." |
| No speech | After transcribe | "No speech found in this video. OpenClip needs words to cut clips." + "Try another video" |
| No face | Notice, non-blocking | "No face detected. Clips were framed with a blurred background." + badge per card |
| Disk full | Any write | "Not enough space in the data folder. Free 2 GB and retry." + path in mono |
| FFmpeg missing | Import | "FFmpeg is not installed. Run `openclip doctor` in a terminal." + Copy command |
| Job failed | `/jobs/:id` | `JobError.code` + message, "Retry from the last finished step", "Open logs" |

## 9. Responsive and accessibility

- Breakpoints 640 / 1024 / 1280. Reflow, never shrink text.
- Focus: 2 px `--ring` at 2 px offset everywhere; "Skip to content" first.
- Contrast: body ≥ 4.5, large ≥ 3, boundaries ≥ 3 (§2). Score is never colour-only — number plus tier word ("Strong").
- Targets ≥ 44 px touch; icon buttons 36 px, 8 px gaps.
- Reduced motion: card lift, caption pops and step transitions go instant; progress still updates.
- Live regions: progress `polite`, one announcement per 5 s; errors `assertive`.
- Keyboard flow: source → options → primary → cards → drawer → Esc returns focus to the card it came from.

## 10. Key UI copy

| Slot | String |
|---|---|
| Hero | Paste a link, or drop a file. |
| Primary | Start clipping |
| Drop hint | Drop a video here · MP4, MOV, MKV, WebM, M4V |
| Options summary | 20–90 s · 8 clips · Auto · Clean Pop |
| Long video | Expect roughly 35–55 min on this CPU. |
| Leaving | You can close this tab — the job keeps running on this machine. |
| ETA | ≈ 12 min left · rough estimate |
| Cancel | Stop this job? Clips already rendered are kept. |
| Results | 8 clips · 41 min saved |
| Download all | Download all (.zip) |
| Preview label | Preview only — captions are burned in on re-render. |
| Re-render | Re-render clip · ≈ 15 s |
| Copied | Hashtags copied |
| Empty | No jobs yet. Import a video to start. |
| Retry | Retry from the last finished step |

## 11. Component map

| Area | shadcn/ui |
|---|---|
| Shared | `Button` `Card` `Badge` `Separator` `Tooltip` `Skeleton` `ScrollArea` `Sonner` |
| Top bar | `Sheet` (History, Settings) `Command` `Switch` `RadioGroup` (theme) |
| Import | `Input` `Label` `Collapsible` `Slider` `ToggleGroup` `Alert` `Popover` `Progress` |
| Processing | `Progress` `AlertDialog` `Badge` |
| Results | `AspectRatio` `Card` `Badge` `Select` `Sheet`/`Drawer` `Slider` `DropdownMenu` `Progress` |
| Picker | `RadioGroup` with styled `Card` children, or single-select `ToggleGroup` |

Nothing outside shadcn/ui is needed; JASSUB only draws subtitles.

**Wiring.** `POST /api/uploads` → `upload_id`, then `POST /api/jobs` with `JobCreate`. TanStack Query on `/api/jobs`; progress from `GET /api/jobs/{id}/events` (SSE). Re-render is one `PATCH /api/jobs/{id}/clips/{clip_id}` with `ClipUpdate`, returning the finished `Clip`. Preview: `.../preview` + `.../captions.ass`; zip: `.../download.zip`.

## 12. Caption presets

Shipped as `presets/captions/<id>.json`, one `CaptionStyle` each, all validating against `core/models.py`. Fonts are OFL, from `assets/fonts/`.

Legibility was measured: base text and highlighted word both sit at **≥ 4.5:1** against their outline or plate. Single-weight faces — Anton, Archivo Black, Bebas Neue, Bangers — carry `bold: false`, since bold there triggers synthetic bolding in libass.

| id | Font | Mood | Best for |
|---|---|---|---|
| `clean-pop` | Montserrat | Confident, neutral, tight | Talking heads, tutorials — default |
| `bold-impact` | Anton | Loud, one-word punches | Hot takes, sport, reactions |
| `karaoke-glow` | Poppins | Playful, fill follows speech | Music, rap, chant-alongs |
| `boxed-highlight` | Archivo Black | Editorial, magazine | Interview quotes, business |
| `neon-night` | Bebas Neue | Electric, late-night | Gaming, tech, nightlife |
| `comic-burst` | Bangers | Comic, jumpy | Memes, comedy, chaos |
| `minimal-clean` | Inter | Calm, premium | Documentary, finance, serious talks |
| `podcast-lower-third` | Oswald | Broadcast, reportage | Podcasts, news commentary |

## 13. Assumptions

1. A dropped file goes through `POST /api/uploads` over loopback, becoming `source: {type: "upload", value: upload_id}`; a link becomes `{type: "url", value: url}`.
2. Sleep suspends the worker; otherwise the notice reads "Sleep pauses the job".
3. The backend exposes overall `progress` + `message` only, hence one bar and five step states. ETA is client-side elapsed ÷ progress, hidden below 5 % rather than faked.
4. Re-render is explicit; trimming never triggers a background render.
5. "Follow face" = `fill`, "Fit with blurred background" = `fit`, `auto` shows as "Auto".
6. Score tiers 0–39 / 40–69 / 70–100; if the backend later defines bands, only §2 changes.
7. A missing preset font falls back to Inter with a muted note.
8. Dark is default; theme persists in `localStorage`. History comes from `GET /jobs`, so clearing the data dir empties it.
