# Evidence-based quality rubric

Pass requires more than 90/100 **and** all mandatory gates: full tests pass, zero known critical security issues, no fabricated evidence and no required verification missing.

| Dimension | Weight | Scoring evidence |
| --- | ---: | --- |
| Functional compatibility | 25 | Existing test suite and new regressions pass in Actions |
| Correctness and boundary cases | 25 | Tests of valid, invalid, race and failure paths; untested-function inventory |
| Verification and coverage | 20 | Published line/branch percentage, function execution inventory and full CI log |
| Security and approval isolation | 15 | Read-only CI permissions; no credentials, external writes or automatic merges |
| Reproducibility | 10 | Python and dependency versions, fixed commands and retained artifacts |
| Operations usability | 5 | State, decisions, instructions and next actions recorded |
| **Total** | **100** | |

A score is **not** assigned until full CI evidence is reviewed. A function appearing in the coverage report with no executed body is *not tested*, even if it is defined or imported. A function with executed body lines may still need branch and behavioral tests; execution alone does not prove correctness. The inventory is a discovery tool, not a substitute for tests. Any failed mandatory condition prevents completion regardless of a numeric score.
