# Technology Guide — what was used and why

## Big picture
A **modular monolith**: one FastAPI app (modules below) serving both the JSON API (`/api/...`) and the web UI.
```
Browser UI (static/index.html)
   │ fetch + Bearer token
FastAPI (main.py) ── db.py (SQLite) ── data/files (uploads, exports)
   ├─ ingest.py  parse DOCX/PDF/TXT → facts
   ├─ match.py   job analysis + relevance score
   ├─ draft.py   build draft + validate claims (uses llm.py if key set)
   └─ render.py  one resume JSON → DOCX + PDF
```

## Technologies
| Tech | Used for | File |
|---|---|---|
| **Python 3** | Whole backend | `backend/app/*` |
| **FastAPI** | Web framework; routes, dependency injection (auth), auto docs at `/docs` | `main.py` |
| **Pydantic** | Request validation (`Cred`, `FactIn`, …) | `main.py` |
| **Uvicorn** | ASGI server that runs FastAPI | `run.sh` |
| **python-multipart** | File upload parsing | `main.py` |
| **SQLite (`sqlite3`)** | Relational storage: users, sessions, facts, jobs, versions, exports, audit | `db.py` |
| **hashlib PBKDF2 + secrets** | Salted password hashing; random session tokens stored only as SHA-256 hashes | `main.py` |
| **python-docx** | Read DOCX resumes; write DOCX exports | `ingest.py`, `render.py` |
| **pypdf** | Read PDF text layer | `ingest.py` |
| **ReportLab** | Write PDF exports directly (no LibreOffice needed) | `render.py` |
| **Regex + skill vocabulary** | Rule-based resume parsing, skill & requirement detection | `ingest.py`, `match.py` |
| **httpx** | Calls the Anthropic Messages API (optional) | `llm.py` |
| **Anthropic Claude (optional)** | Structured extraction and bullet rewording as strict JSON | `llm.py`, `draft.py` |
| **Vanilla JS + CSS** | UI (no build step): responsive grid, bottom tab bar on phones, dark mode, toasts, drag-and-drop | `static/index.html` |
| **docx-preview + JSZip (CDN)** | Renders the generated .docx in the browser so it looks like Word | `static/index.html` |
| **CSS media queries / viewport meta** | Mobile layout (≤860px) | `static/index.html` |
| **pytest + TestClient** | Automated tests | `tests/test_flow.py` |

## Key concepts (map to the blueprint)
- **Facts vs files** – every extracted item is a row in `facts` (kind, JSON data, confidence, source file, verified flag). Files are only sources. Fact IDs are the **evidence IDs**.
- **Canonical resume JSON** – `draft.build_draft` outputs one JSON; `render.py` turns it into DOCX/PDF. Templates change layout only.
- **Hybrid relevance score** – `0.35·skills + 0.25·responsibilities + 0.15·domain + 0.10·recency + 0.10·evidence + 0.05·priority` (`match.score_item`). The UI shows each component, so ranking is explainable. It is *not* a hiring probability.
- **Truthfulness validator** – `draft.validate` checks every bullet: has valid evidence IDs; each number and known tool in the text appears in the cited evidence; titles/orgs/dates match the verified profile; skills have evidence; required job skills missing from the profile are listed.
- **AI as helper, code as judge** – the LLM only proposes wording; unsupported bullets are dropped or flagged by code. Resume/JD text is wrapped in `<data>` tags and declared untrusted (prompt-injection guard).
- **Security basics** – every SQL query filters by `user_id` (cross-user access returns 404); upload allowlist, size cap, magic-byte check, safe filenames; hashed passwords/tokens; no resume text in logs; account/file deletion endpoints; audit table.
- **Graceful fallback** – no API key (or API failure) → deterministic parsing/drafting.

## API summary (all under `/api`)
`POST /auth/register|login` · `GET /me` · `GET /templates` · `POST /files` · `GET /files`, `/files/{id}/status` · `DELETE /files/{id}` · `GET /profile` · `POST|PATCH|DELETE /profile/facts` · `POST /job-descriptions` · `POST /resume-drafts` · `GET|PATCH /resume-versions/{id}` · `POST /resume-versions/{id}/exports` · `GET /exports/{id}` · `DELETE /account`

## Suggested learning order
1. `db.py` (tables) → 2. `main.py` (routes) → 3. `ingest.py` → 4. `match.py` → 5. `draft.py` → 6. `render.py` → 7. `static/index.html` → 8. `tests/test_flow.py`.
Official docs worth reading: FastAPI tutorial, python-docx, ReportLab user guide, Anthropic API docs (docs.claude.com).

## New in v2 (how each requirement is implemented)
| Requirement | Where / how |
|---|---|
| Mobile use | Responsive CSS; server binds `0.0.0.0` so a phone on the same Wi-Fi can open it |
| Word-style preview | `GET /api/resume-versions/{id}/docx` returns the real DOCX; browser renders it with docx-preview |
| Reference-resume templates | `render.extract_style` reads fonts/size/colour/margins/heading style from an uploaded DOCX; `to_docx` reuses that file as the base document; `templates` table |
| Flag absent skills/details | `draft.analyze` → missing required/preferred skills, unused-but-supported skills, domain terms, missing contact/dates/metrics; "I have this" → `POST /profile/skills` |
| Best resume with required words | `draft.build_draft` tries item×bullet variants, scores keyword coverage (required 1.0, preferred 0.5) + responsibility + domain, minus a length penalty; bullets with job keywords sorted first; skills ordered required-first |
| Professional UI | Gradient header, stepper, template cards, score ring, chips, collapsible fact cards |

## New in v3
| Requirement | Where / how |
|---|---|
| Reference as DOCX / PDF / image | `render.extract_style`: python-docx (DOCX), PyMuPDF or pypdf (PDF text sizes/fonts/colours/rules), Pillow pixel analysis (image, no OCR) |
| ATS-friendly | `render.py`: single column, font allow-list (`ATS_FONTS`), real bullets, date normaliser `nd()`, header/footer stripping, standard headings; `draft.analyze` → `ats` checklist |
| Summary as introduction | `draft._summary` (own summary + education + skills/roles + target job title); AI path asks for a 2–3 sentence intro with evidence IDs |
| Use all details | `draft._build` includes every fact; `draft.build_draft` trims only to fit `max_pages`, least relevant first; new `extra` fact kind; `used_fact_ids` + "left out" report |
| Libraries added | Pillow (image analysis), optional PyMuPDF (PDF style) |

## New in v4
| Change | Where / how |
|---|---|
| Robust section detection | `ingest.classify_heading` (exact + keyword rules + styling cues), `ingest.heuristic` state machine, `ingest.reclass` |
| Resume analysis + one-click fixes | `review.py`, `GET /api/profile/analysis`, `PATCH /api/profile/facts/{id}` with `kind` |
| Add something new | `custom` fact kind (`section` name), `POST /api/profile/text` (paste & parse), `POST /api/profile/skills` |
| Reference DOCX mimic | `render._learn` (learns prototypes) + `render._mimic` (clones paragraphs with python-docx/lxml `deepcopy`) |
| Keyword/term targeting | `match.extract_terms`, `draft._build` (Key Skills group, term evidence), `draft.analyze` (terms in score) |
| Neat output | `render.to_docx` (right tab stops), `render.to_pdf` (ReportLab Table for title/date rows) |

## New in v5 (links)
| Change | Where / how |
|---|---|
| Real hyperlinks | `render._linkify` (DOCX: `w:hyperlink` + external relationship) and `render._pdf_markup` (ReportLab `<a href>`) |
| Header-only contact links | `ingest._header_links`, `ingest._profile_links` |
| LinkedIn / GitHub / Portfolio fields + validation | `links.normalize_contact`, enforced by `PATCH/POST /api/profile/facts` for contacts |
| Link labels | `links.label`, `render._show`, `link_style` ("url" or "label") |
| Links card | `review.analyze_profile()["links"]`, `draft.links_report` |
| Link checker | `links.check_links` (httpx, SSRF guard, redirect re-validation), `POST /api/links/check` |
| Per-project links | review editor (`data-l` fields), `main.sanitize_links` |

## New in v6 (font size)
| Change | Where / how |
|---|---|
| Font size option | `font_size` on the resume ("auto", "ref" or 8–14), `render.resolve_cfg`, `render.norm_font_size` |
| Auto-fit | `render._pdf` returns the page count; `resolve_cfg` tries larger sizes/spacing and keeps the largest that fits the same pages (cached) |
| Uniform sizes with a reference DOCX | `render._apply_override` |
| UI | `FSOPT` dropdown (`#fsel` on Tailor, `#fs` in Review) in `index.html` |
