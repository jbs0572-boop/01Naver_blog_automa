# Topic feedback source and access policy

## Fixed decisions

- Scope: C, the staged full loop from manual Blog Statistics import through 7-day and 28-day feedback to Creator Advisor candidate shadow scoring.
- v1 KPI: `search_inflow`. No domain, audience, or publishing-purpose input is added.
- Automatic candidates: `creator_advisor_only`. Every other source may validate an existing Creator Advisor candidate or measure a published post; none may add, replace, or recover a candidate.
- The requested list has nine tools. `missing-tenth-tool` is an explicit disabled placeholder for an unknown product, not a guessed tenth tool.
- ChatGPT is represented only as the current Codex structuring step. It is not an external data source and must not trigger an additional API call.

## User decision record

1. Scope choice: C, the whole loop is staged, with the MVP beginning from manual Blog Statistics import, 7-day/28-day feedback, and Creator Advisor candidate shadow scoring.
2. KPI choice: search inflow is the shared v1 KPI. No conversion or purpose-specific branch is introduced without a confirmed source.
3. Missing tenth tool: no product was supplied. The registry keeps only the `missing-tenth-tool` disabled placeholder.

## Trust and access matrix

| Tool | Confidence | Access mode | Default | Allowed purpose | Restriction |
| --- | --- | --- | --- | --- | --- |
| NAVER DataLab | A | `official_api` | enabled | Same-candidate relative signal | Official API only; no candidate expansion or absolute-volume conversion. |
| NAVER Search Ads Keyword Tool | A | `official_api` | disabled | Same-candidate signal | Task 1 always denies activation; Task 7 may later require a separately trusted runtime capability, license, and policy record. |
| BlackKiwi | B | `manual_import` | disabled | Quarantined shadow signal | Manual redacted export only; no automatic collection. |
| Ecommerce AI Extension | C | `disabled` | disabled | None until exact product and terms verification | No installation, extension access, DOM extraction, or guessed URL. |
| DataLab Tools Helper | C | `disabled` | disabled | Writing aid only | Not an automatic candidate or data provider. |
| N Supporter | C | `disabled` | disabled | None until terms verification | Official portal is recorded in the registry; no extension access or manual import until review. |
| Daglo | B | `manual_import` | disabled | Rights-holder researcher input | No automatic upload; transcript is not a fact source. |
| ChatGPT / current Codex | C | `disabled` | disabled | Structuring only | Not an external data source; source material remains the evidence. |
| NAVER Blog Statistics | A | `manual_import` | enabled | Published-post measurement | Owner-provided CSV/JSON only; no login automation or third-party statistics. |
| Missing tenth tool | D | `disabled` | disabled | Placeholder only | It is not a product and cannot be activated. |

Confidence is closed: A is a first-party source, B is an identified supporting provider, C is a limited or unverified-derived aid, and D is an unavailable placeholder. Access mode is closed: `official_api`, `manual_import`, `authenticated_read_only`, or `disabled`.

## Terms and activation gate

`terms_checked_at=2026-09-08` and `effective_date=2025-07-10` for the governing NAVER terms at `https://policy.naver.com/rules/service.html`. The current official terms prohibit unapproved automated login, search, collection, and posting; this policy therefore permits only approved official APIs or explicit manual imports and treats uncertain UI and extension paths as disabled.

An enabled source must have an identified product, an exact official HTTPS URL, a reviewed `terms_checked_at`, a non-disabled compatible access mode, and be an external data source. The static Task 1 registry also pins each source ID's display name, URL, and reviewed date plus the current terms URL, check date, and effective date to its reviewed 2026-09-08 snapshot. Search Ads is always denied in Task 1: an `activation_proof` string in untrusted config is never authorization. Task 7 may accept a separately trusted runtime capability, license, and policy record. Otherwise activation fails with `source activation requires identified product and reviewed terms` before any adapter or network call.

No registry, snapshot, log, report, or CLI argument may contain a credential, cookie, token, browser profile path, session identifier, or personally identifying visitor data. The registry parser rejects secret-like keys and values.

## Existing workflow protections

This policy does not modify Q1, Q2, canonical content manifests, Notion writes, Naver input, or the explicit confirmation immediately before Naver draft save. Feedback collection and analysis remain read-only; publication is never automated.
