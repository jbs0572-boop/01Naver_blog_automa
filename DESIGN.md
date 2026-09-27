# Naver Blog Workflow QA Console Design System

## 0. Research Log

- Embedded refs: shortlisted `sentry.md`, `clickhouse.md`, `linear.app.md` → picked operational `taste-skill.md` + `sentry.md` because this is a local, data-dense QA console rather than a public product.
- Lazyweb: skipped — this read-only local console has no public product surface or image-led layout to research.
- Imagen drafts: skipped — the console has no hero or decorative image requirement; visual attention belongs on workflow state and gate evidence.
- PBS-01: Altair PBS Professional 19.2.3 User’s Guide §10.1.2–10.1.4, §10.1.9, §10.1.17 informed the summary-row → selected-detail hierarchy, explicit wait reasons, and observed-versus-final usage language. Its paper styling and product branding are not visual targets.

## 1. Atmosphere & Identity

A bright operational workbench for catching workflow mistakes before they reach Notion or Naver. Work, schedules, and settings are separate destinations. Queue state is always visible as text and controls; color supplements the state and never carries it alone.

## 2. Color

| Role | Token | Value | Usage |
|---|---|---:|---|
| Surface/primary | `--surface-primary` | `#F5F6F8` | App background |
| Surface/secondary | `--surface-secondary` | `#FFFFFF` | Header and panels |
| Surface/elevated | `--surface-elevated` | `#EEF4F0` | Selected row and detail panel |
| Text/primary | `--text-primary` | `#20252B` | Headings and key values |
| Text/secondary | `--text-secondary` | `#59636E` | Supporting copy |
| Text/tertiary | `--text-tertiary` | `#75808A` | Metadata and empty hints |
| Border/default | `--border-default` | `#D8DEE5` | Panel and row separators |
| Border/subtle | `--border-subtle` | `#E9EDF1` | Quiet dividers |
| Accent/primary | `--accent-primary` | `#167D46` | Safe progress and focus |
| Accent/secondary | `--accent-secondary` | `#12683A` | Links and selected controls |
| Status/success | `--status-success` | `#167D46` | Passed and saved |
| Status/warning | `--status-warning` | `#9A5B00` | Waiting and attention |
| Status/error | `--status-error` | `#B42318` | Failed and blocked |
| Status/info | `--status-info` | `#2563EB` | Informational state |

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

Component geometry is tokenized in the live stylesheet: `--radius-control: 8px`, `--radius-panel: 12px`, `--radius-pill: 999px`, and `--radius-round: 50%`; `--control-height-field: 40px`, `--control-height-compact: 36px`, and `--touch-target-min: 44px`; `--icon-mark: 32px`, `--icon-detail: 40px`, `--icon-dot: 8px`, and `--status-dot: 6px`; `--stage-gap: 3px`, `--stage-segment-min-inline-size: 10px`, `--stage-segment-height: 4px`; and `--separator-size: 1px`. These tokens define shared control, panel, status, stage, and separator geometry across responsive layouts.

The page uses a sticky top bar and a two-column run-list/detail workspace on desktop, then a single-column scroll body below 900px. The page body is the only vertical scroll owner. Content is capped at 1280px with intrinsic grids that collapse without horizontal overflow.

## 5. Components

### Progress and token usage
- Cards show a native labeled progress bar, percent, completed count out of seven, and Korean next-step label. Naver counts only after draft_saved. Percent means stage completion, not elapsed time.
- Token counters sum recorded Codex turn input and output. Cached input is a subset. Missing counters render 집계 전. Summary states coverage and recording delay.
- Reuse panel, spacing, text and status tokens. Mobile summaries stack; progress has no decorative animation.

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
- **Structure**: one Job filter toolbar, compact summary row, paginated table/list rows, selected Job detail, empty and error regions. The former duplicate run inventory is removed.
- **Variants**: desktop table, mobile run cards.
- **Spacing**: `--space-3` row padding, `--space-4` toolbar gap.
- **States**: loading, populated, selected, empty, stale connection, error. Rows show a safe reason beneath status and distinguish scheduled, submitted, and actual-start timing when recorded.
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
- **Data access**: the list reads summary data only. Selecting a Job with a run ID triggers exactly that detail request; polling does not eagerly fetch every detail.

### Schedule row
- **Structure**: time, per-row enabled control, model preset, next/last occurrence status, and remove action.
- **States**: enabled, disabled, future scheduled, submitted, running, missed, failed, confirmation waiting, and draft saved.
- **Language**: future schedule eligibility, queue submission, and actual execution are separate. Global `예약 중지`/`예약 재개` affects new submissions only and never implies an active Job is paused.
- **Preservation**: updating one row retains every other entry ID and preset. Visible-value equality skips history reconstruction so active inputs and focus remain stable.

### Manual run batch
- **Structure**: `주제 선택 방식` selector (`사용자 주제` / `자동 주제 선정`), conditional keyword field, workflow button, and one child card for every new request. Historical three-child batches retain their original cards.
- **States**: ready, queued, running, blocked, local-only, Q2 confirmation, draft saved, and failed. A child card advertises at most one next action and only that card is disabled while its request is active.
- **Accessibility**: labels remain visible, keyword is enabled and required only for `사용자 주제`, KST 기준일 is always required, the batch region uses `aria-live="polite"`, and child actions are real buttons with at least 44px mobile targets.
- **Contract**: the manual endpoint accepts exactly one topic-source payload plus `as_of_date`: `{ "keyword": "...", "as_of_date": "YYYY-MM-DD" }` or `{ "auto_topic": true, "as_of_date": "YYYY-MM-DD" }`. New requests create one child; the UI sends no count or selection context. 분야·독자·발행목적은 입력받지 않는다.
- **Safety**: `child_confirmation=independent`; the UI uses only child-scoped `retry`, `external`, or `confirm` requests with the currently advertised nonce. `bulk_confirmation=forbidden`; there is no bulk action or bulk confirmation. Before a selected child’s final Naver request, the UI shows target blog, title, image list, digest, and exact “draft save, do not publish” action in a fresh confirmation dialog. Missing preview data blocks the request; a stale response refreshes once without replaying the action.

## 6. Motion & Interaction

Micro interactions use 120ms ease-out; selection uses 180ms ease-in-out. Only `opacity` and `transform` animate. Buttons have hover, active, focus-visible, and disabled styles. `prefers-reduced-motion: reduce` removes non-essential transitions.

## 7. Depth & Surface

The strategy is mixed: tonal shifts establish the dark shell, while one-pixel purple borders separate evidence panels. No decorative gradients or large shadows are used. Selected content receives `--surface-elevated`, not a glow.

## 8. Accessibility Constraints & Accepted Debt

- WCAG 2.2 AA target, 4.5:1 minimum for body text, visible keyboard focus, all controls keyboard reachable, and text alternatives for every state.
- The console is intentionally loopback-only. Authentication and multi-user RBAC are out of scope for this QA surface; production automation remains governed by its existing hook, external-write rules, and adapter boundary.

## 9. Operational screen contract

The default route is `/#tasks`; `/#schedule` and `/#settings` retain the existing schedule and model controls. Tasks show running, queued, attention, today-complete, and next-schedule summaries above one server-backed, queue-aware Job table and selected detail. At widths below 768px rows become cards that prioritize Job ID/topic, status, stage, and observed tokens while selection and cancellation remain reachable. Loading, empty, stale connection, connection failure, and cancelling states have explicit text and tokenized styling.
