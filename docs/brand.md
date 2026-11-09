# KRYTH brand and terminal design

KRYTH's terminal opening uses a six-row block wordmark: K, R, T, and H in primary white, with Y in KRYTH lime. The returning-session lockup uses a compact geometric K beside the wordmark. Unicode art has an ASCII fallback, and all color is optional so the interface remains legible in monochrome terminals. The SVG files in [`assets/brand/`](../assets/brand/) are the source artwork for web, documentation, and app use.

## Logo files

| File | Use |
| --- | --- |
| `kryth-symbol.svg` | Accent-colored symbol on a transparent background |
| `kryth-horizontal.svg` | Primary symbol, wordmark, and descriptor lockup |
| `kryth-monochrome.svg` | Single-color wordmark lockup |
| `kryth-icon.svg` | Compact app icon on obsidian |

## Design tokens

| Token | Color | Use |
| --- | --- | --- |
| Obsidian | `#101114` | Optional branded surfaces; terminal backgrounds remain user-controlled |
| Graphite | `#1D2025` | Subtle structure |
| Surface | `#252930` | Raised code and detail areas |
| Muted | `#858B91` | Metadata and secondary labels |
| Primary | `#ECEDEF` | Main content |
| Secondary | `#A1A5AD` | Supporting content |
| KRYTH lime | `#DFFF00` | Wordmark, active focus, and important highlights |

The live terminal theme is centralized in `ui/theme/palette.py`. Status colors are muted and reserved for success, warning, and error states. The acid accent is not used for ordinary text.

## Welcome and keyboard interaction

The opening view shows live model, workspace, session, version, and branch details when available, plus a previous task title for resumed sessions. It links to implemented commands: `/help`, `/model`, `/sessions`, and `/config`. Narrow or short terminals hide optional copy and metadata. `ui.animations = false` disables the startup reveal and animated indicators; `KRYTH_REDUCED_MOTION=1` and `KRYTH_NO_ANIMATION=1` provide environment overrides.

The composer keeps Enter to send, Ctrl+J for a newline, and `/` for command suggestions. Ctrl+P opens the same handler-backed command picker. The status bar shortens optional metadata as terminal width decreases.
