# Naver Blog Workflow QA Console Design System

## 0. Research Log

- Embedded refs: shortlisted `sentry.md`, `clickhouse.md`, `linear.app.md` → picked operational `taste-skill.md` + `sentry.md` because this is a local, data-dense QA console rather than a public product.
- Lazyweb: skipped — this read-only local console has no public product surface or image-led layout to research.
- Imagen drafts: skipped — the console has no hero or decorative image requirement; visual attention belongs on workflow state and gate evidence.

## 1. Atmosphere & Identity

A quiet control room for catching workflow mistakes before they reach Notion or Naver. The signature is a warm purple-black canvas with a single lime signal for safe progress; failed and waiting states stay visible as evidence, not decoration.

## 2. Color

| Role | Token | Value | Usage |
|---|---|---:|---|
| Surface/primary | `--surface-primary` | `#15121d` | App background |
| Surface/secondary | `--surface-secondary` | `#1f1a2a` | Header and panels |
| Surface/elevated | `--surface-elevated` | `#2a2338` | Selected row and detail panel |
| Text/primary | `--text-primary` | `#f8f5ff` | Headings and key values |
| Text/secondary | `--text-secondary` | `#c9c0d8` | Supporting copy |
| Text/tertiary | `--text-tertiary` | `#968ba8` | Metadata and empty hints |
| Border/default | `--border-default` | `#413653` | Panel and row separators |
| Border/subtle | `--border-subtle` | `#302740` | Quiet dividers |
| Accent/primary | `--accent-primary` | `#c2ef4e` | Safe progress and focus |
| Accent/secondary | `--accent-secondary` | `#a995e8` | Links and selected controls |
| Status/success | `--status-success` | `#c2ef4e` | Passed and saved |
| Status/warning | `--status-warning` | `#ffb287` | Waiting and attention |
| Status/error | `--status-error` | `#fa7faa` | Failed and blocked |
| Status/info | `--status-info` | `#8fc7ff` | Informational state |

Accent is reserved for interaction and workflow meaning. Status colors are never used as decoration.

## 3. Typography

| Level | Size | Weight | Usage |
|---|---:|---:|---|
| Display | `clamp(2rem, 4vw, 3rem)` | 700 | Page title |
| H2 | `1.75rem` | 600 | Section titles |
| H3 | `1.375rem` | 600 | Detail headings |
| Body | `1rem` | 400 | Explanatory copy |
| Body/sm | `0.875rem` | 400 | Table and panel text |
| Caption | `0.75rem` | 500 | Metadata and labels |
| Overline | `0.6875rem` | 600 | Navigation and status labels |

Primary font is `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. Technical identifiers use `ui-monospace, SFMono-Regular, Menlo, monospace`.

## 4. Spacing & Layout

The base unit is 4px. Tokens are `--space-1: 4px`, `--space-2: 8px`, `--space-3: 12px`, `--space-4: 16px`, `--space-5: 20px`, `--space-6: 24px`, `--space-8: 32px`, and `--space-12: 48px`.

The page uses a sticky top bar and a two-column run-list/detail workspace on desktop, then a single-column scroll body below 900px. The page body is the only vertical scroll owner. Content is capped at 1280px with intrinsic grids that collapse without horizontal overflow.

## 5. Components

### Summary card
- **Structure**: label, value, supporting note.
- **Variants**: total, success, attention, blocked.
- **Spacing**: `--space-4` inner padding, `--space-6` grid gap.
- **States**: default, loading skeleton, empty zero, error unavailable.
- **Accessibility**: semantic `section`, visible text labels, no color-only meaning.
- **Motion**: opacity/transform entry only; reduced motion disables it.
- **Layout**: intrinsic grid; no scroll owner.

### Status chip
- **Structure**: text label and optional status dot.
- **Variants**: passed, waiting, failed, blocked, info.
- **Spacing**: `--space-1` vertical and `--space-2` horizontal.
- **States**: default, selected, unknown.
- **Accessibility**: status text is always present; color supplements text.
- **Motion**: none.
- **Layout**: cluster item.

### Run table
- **Structure**: filter toolbar, table/list rows, empty and error regions.
- **Variants**: desktop table, mobile run cards.
- **Spacing**: `--space-3` row padding, `--space-4` toolbar gap.
- **States**: loading, populated, empty, error.
- **Accessibility**: real buttons for selectable rows, table headers on desktop, focus-visible outline.
- **Motion**: selected row uses a 150ms tonal transition.
- **Layout**: scroll-body-shell; page body owns vertical scroll.

### Run detail
- **Structure**: identity header, gate summary, stage timeline, safe evidence.
- **Variants**: selected, no selection, malformed log.
- **Spacing**: `--space-4` sections, `--space-6` panel padding.
- **States**: loading, populated, empty, error.
- **Accessibility**: heading hierarchy, labeled metadata, long IDs wrap safely.
- **Motion**: opacity entry only; reduced motion disables it.
- **Layout**: sidebar panel on wide screens, stacked section on narrow screens.

### Manual run form
- **Structure**: `주제 선택 방식` selector (`사용자 주제` / `자동 주제 선정`), conditional keyword field, one neutral primary workflow button, live task status.
- **States**: ready, queued, running, external storage pending, Q2 confirmation, draft saved, failed.
- **Accessibility**: labels remain visible, keyword is enabled and required only for `사용자 주제`, status uses `role=status`, and external/confirmation actions are real buttons.
- **Contract**: the manual endpoint accepts exactly one topic-source payload: `{ "keyword": "..." }` for a user-defined topic or `{ "auto_topic": true }` for automatic topic selection. The selected source changes only the topic-selector input; Q1, Q2, external storage, and Naver confirmation remain the same workflow.
- **Safety**: the UI starts the existing `daily-generate` runner; external writes require their adapters and remain behind the existing Q1/Q2 gates. Before the final Naver request, the UI must show the target blog, title, image list, and exact “draft save, do not publish” action in a fresh confirmation dialog. Missing preview data blocks the request.

## 6. Motion & Interaction

Micro interactions use 120ms ease-out; selection uses 180ms ease-in-out. Only `opacity` and `transform` animate. Buttons have hover, active, focus-visible, and disabled styles. `prefers-reduced-motion: reduce` removes non-essential transitions.

## 7. Depth & Surface

The strategy is mixed: tonal shifts establish the dark shell, while one-pixel purple borders separate evidence panels. No decorative gradients or large shadows are used. Selected content receives `--surface-elevated`, not a glow.

## 8. Accessibility Constraints & Accepted Debt

- WCAG 2.2 AA target, 4.5:1 minimum for body text, visible keyboard focus, all controls keyboard reachable, and text alternatives for every state.
- The console is intentionally localhost-only. Authentication and multi-user RBAC are out of scope for this QA surface; production automation remains governed by its existing hook, external-write rules, and adapter boundary.
