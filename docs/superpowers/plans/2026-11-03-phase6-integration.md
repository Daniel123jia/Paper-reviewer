# P6 Integration and Predeployment Implementation Plan

**Goal:** Verify the independent workbench end to end, secure its single account mode, and make local failures and usage observable before any deployment.

**Architecture:** Keep the current React/Vite/FastAPI/SQLite application. Add optional cookie authentication for local testing, require it in explicit predeployment mode, recover interrupted background jobs on startup, harden duplicate uploads, and expose aggregate model usage. Run real PDF and browser checks against local servers only.

**Tech Stack:** FastAPI, SQLite, PyMuPDF, React, Playwright, pytest.

---

## Task 1: Access isolation

- [x] Add failing API tests: unauthenticated document list, PDF and export return 401; login sets HttpOnly cookie; valid cookie grants access; logout revokes browser cookie; predeployment mode rejects missing or short secret.
- [x] Implement middleware and signed 12 hour session cookie using `PAPER_REVIEW_ACCESS_TOKEN`; add status/login/logout routes. Keep existing local mode when the variable is unset.
- [x] Add a login gate to the React entry point and a logout action. Verify PDF.js works with the same origin cookie; protected export is checked by API test.
- [x] Run API and real browser tests on a separate authenticated local server.

## Task 2: Recovery and concurrency

- [x] Add failing test for queued/running jobs after restart; parallel duplicate upload test passed with the current storage path.
- [x] Mark interrupted runs and finding verifications failed at startup. Keep SQLite and file writes unchanged because concurrent tests passed; use a single process for this local service.
- [x] Rerun the tests and check the parsed snapshot ID and stored PDF remain stable.

## Task 3: Usage and performance

- [x] Add a usage summary API test covering chat, review, and draft calls, including unknown costs and failed calls.
- [x] Implement the read only aggregate API and a small UI view for model, tokens, duration, and known versus unknown costs.
- [x] Run a local real PDF smoke script measuring cold and repeated upload, search, PDF fetch, report export, and concurrent reads; record observed values without claiming universal thresholds.

## Task 4: Final local acceptance

- [x] Run full backend tests, frontend build, offline browser suite, authenticated browser flow, and true PDF export flow.
- [x] Inspect PDF pages and report content from P5, plus P6 login and usage screenshots; record limits in `docs/phase6-acceptance.md` and update README.
- [x] Confirm no server deployment or remote integration was performed.
