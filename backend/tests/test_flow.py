import io, os, tempfile
os.environ["DATA_DIR"] = tempfile.mkdtemp()
from docx import Document
from docx.shared import Pt
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
RESUME = ["Asha Rao", "asha@example.com | +91 98765 43210", "Summary", "CS student interested in ML.", "Skills",
          "Python, SQL, scikit-learn, Pandas, Docker", "Internships", "Data Science Intern | Acme Corp | Jun 2024 - Aug 2024",
          "• Built a churn model using Python and scikit-learn reaching 87% accuracy",
          "• Cleaned 50,000 records with Pandas", "Projects", "Portfolio Website | Jan 2023 - Mar 2023",
          "• Built a static site with HTML and CSS", "Education", "B.Tech Computer Science | State University | 2021 - 2025"]
JD = """Machine Learning Intern
Requirements
- Python and scikit-learn
- Experience building and evaluating machine learning models
Nice to have
- Docker, Kubernetes
"""


def auth(email):
    t = c.post("/api/auth/register", json={"email": email, "password": "password123"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def test_end_to_end():
    h = auth("a@x.com")
    d = Document()
    for l in RESUME:
        d.add_paragraph(l)
    buf = io.BytesIO(); d.save(buf)
    r = c.post("/api/files", files={"file": ("r.docx", buf.getvalue())}, headers=h).json()
    assert r["status"] == "needs_review" and r["facts_added"] >= 4, r
    facts = c.get("/api/profile", headers=h).json()
    assert {"contact", "skills", "internship", "project", "education"} <= {f["kind"] for f in facts}
    jd = c.post("/api/job-descriptions", json={"text": JD}, headers=h).json()
    assert jd["ranking"][0]["title"].startswith("Data Science Intern")
    assert "Docker" in [s["name"] for s in jd["requirements"]["preferred_skills"]]
    dr = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": "compact"}, headers=h).json()
    assert dr["resume"]["experience"][0]["bullets"]
    rep = dr["report"]
    assert "Kubernetes" in rep["missing_preferred"] and rep["score"] > 0 and rep["covered"]
    assert rep["links_report"]["missing"] == ["LinkedIn", "GitHub"]
    assert c.post("/api/profile/skills", json={"skills": ["Kubernetes"]}, headers=h).status_code == 200
    # custom reference template
    ref = Document(); p0 = ref.add_paragraph(); p0.alignment = 0; rr = p0.add_run("YOUR NAME"); rr.font.size = Pt(24)
    ref.add_paragraph("EXPERIENCE").runs[0].bold = True
    rb = io.BytesIO(); ref.save(rb)
    tp = c.post("/api/templates", files={"file": ("mine.docx", rb.getvalue())}, headers=h).json()
    assert tp["style"]["name_size"] == 24 and tp["style"]["head_caps"] is True
    assert any(x["custom"] for x in c.get("/api/templates", headers=h).json())
    d2 = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": tp["id"], "font_size": "ref"}, headers=h).json()
    assert "Kubernetes" in d2["report"]["covered"]
    wd = c.get(f"/api/resume-versions/{d2['id']}/docx", headers=h)
    assert wd.status_code == 200 and wd.content.startswith(b"PK")
    assert Document(io.BytesIO(wd.content)).paragraphs[0].runs[0].font.size == Pt(24)
    # unsupported claim is flagged
    res = dr["resume"]; res["experience"][0]["bullets"][0] = "Improved accuracy by 99% using TensorFlow"
    w = c.patch(f"/api/resume-versions/{dr['id']}", json={"resume_json": res}, headers=h).json()["warnings"]
    assert any("99%" in x["message"] for x in w) and any("TensorFlow" in x["message"] for x in w)
    for fmt, magic in (("docx", b"PK"), ("pdf", b"%PDF")):
        e = c.post(f"/api/resume-versions/{dr['id']}/exports", json={"format": fmt}, headers=h).json()
        out = c.get(f"/api/exports/{e['id']}", headers=h)
        assert out.status_code == 200 and out.content.startswith(magic)
    # tenant isolation
    h2 = auth("b@x.com")
    assert c.get(f"/api/resume-versions/{dr['id']}", headers=h2).status_code == 404
    assert c.get(f"/api/exports/{e['id']}", headers=h2).status_code == 404
    assert c.get("/api/profile").status_code == 401
    # upload hardening
    assert c.post("/api/files", files={"file": ("x.exe", b"MZ")}, headers=h).status_code == 400
    assert c.post("/api/files", files={"file": ("x.pdf", b"not a pdf")}, headers=h).status_code == 400


def test_reference_pdf_image_summary_ats():
    import re
    from PIL import Image, ImageDraw
    from reportlab.pdfgen import canvas
    h = auth("c@x.com")
    d = Document()
    for l in RESUME:
        d.add_paragraph(l)
    buf = io.BytesIO(); d.save(buf)
    c.post("/api/files", files={"file": ("r.docx", buf.getvalue())}, headers=h)
    # PDF reference: centred blue 22pt name, serif body, ruled headings
    pb = io.BytesIO(); cv = canvas.Canvas(pb, pagesize=(612, 792))
    cv.setFont("Times-Bold", 22); cv.setFillColorRGB(0.1, 0.2, 0.8); cv.drawCentredString(306, 740, "JANE DOE")
    cv.setFillColorRGB(0, 0, 0); cv.setFont("Times-Roman", 10); cv.drawCentredString(306, 725, "jane@x.com | 999")
    cv.setFont("Times-Bold", 12); cv.drawString(54, 690, "EXPERIENCE"); cv.line(54, 686, 558, 686)
    cv.setFont("Times-Roman", 10); cv.drawString(54, 670, "Engineer at Foo - did things"); cv.save()
    t1 = c.post("/api/templates", files={"file": ("ref.pdf", pb.getvalue())}, headers=h).json()
    assert t1["style"]["source"] == "pdf" and t1["style"]["name_align"] == 1 and 20 <= t1["style"]["name_size"] <= 24
    # image reference: simulated page with centred name band and a rule
    im = Image.new("RGB", (850, 1100), "white"); dr = ImageDraw.Draw(im)
    dr.rectangle([300, 50, 550, 76], fill=(20, 40, 160)); dr.rectangle([60, 130, 300, 142], fill="black")
    dr.rectangle([60, 150, 790, 152], fill="black")
    for y in range(180, 400, 24):
        dr.rectangle([60, y, 700, y + 11], fill="black")
    ib = io.BytesIO(); im.save(ib, "PNG")
    t2 = c.post("/api/templates", files={"file": ("ref.png", ib.getvalue())}, headers=h).json()
    assert t2["style"]["source"] == "image" and t2["style"]["name_align"] == 1 and t2["style"]["head_border"] is True
    assert c.post("/api/templates", files={"file": ("x.png", b"nope")}, headers=h).status_code == 400
    jd = c.post("/api/job-descriptions", json={"text": JD}, headers=h).json()
    for t in (t1["id"], t2["id"]):
        dr_ = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": t}, headers=h).json()
        r = dr_["resume"]
        # every profile entry is used, summary is an introduction, ATS checks are present
        assert r["projects"] and r["experience"] and r["education"]
        assert len(re.findall(r"[.!?]", r["summary"])) >= 2, r["summary"]
        assert all(a["ok"] for a in dr_["report"]["ats"] if "Estimated" in a["message"] or "headings" in a["message"])
        assert dr_["report"]["unused_facts"] == []
        pdf = c.post(f"/api/resume-versions/{dr_['id']}/exports", json={"format": "pdf"}, headers=h).json()
        assert c.get(f"/api/exports/{pdf['id']}", headers=h).content.startswith(b"%PDF")


MESSY = """JOHN DOE
john@x.com | 9999999999 | github.com/john
CAREER OBJECTIVE
Aspiring ML engineer.
EDUCATIONAL QUALIFICATION
B.Tech in Computer Science, ABC Institute of Technology | 2021 - 2025
CGPA: 8.4
Class XII - XYZ Public School | 2021
TECHNICAL SKILLS
Languages: Python, Java
ACADEMIC PROJECTS
Spam Classifier
Tech Stack: Python, scikit-learn
Built a classifier on 5,000 emails reaching 94% accuracy
using TF-IDF features.
Library Management System | Jan 2023 - Mar 2023
• Built with Java and MySQL
CERTIFICATIONS
Machine Learning - Coursera | 2023
"""
JD2 = """Data Engineer
Responsibilities
- Build data pipelines and ETL jobs with Python and Spark
- Improve data quality and monitoring for stakeholders
Requirements
- Python, SQL, Java
Nice to have
- Kafka
"""


def test_messy_resume_sections_analysis_custom_and_keywords():
    h = auth("d@x.com")
    r = c.post("/api/files", files={"file": ("m.txt", MESSY.encode())}, headers=h).json()
    assert r["status"] == "needs_review", r
    facts = c.get("/api/profile", headers=h).json()
    by = lambda k: [f["data"]["title"] for f in facts if f["kind"] == k]
    assert sorted(by("education")) == ["B.Tech in Computer Science", "Class XII"], by("education")
    assert sorted(by("project")) == ["Library Management System", "Spam Classifier"], by("project")
    spam = next(f for f in facts if f["data"].get("title") == "Spam Classifier")["data"]
    assert "scikit-learn" in spam["skills"] and "TensorFlow" not in spam["skills"] and len(spam["bullets"]) == 1
    # analysis flags a wrongly filed item and offers a fix
    edu = next(f for f in facts if f["data"].get("title") == "Class XII")
    c.patch(f"/api/profile/facts/{edu['id']}", json={"kind": "project"}, headers=h)
    an = c.get("/api/profile/analysis", headers=h).json()
    fix = [i for i in an["issues"] if i["fix"] and i["fix"]["kind"] == "education"]
    assert fix and an["counts"]["project"] == 3
    c.patch(f"/api/profile/facts/{fix[0]['fact_id']}", json={"kind": "education"}, headers=h)
    # 'add something new': custom section + pasted text
    assert c.post("/api/profile/facts", json={"kind": "custom", "data": {"section": "Hackathons", "title": "Smart India Hackathon", "bullets": ["Finalist among 200 teams"]}}, headers=h).status_code == 200
    assert c.post("/api/profile/text", json={"text": "Projects\nChat App | 2024\n• Built a chat app with Node.js and MongoDB"}, headers=h).json()["facts_added"] >= 1
    jd = c.post("/api/job-descriptions", json={"text": JD2}, headers=h).json()
    assert "data quality" in jd["requirements"]["keywords"]
    dr = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": "classic"}, headers=h).json()
    res = dr["resume"]
    assert {p["title"] for p in res["projects"]} >= {"Spam Classifier", "Library Management System", "Chat App"}
    assert {e["title"] for e in res["education"]} == {"B.Tech in Computer Science", "Class XII"}
    assert not any(p["title"] in ("Class XII", "B.Tech in Computer Science") for p in res["projects"])
    assert res["custom"][0]["section"] == "Hackathons"
    assert res["skills"][0]["group"] == "Key Skills" and res["skills"][0]["name"] == "Python"
    assert dr["report"]["terms_missing"] is not None and "ats" in dr["report"]


def test_reference_docx_is_mimicked():
    from docx.shared import Pt
    h = auth("e@x.com")
    c.post("/api/files", files={"file": ("m.txt", MESSY.encode())}, headers=h)
    ref = Document()
    p = ref.add_paragraph(); p.alignment = 1; r_ = p.add_run("Ref Name"); r_.bold = True; r_.font.size = Pt(22)
    ref.add_paragraph("ref@x.com | 111").alignment = 1
    def head(t):
        q = ref.add_paragraph(); x = q.add_run(t); x.bold = True; x.font.size = Pt(13)
    head("ACADEMIC PROJECTS")
    q = ref.add_paragraph(); q.paragraph_format.tab_stops.add_tab_stop(Pt(480), 2); q.add_run("Title One").bold = True; q.add_run("\tJan 2022")
    ref.add_paragraph("Did a thing", style="List Bullet")
    head("EDUCATION")
    q = ref.add_paragraph(); q.add_run("Degree").bold = True; q.add_run("\t2020")
    head("TECHNICAL SKILLS")
    ref.add_paragraph("Python, Java")
    rb = io.BytesIO(); ref.save(rb)
    tp = c.post("/api/templates", files={"file": ("ref.docx", rb.getvalue())}, headers=h).json()
    assert tp["style"].get("mimic"), tp["style"]
    jd = c.post("/api/job-descriptions", json={"text": JD2}, headers=h).json()
    dr = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": tp["id"], "font_size": "ref"}, headers=h).json()
    out = Document(io.BytesIO(c.get(f"/api/resume-versions/{dr['id']}/docx", headers=h).content))
    texts = [p.text for p in out.paragraphs if p.text.strip()]
    assert texts[0] == "JOHN DOE" and out.paragraphs[0].runs[0].font.size == Pt(22)
    ip, ie, isk = texts.index("ACADEMIC PROJECTS"), texts.index("EDUCATION"), texts.index("TECHNICAL SKILLS")
    assert ip < ie < isk, texts  # reference's section order and wording are reused
    assert any(p.style.name == "List Bullet" for p in out.paragraphs)
    assert any("\t" in p.text for p in out.paragraphs)  # right-aligned dates via tab, like the reference


def test_hyperlinks_in_docx_and_pdf():
    import zipfile, re as _re
    h = auth("f@x.com")
    txt = MESSY.replace("john@x.com | 9999999999 | github.com/john", "john@x.com | 9999999999 | https://www.linkedin.com/in/john-doe | github.com/john | johndoe.dev")
    txt = txt.replace("Spam Classifier\n", "Spam Classifier\ngithub.com/john/spam-classifier\n")
    c.post("/api/files", files={"file": ("m.txt", txt.encode())}, headers=h)
    # make the portfolio link explicit via the profile (bare domains are only linked when stored as contact links)
    contact = next(f for f in c.get("/api/profile", headers=h).json() if f["kind"] == "contact")
    c.patch(f"/api/profile/facts/{contact['id']}", json={"data": {**contact["data"], "links": contact["data"]["links"] + ["johndoe.dev"]}}, headers=h)
    jd = c.post("/api/job-descriptions", json={"text": JD2}, headers=h).json()
    ref = Document()
    ref.add_paragraph("Ref Name").alignment = 1
    ref.add_paragraph("a@b.com").alignment = 1
    for t in ("SKILLS", "PROJECTS", "EDUCATION"):
        ref.add_paragraph(t).runs[0].bold = True
        ref.add_paragraph("Python" if t == "SKILLS" else "Item")
    rb = io.BytesIO(); ref.save(rb)
    tp = c.post("/api/templates", files={"file": ("ref.docx", rb.getvalue())}, headers=h).json()
    for tpl in ("classic", tp["id"]):
        d = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": tpl}, headers=h).json()
        raw = c.get(f"/api/resume-versions/{d['id']}/docx", headers=h).content
        z = zipfile.ZipFile(io.BytesIO(raw))
        xml, rels = z.read("word/document.xml").decode(), z.read("word/_rels/document.xml.rels").decode()
        assert xml.count("<w:hyperlink") >= 4, xml.count("<w:hyperlink")
        for target in ("mailto:john@x.com", "https://linkedin.com/in/john-doe", "https://github.com/john", "https://johndoe.dev", "https://github.com/john/spam-classifier"):
            assert target in rels, (tpl, target)
        assert 'TargetMode="External"' in rels
        Document(io.BytesIO(raw))  # still a valid Word file
    e = c.post(f"/api/resume-versions/{d['id']}/exports", json={"format": "pdf"}, headers=h).json()
    pdf = c.get(f"/api/exports/{e['id']}", headers=h).content
    assert pdf.count(b"/URI") >= 4 and b"linkedin.com/in/john-doe" in pdf


LINKRES = """JANE ROE
jane@x.com | +91 98765 43210 | https://www.linkedin.com/in/jane-roe | github.com/janeroe | janeroe.dev
Projects
Chat App | 2024
https://github.com/janeroe/chat-app
• Built a chat app with Node.js. Deployed using GitHub Actions and Docker
Education
B.Tech in Computer Science, ABC Institute | 2021 - 2025
"""


def test_links_header_only_labelled_fields_validation_labels_and_check():
    import zipfile, threading, http.server
    from app import links as lk
    h = auth("g@x.com")
    c.post("/api/files", files={"file": ("l.txt", LINKRES.encode())}, headers=h)
    facts = c.get("/api/profile", headers=h).json()
    ct = next(f for f in facts if f["kind"] == "contact")["data"]
    # 1+2: contact links come from the header only and are split into labelled fields; project link stays on the project
    assert ct["linkedin"] == "https://linkedin.com/in/jane-roe" and ct["github"] == "https://github.com/janeroe"
    assert ct["portfolio"] == "https://janeroe.dev" and ct["links"] == [], ct
    proj = next(f for f in facts if f["kind"] == "project")["data"]
    assert proj["links"] == ["https://github.com/janeroe/chat-app"]
    cid = next(f for f in facts if f["kind"] == "contact")["id"]
    bad = c.patch(f"/api/profile/facts/{cid}", json={"data": {**ct, "linkedin": "https://example.com/x"}}, headers=h)
    assert bad.status_code == 400 and "LinkedIn" in bad.json()["detail"]
    assert c.patch(f"/api/profile/facts/{cid}", json={"data": {**ct, "email": "nope"}}, headers=h).status_code == 400
    # 4: links card in the profile analysis
    an = c.get("/api/profile/analysis", headers=h).json()["links"]
    assert an["missing"] == [] and any(x["label"] == "GitHub" for x in an["found"]) and an["projects_without_link"] == []
    # 3: label style — contact line shows LinkedIn | GitHub | Portfolio (still clickable); the word GitHub inside a bullet is NOT linked
    jd = c.post("/api/job-descriptions", json={"text": JD2}, headers=h).json()
    d = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": "classic", "link_style": "label"}, headers=h).json()
    raw = c.get(f"/api/resume-versions/{d['id']}/docx", headers=h).content
    z = zipfile.ZipFile(io.BytesIO(raw))
    xml, rels = z.read("word/document.xml").decode(), z.read("word/_rels/document.xml.rels").decode()
    out = Document(io.BytesIO(raw))
    contact_line = next(p.text for p in out.paragraphs if "jane@x.com" in p.text)
    assert "LinkedIn" in contact_line and "GitHub" in contact_line and "Portfolio" in contact_line and "linkedin.com" not in contact_line
    for target in ("https://linkedin.com/in/jane-roe", "https://github.com/janeroe", "https://janeroe.dev", "https://github.com/janeroe/chat-app"):
        assert target in rels, target
    assert xml.count("<w:hyperlink") == 6  # email, phone, 3 contact links, project link
    assert d["report"]["links_report"]["projects_without_link"] == [] and d["resume"]["link_style"] == "label"
    # 6: links can be edited per project in the review step; invalid ones are dropped with a note
    res = d["resume"]; res["projects"][0]["links"] = ["github.com/janeroe/other", "not a link"]
    o = c.patch(f"/api/resume-versions/{d['id']}", json={"resume_json": res}, headers=h).json()
    assert any(w["type"] == "link" for w in o["warnings"])
    assert c.get(f"/api/resume-versions/{d['id']}", headers=h).json()["resume"]["projects"][0]["links"] == ["https://github.com/janeroe/other"]
    # 5: link checker (private addresses are refused unless tests allow them)
    assert lk.check_one("http://127.0.0.1:9/")["status"] == "invalid"
    lk.ALLOW_PRIVATE = True

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/ok" else 403 if self.path == "/blocked" else 404); self.end_headers()
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    got = {r["url"].rsplit("/", 1)[-1]: r["status"] for r in c.post("/api/links/check", json={"urls": [base + "/ok", base + "/missing", base + "/blocked", "mailto:a@b.com", "nonsense"]}, headers=h).json()["results"]}
    srv.shutdown(); lk.ALLOW_PRIVATE = False
    assert got["ok"] == "ok" and got["missing"] == "broken" and got["blocked"] == "blocked" and got["mailto:a@b.com"] == "ok" and got["nonsense"] == "invalid", got


def test_font_size_options_fill_page_and_uniform_sizes():
    import io as _io
    from docx.shared import Pt
    from pypdf import PdfReader
    h = auth("fs@x.com")
    c.post("/api/files", files={"file": ("m.txt", MESSY.encode())}, headers=h)
    jd = c.post("/api/job-descriptions", json={"text": JD2}, headers=h).json()

    def make(fs, tpl="classic"):
        d = c.post("/api/resume-drafts", json={"job_id": jd["id"], "template": tpl, "font_size": fs}, headers=h).json()
        raw = c.get(f"/api/resume-versions/{d['id']}/docx", headers=h).content
        return d, Document(_io.BytesIO(raw))
    normal = lambda doc: doc.styles["Normal"].font.size.pt
    _, ref = make("ref"); _, auto = make("auto"); _, fixed = make("12")
    assert normal(ref) == 10.5
    assert 11 <= normal(auto) <= 12, normal(auto)          # short resume -> text grows to use the page
    assert normal(fixed) == 12 and fixed.paragraphs[0].runs[0].font.size.pt == round(17.5 * 12 / 10.5, 1)
    # auto-fit never adds pages: PDF page count equals the template-size page count
    pages = {}
    for fs in ("ref", "auto"):
        d, _ = make(fs)
        e = c.post(f"/api/resume-versions/{d['id']}/exports", json={"format": "pdf"}, headers=h).json()
        pages[fs] = len(PdfReader(_io.BytesIO(c.get(f"/api/exports/{e['id']}", headers=h).content)).pages)
    assert pages["auto"] == pages["ref"], pages
    # invalid values fall back to auto; editing the version keeps the choice
    d, _ = make("banana")
    assert d["resume"]["font_size"] == "auto"
    res = d["resume"]; res["font_size"] = "11.5"
    c.patch(f"/api/resume-versions/{d['id']}", json={"resume_json": res}, headers=h)
    assert c.get(f"/api/resume-versions/{d['id']}", headers=h).json()["resume"]["font_size"] == "11.5"
    # reference DOCX with mixed sizes -> every body line gets the same size (headings/name excepted)
    ref = Document()
    ref.add_paragraph("Ref Name").runs[0].font.size = Pt(22)
    ref.add_paragraph("a@b.com").runs[0].font.size = Pt(9)
    for t, sz in (("SKILLS", 13), ("PROJECTS", 13), ("EDUCATION", 13)):
        q = ref.add_paragraph(); x = q.add_run(t); x.bold = True; x.font.size = Pt(sz)
        body = ref.add_paragraph(); y = body.add_run("Python" if t == "SKILLS" else "Item"); y.font.size = Pt(8 if t == "SKILLS" else 12)
    rb = _io.BytesIO(); ref.save(rb)
    tp = c.post("/api/templates", files={"file": ("ref.docx", rb.getvalue())}, headers=h).json()
    _, doc = make("11", tp["id"])
    heads = {"SKILLS", "PROJECTS", "EDUCATION"}
    body_sizes = {r_.font.size.pt for i, p in enumerate(doc.paragraphs) if i > 1 and p.text.strip() and p.text.strip() not in heads for r_ in p.runs if r_.text.strip()}
    assert body_sizes <= {11.0, 10.5}, body_sizes
