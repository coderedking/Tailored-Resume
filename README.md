# Tailored Resume Platform — Run Guide

Upload a resume → review extracted facts → paste a job description → see explainable ranking →
get an evidence-checked tailored draft → export DOCX / PDF. Built from the *Technical Blueprint*.

## 1. Requirements
- Python 3.10+ (check: `python --version`). Nothing else — no Node, no database server, no Docker.

## 2. Run it
**macOS / Linux:** `./run.sh`  **Windows:** double-click `run.bat`

Manual alternative:
```
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Open **http://127.0.0.1:8000** (API docs: http://127.0.0.1:8000/docs).

## 2b. Use it on your phone
1. Phone and PC must be on the **same Wi-Fi**. The app already listens on all interfaces (`--host 0.0.0.0`).
2. On the PC run `ipconfig` (Windows) / `ifconfig` (Mac/Linux) and note the IPv4 address, e.g. `192.168.1.23`.
3. On the phone open `http://192.168.1.23:8000`. Allow Python through **Windows Firewall** if asked (private networks only).
4. The layout switches to a mobile layout with a bottom tab bar. Tip: "Add to Home Screen" for an app-like icon.
5. Only use on trusted Wi-Fi: the local server uses plain HTTP. To use it from anywhere you must deploy it with HTTPS.

## 3. Use it
1. **Create account**, then on **Profile** upload a PDF/DOCX/TXT resume (headings like *Experience, Internships, Projects, Education, Skills* are recognised).
2. Review each fact card, fix mistakes, click **Save & verify**. Cards marked *needs review* or *conflicting dates* want attention. Tick *prioritise* to boost an item in ranking.
3. **Tailor** tab: paste a job description, pick a template (or **upload your own reference resume .docx**), click **Analyse** to see requirements and per-item relevance scores, then **Generate tailored draft**.
4. **Review**: you get a **Match score**, job keywords found, **missing required/preferred skills** (red/amber chips), keywords you have but didn't use, **missing details** (phone, LinkedIn, dates, metrics…), and a **claim check**. The **Word preview** shows the real .docx rendered like Word. Click **I have this** on a missing skill only if it is true — it is added to your profile and the resume is rebuilt. Edit bullets, **Save edits & refresh**, then **Download DOCX / PDF**.

## 3b. How the "best resume" is chosen
The app builds many variants (how many projects/roles × how many bullets), scores each for coverage of required (weight 1.0) and preferred (0.5) skills, responsibility wording and domain terms, penalises length, and keeps the best. Bullets containing the job's keywords are put first. It only uses words that your verified profile supports — it never adds skills you don't have.

## 3f. What changed in v4
- **Reads your resume properly.** Section headings are recognised even when worded differently or in capitals ("Academic Projects", "Educational Qualification", "Technical Skills & Tools"…), bold/Heading-styled headings in DOCX/PDF are detected, wrapped lines are joined, "Tech Stack:" lines become skills, and obvious mix-ups (a degree filed under Projects, a Coursera course filed as a project) are corrected automatically.
- **Resume analysis card** (Profile page): counts per section, plus a list of problems (missing dates, no bullets, entry in the wrong section…) with one-click fixes. Every detail card also has a **Type** dropdown to move it to another section.
- **➕ Add something new** (Profile page): education, project, job, internship, skills, certification, achievement, hackathon, volunteering, publication, research, open source, languages, links, your own named section, or **paste text** and let the app sort it.
- **Reference DOCX is reused, not just imitated.** The app learns your reference's section order, heading wording (e.g. "ACADEMIC PROJECTS"), entry layout, bullet style and right-aligned dates, and clones those paragraphs for the new resume. If the reference uses tables, they are dropped (ATS-safe) and only the look is copied.
- **Keyword targeting.** The job analysis now also extracts job *terms* (e.g. data pipelines, stakeholders, monitoring, ETL). A **Key Skills** group at the top of Technical Skills lists the job's skills and terms that your profile genuinely supports; each entry's technologies are ordered job-relevant first; the summary mentions matching domains. Abbreviations like ML, JS, k8s, sklearn are understood.
- **Neater layout.** Headings with rules, bold titles with right-aligned dates, tidy bullets and spacing, in both DOCX and PDF.

## 3h. What changed in v6 (font size)
- **Font size option** on the Tailor page and in the Review toolbar: **Auto-fit (fills the page)** (default), **As in template**, or a fixed size from 9 to 14 pt.
- **Auto-fit** makes the body text, name and headings as large as possible, then adds line/section spacing, while keeping the **same number of pages** (so short resumes no longer leave a big empty area and descriptions are easier to read). It is checked by laying the resume out and counting pages, so it never adds a page.
- With a **reference DOCX**, the new resume now uses one regular set of sizes (name, headings, body) instead of the reference's mixed sizes; choose "As in template" to keep the reference's own sizes exactly.
- Dates no longer wrap in the PDF at larger sizes.
- Fixed a bug where **Save edits** in the Review step could fail when a resume had fewer than 3 projects/jobs.

## 3g. What changed in v5 (links)
- **Clickable links** in DOCX and PDF: email, phone, LinkedIn, GitHub, portfolio and project links (the visible text stays readable for ATS).
- **Contact links come from the top of your resume only**, and are saved as separate LinkedIn / GitHub / Portfolio fields (plus "other links") with validation. Project links stay on the project.
- **Link text option:** show web addresses (safest for ATS) or labels like `LinkedIn | GitHub | GitHub: repo-name`. Choose it on the Tailor page or switch it in the Review toolbar.
- **Links card** (Profile and Review pages): what is present, what is missing (LinkedIn/GitHub/Portfolio), and projects without a link.
- **Link checker:** "Check my links" tests each address from the server (private addresses refused, max 15 per request, 10 requests per minute). Sites that block bots (e.g. LinkedIn) show as "can't verify", not broken. Downloads warn you about links that look broken.
- **Per-project links:** edit them in the Review step and open them with "View project".

## 3d. ATS-friendly output
Every resume uses one column, a standard font (Calibri/Arial/Times…), real bullets, standard headings (Professional Summary, Technical Skills, Work Experience, Projects, Education, Certifications), consistent dates (`Jun 2024 – Aug 2024`), plain-text contact line, no tables/text boxes/images, and nothing important in headers/footers. PDFs contain real selectable text. The Review page has an **ATS checklist** (email/phone present, date format, bullet length, estimated pages, keyword coverage, summary length).

## 3e. Uses all your details
All verified projects, internships, jobs, education, certifications, achievements, extra sections (languages, interests, coursework…), links and skills are included, ordered by relevance to the job. Only if the result exceeds the page limit (2 pages) are the least relevant bullets, then entries, trimmed — and the app tells you exactly what was left out. The **Professional Summary** is written as an introduction (your own summary text if you have one, your education, hands-on experience with your strongest matching skills, and the target role from the job posting).

## 3c. Reference-resume templates
Upload a reference resume as **DOCX, PDF or an image** (PNG/JPG/WEBP — a photo or screenshot works). The app copies its fonts, sizes, colours, margins, name alignment and heading style (caps / rule line) and writes your content in that style, always keeping the ATS-safe structure (no columns, text boxes, tables or images).
- DOCX: exact values are read from the file.
- PDF: read from the text layer (best results if `PyMuPDF` installed; `run.bat` tries to install it automatically).
- Image: no OCR is used; the style is *estimated* from the picture (name alignment/size, body size, margin, accent colour, heading rule). Expect approximate results; a DOCX gives the most faithful copy.

## 4. Optional: turn on the AI
Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY=...`. Restart. The header will show **AI on**.
The AI improves resume parsing and bullet wording; every bullet must cite evidence IDs and is still validated by code.
Without a key, everything works in rule-based mode (bullets are your own wording, reordered by relevance).
Note: with AI on, resume text and profile facts are sent to the provider — review their data terms.

## 5. Tests
```
cd backend && python -m pytest -q tests
```
Covers upload → parse → rank → draft → unsupported-claim detection → DOCX/PDF export → cross-user isolation → upload hardening.

## 6. Where data lives
`backend/data/app.db` (SQLite) and `backend/data/files/` (uploads/exports). Delete the folder to reset. Not encrypted — use an encrypted disk for real personal data.

## 7. Troubleshooting
- *Port in use:* change `--port 8000`.
- *“No text layer found”:* scanned PDF; OCR isn't included — upload DOCX/TXT or add facts manually.
- *Few facts extracted:* make sure section headings are on their own line; or add facts manually / enable AI.

## 8. Differences from the blueprint (honest scope notes)
| Blueprint | This build | Upgrade path |
|---|---|---|
| React/Next.js | Single-file vanilla JS UI served by FastAPI (no build step) | Port `static/index.html` views to React; API is unchanged |
| PostgreSQL | SQLite via `db.py` | Replace `db.py` with SQLAlchemy/psycopg; schema is relational |
| Redis worker queue | Parsing/export run inside the request | Move `ingest.parse` / `render.*` into RQ/Celery tasks |
| OCR, malware scan, MFA, signed URLs, rate limits | Not included (type/size/signature checks, hashed sessions, per-user scoping are) | Add per blueprint §6, §12 |
| pgvector / embeddings | Not used (blueprint says optional) | Add into `match.py` |
| 2 templates | 2 templates (classic, compact) | Add entries to `render.TEMPLATES` |

## 9. Notes on the new features
- **Word preview** uses the open-source `docx-preview` library loaded from a CDN, so it needs internet. Offline, a simplified preview is shown instead; downloaded DOCX/PDF files are unaffected. I tested the backend (including DOCX generation) automatically, but not the browser interface visually.
- The match score is a transparency aid based on keyword coverage; it is not an official ATS score or a prediction of being hired.
