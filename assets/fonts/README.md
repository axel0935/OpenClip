# Bundled caption fonts

Static TrueType files for the eight caption presets in `presets/captions/`.
Every family is licensed under the SIL Open Font License 1.1 (OFL), which is
permissive and allows redistribution and embedding. Each family's licence text
lives in the sibling directory `assets/licenses/` as `<Family>-OFL.txt`, **not**
next to the TTFs: libass opens every name it finds in `fontsdir`, so licence files
must stay out of it. `make build-wheel` ships both directories inside the wheel.

**Static files only, deliberately.** Variable fonts are not used: libass handles
synthetic bold and named instances of variable fonts inconsistently, so
`bold: true` presets would render with the wrong weight. Families that ship only
a variable font in `google/fonts` were taken from the upstream repository or a
pinned `google/fonts` commit where static TTFs still existed (see the URL column).

## Layout requirement

The directory is **flat** — no per-family sub-directories. This is a libass
constraint, not a preference: `subtitles=...:fontsdir=...` does not recurse, it
treats each sub-directory name as a font file, fails to load it, and silently
falls back to a system font. That fallback makes every glyph measurement wrong
while still "working", which is the worst kind of bug.

## Inventory

| Family | File | Weight | Used by | Source | Licence |
| --- | --- | --- | --- | --- | --- |
| Montserrat | `Montserrat-Regular.ttf` | 400 | `clean-pop` (not used; `bold` is true) | `google/fonts` @ `9548902`, `ofl/montserrat/` (last static release, v7.200) | OFL-1.1 |
| Montserrat | `Montserrat-Bold.ttf` | 700 | `clean-pop` | same as above | OFL-1.1 |
| Inter | `Inter-Regular.ttf` | 400 | `minimal-clean` | `rsms/inter` release v4.1, `extras/ttf/` (static) | OFL-1.1 |
| Inter | `Inter-Bold.ttf` | 700 | `minimal-clean` (unused; `bold` is false) | same as above | OFL-1.1 |
| Oswald | `Oswald-Regular.ttf` | 400 | `podcast-lower-third` (not used; `bold` is true) | `google/fonts` @ `1e5d467`, `ofl/oswald/` (last static release, v4.100) | OFL-1.1 |
| Oswald | `Oswald-Bold.ttf` | 700 | `podcast-lower-third` | same as above | OFL-1.1 |
| Poppins | `Poppins-Regular.ttf` | 400 | `karaoke-glow` (not used; `bold` is true) | `google/fonts` @ `main`, `ofl/poppins/` | OFL-1.1 |
| Poppins | `Poppins-Bold.ttf` | 700 | `karaoke-glow` | same as above | OFL-1.1 |
| Anton | `Anton-Regular.ttf` | 400 | `bold-impact` | `google/fonts` @ `main`, `ofl/anton/` | OFL-1.1 |
| Archivo Black | `ArchivoBlack-Regular.ttf` | 400 | `boxed-highlight` | `google/fonts` @ `main`, `ofl/archivoblack/` | OFL-1.1 |
| Bebas Neue | `BebasNeue-Regular.ttf` | 400 | `neon-night` | `google/fonts` @ `main`, `ofl/bebasneue/` | OFL-1.1 |
| Bangers | `Bangers-Regular.ttf` | 400 | `comic-burst` | `google/fonts` @ `main`, `ofl/bangers/` | OFL-1.1 |

Base URLs:

- `https://raw.githubusercontent.com/google/fonts/main/ofl/<family>/`
- `https://raw.githubusercontent.com/google/fonts/9548902f8a5a0b4a0bb7427d93347a9ba9fd0f2e/ofl/montserrat/`
- `https://raw.githubusercontent.com/google/fonts/1e5d467208dd1306173960dc7b176686d630f307/ofl/oswald/`
- `https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip`

Single-weight families (Anton, Archivo Black, Bebas Neue, Bangers) deliberately
carry `bold: false` in their presets: they have no bold cut, and asking libass for
one triggers synthetic bolding that smears the outline.

## Why `Fontsize` is larger than `font_size`

`CaptionStyle.font_size` is an **em size** in canvas pixels. libass instead fits
the font's `usWinAscent + usWinDescent` box to `Fontsize`, which is 1.21–1.76×
the em depending on the family. `openclip.captions.ttf` reads those header
values and the generator emits `font_size * ratio`, so text renders at the
documented size. Skipping that correction makes every caption 18–45 % too small.

## Refreshing

Replace files in place, keep the `Family-Weight.ttf` naming, keep the flat
layout, put its licence in `assets/licenses/`, and update this table. Do not add
a file whose licence you have not read.
