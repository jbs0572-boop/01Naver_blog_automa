# Local historical logs and portable legacy-contract tests

The 2026-08-26 beta run logs are **not committed**. The repository deliberately
ignores the entire `runs/` directory. The following two paths are authentic,
local-only operational records if they are available on a contributor's machine:

- `runs/beta-2026-08-26-energy-day.jsonl` — originally expected to contain 8 events.
- `runs/beta-gate0-2026-08-26-9topics.jsonl` — originally expected to contain 66 events.

Do not reconstruct, generate, or upload synthetic data under either original
filename. Do not remove the `runs/` ignore rule just to make CI green.

`tests/test_log_contract.py` now has two distinct verification surfaces:

1. **Authentic records:** If both local original files exist, the historical
   compatibility test calls the real `validate_log` reader and checks the
   original event counts exactly. If only one exists, it **fails** instead of
   claiming the two-run verification succeeded. If neither exists (as in a
   normal GitHub Actions checkout), it reports an explicit **skip**, never a
   misleading historical-compatibility pass.
2. **Portable format regression:** Independently, a mandatory offline test
   creates clearly labeled **synthetic test-only JSONL** in pytest's temporary
   directory and verifies 8 and 66 events across legacy stage-status aliases,
   lane events, approval, baseline, preparation, completion and null event
   types. It verifies full event round-trip and exercises exactly the same
   production `read_events` and `validate_log` functions. These test inputs
   are **not** real past operational logs.

Malformed JSON, invalid event kinds and optimized schema violations retain
their separate negative-case tests. No validator behavior, strict schema,
quality threshold, approval gate, or historical record is changed.

**Readiness:** A green CI establishes legacy-format regression compatibility,
not actual readability of unavailable local historical files. For that latter
claim, securely supply the authentic local originals and rerun pytest; retain
privacy and secrets protection.
