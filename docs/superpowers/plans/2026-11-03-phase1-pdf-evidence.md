# Phase 1 PDF Evidence Implementation Plan

> **For agentic workers:** Implement inline in this session. Each step is verified before the next.

**Goal:** Upload a real PDF, search its text, read the original pages and jump to highlighted evidence.

**Architecture:** FastAPI stores immutable PDFs and SQLite indexes. React renders the source PDF through PDF.js and overlays evidence coordinates. Parse snapshots preserve links across reparses.

**Tech Stack:** Python 3.11, FastAPI, PyMuPDF, SQLite, React, Vite, TypeScript, PDF.js, pytest and Playwright.

---

### Task 1: PDF index and API

**Files:** `backend/app.py`, `backend/tests/test_api.py`, `backend/requirements.txt`

- [x] Write an API test that uploads a valid PDF and checks the source bytes, indexed blocks, search result, evidence lookup and invalid input response.
- [x] Run pytest and observe the missing backend failure.
- [x] Implement immutable file storage, SQLite records, PyMuPDF extraction and the API.
- [x] Run pytest and fix failures.

### Task 2: PDF browser interface

**Files:** `frontend/src/App.tsx`, `frontend/src/App.css`, `frontend/package.json`, `frontend/index.html`

- [x] Build upload, search, section list, evidence results, page controls and PDF.js rendering.
- [x] Build the frontend with TypeScript checking.
- [x] Run a real PDF through the browser and inspect upload, search, jump and highlight.

### Task 3: Phase acceptance

- [x] Run backend tests and frontend build again.
- [x] Use a real downloaded academic PDF for browser acceptance and inspect the actual PDF position.
- [x] Record any unverified cases and stop before P2.
