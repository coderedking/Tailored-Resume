"""Document ingestion: text extraction + structured fact extraction (LLM if configured, else heuristics)."""
import json, re
from . import llm

SKILL_LIST = ["Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "SQL", "PostgreSQL", "MySQL", "SQLite", "MongoDB",
              "Redis", "React", "Next.js", "Node.js", "FastAPI", "Flask", "Django", "Spring Boot", "HTML", "CSS", "Tailwind",
              "Docker", "Kubernetes", "AWS", "Azure", "GCP", "Git", "Linux", "CI/CD", "REST", "GraphQL", "Pandas", "NumPy",
              "scikit-learn", "TensorFlow", "PyTorch", "Keras", "OpenCV", "NLP", "Machine Learning", "Deep Learning",
              "Data Analysis", "Data Visualization", "Tableau", "Power BI", "Excel", "Spark", "Hadoop", "Kafka", "Airflow",
              "ETL", "Data Pipelines", "Model Evaluation", "LLM", "Computer Vision", "Statistics", "Agile", "Scrum", "Jira",
              "Figma", "Selenium", "Playwright", "pytest", "Android", "Kotlin", "Swift", "Flutter", "Streamlit",
              "Matplotlib", "Terraform", "Microservices", "Unit Testing"]
_SK_RE = [(s, re.compile(r"(?<![A-Za-z0-9+#.])" + re.escape(s) + r"(?![A-Za-z0-9+#])", re.I)) for s in SKILL_LIST]
_AL = [("Machine Learning", r"\bML\b", 0), ("JavaScript", r"\bJS\b", 0), ("PostgreSQL", r"\bpostgres\b", re.I), ("Kubernetes", r"\bk8s\b", re.I),
       ("scikit-learn", r"\b(sklearn|scikit learn)\b", re.I), ("Power BI", r"\bpowerbi\b", re.I), ("Node.js", r"\b(nodejs|node js)\b", re.I),
       ("React", r"\b(reactjs|react\.js)\b", re.I), ("MongoDB", r"\bmongo\b", re.I), ("REST", r"\brestful\b", re.I),
       ("Unit Testing", r"\bunit tests?\b", re.I), ("CI/CD", r"\bci ?/ ?cd\b", re.I)]
_AL_RE = [(c, re.compile(p, f)) for c, p, f in _AL]


def find_skills(text):
    t = text or ""
    out = [s for s, rx in _SK_RE if rx.search(t)] + [c for c, rx in _AL_RE if rx.search(t)]
    return list(dict.fromkeys(out))


HEAD = {"experience": "job", "work experience": "job", "professional experience": "job", "employment": "job", "work history": "job",
        "internship": "internship", "internships": "internship", "internship experience": "internship", "training": "internship",
        "projects": "project", "project": "project", "academic projects": "project", "personal projects": "project",
        "technical projects": "project", "key projects": "project", "project work": "project", "major projects": "project",
        "education": "education", "educational qualification": "education", "educational qualifications": "education",
        "academic qualification": "education", "academic qualifications": "education", "academic background": "education",
        "education details": "education", "qualification": "education", "qualifications": "education",
        "certifications": "certification", "certificates": "certification", "certification": "certification", "licenses": "certification",
        "achievements": "achievement", "awards": "achievement", "accomplishments": "achievement", "honors": "achievement",
        "skills": "skills", "technical skills": "skills", "core competencies": "skills", "key skills": "skills", "skills & tools": "skills",
        "tools & technologies": "skills", "technologies": "skills", "tech stack": "skills",
        "summary": "summary", "objective": "summary", "profile": "summary", "professional summary": "summary", "career objective": "summary",
        "about me": "summary", "volunteer experience": "achievement", "leadership": "achievement", "extracurricular activities": "achievement",
        "positions of responsibility": "achievement", "publications": "achievement", "hackathons": "achievement", "activities": "achievement",
        "languages": "extra", "interests": "extra", "hobbies": "extra", "coursework": "extra", "relevant coursework": "extra",
        "courses": "extra", "additional information": "extra", "personal details": "extra"}
HEAD_RULES = [("summary", r"summary|objective|profile|about"), ("skills", r"skill|competenc|technolog|tools|tech stack|expertise"),
              ("internship", r"internship|training"), ("project", r"project"),
              ("achievement", r"achievement|award|honou?r|accomplish|activit|leadership|volunteer|extracurricular|responsibilit|publication|patent|hackathon|competition"),
              ("certification", r"certif|licen[cs]e|credential"), ("education", r"education|academic|qualification|scholastic"),
              ("job", r"experience|employment|work|history"), ("extra", r"language|interest|hobb|additional|personal|declaration|course")]
CORE = {"projects", "project", "skills", "experience", "education", "certifications", "achievements", "summary", "internships", "qualifications"}
VOCAB = set("""technical professional academic personal relevant key core additional other work career summary objective profile skills skill
competencies tools technologies technology tech stack expertise internship internships training experience employment history project projects
education educational qualification qualifications scholastic certifications certification certificates licenses credentials achievements
achievement awards honors honours accomplishments activities extracurricular leadership volunteer volunteering responsibilities position
positions of responsibility publications patents hackathons competitions languages language interests hobbies details information
declaration coursework courses and the of in & about me major""".split())
DATE = re.compile(r"(?P<s>(?:[A-Za-z]{3,9}\.?\s+)?\d{4})\s*(?:-|–|—|to)\s*(?P<e>(?:[A-Za-z]{3,9}\.?\s+)?\d{4}|Present|Current|Ongoing)", re.I)
YEAR = re.compile(r"\b((?:[A-Za-z]{3,9}\.?\s+)?(?:19|20)\d{2})\b")
BULLET = re.compile(r"^[\u2022\u25AA\u00b7\-\*–•●◦▪]\s*")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+?\d[\d\s().-]{8,}\d)")
TECH = re.compile(r"^\s*(?:tech(?:nolog(?:y|ies))?(?: stack| used)?|tools?(?: used)?|stack|built with|languages?(?: and frameworks)?|frameworks?|skills? used)\s*[:\-–]\s*(.+)$", re.I)
ACTION = re.compile(r"^(built|developed|designed|implemented|created|led|managed|analy[sz]ed|worked|used|integrated|deployed|optimi[sz]ed|improved|achieved|automated|trained|performed|conducted|collaborated|engineered|contributed|supported|assisted|maintained|wrote|tested|processed|reduced|increased|launched|configured|migrated|researched|presented|organi[sz]ed|coordinated|mentored|prepared|ensured|responsible|handled|utili[sz]ed|leveraged|applied|a|an|the|this|it|our|my|we|i)\b", re.I)
EDU_RX = re.compile(r"\b(b\.?\s?tech|m\.?\s?tech|b\.?sc|m\.?sc|b\.?com|m\.?com|bachelors?|masters?|diploma|ph\.?d|cgpa|gpa|hsc|ssc|12th|10th|class (?:x|xii|10|12)|higher secondary|secondary school|intermediate|b\.e\.?|m\.e\.?)\b", re.I)
EDU_CS = re.compile(r"\b(BE|BCA|BBA|MCA|MBA|BSc|MSc|BA|MA)\b")
INST_RX = re.compile(r"\b(university|college|institute|school|academy|polytechnic)\b", re.I)
CERT_RX = re.compile(r"\b(certified|certification|certificate|coursera|udemy|nptel|edx|credential|udacity)\b", re.I)


def classify_heading(line, cue=0):
    """Returns a section kind for heading-like lines, else None. cue: 1 = bold/caps styling, 2 = Heading style / rule line."""
    s = re.sub(r"[^A-Za-z&' ]", " ", line)
    words = s.split()
    if not words or len(words) > 5 or re.search(r"\d", line) or line.strip().endswith((".", ",")) or BULLET.match(line.strip()):
        return None
    low = " ".join(words).lower()
    if low in HEAD:
        return HEAD[low]
    shout = line.strip().endswith(":") or cue >= 1 or line.strip().isupper()
    vocab = all(w.lower() in VOCAB for w in words)
    if not shout and not (vocab and len(words) <= 4):
        return None
    if not (vocab or words[-1].lower() in CORE):
        return "custom" if cue >= 2 or (line.strip().isupper() and cue >= 1) else None
    for k, rx in HEAD_RULES:
        if re.search(rx, low):
            return k
    return None


def docx_cue(p):
    t = p.text.strip()
    if not t or len(t.split()) > 6:
        return 0
    sn = (p.style.name or "").lower() if p.style is not None else ""
    if sn.startswith(("heading", "title")) or p._p.xpath("./w:pPr/w:pBdr"):
        return 2
    runs = [r for r in p.runs if r.text.strip()]
    sb = bool(p.style is not None and p.style.font is not None and p.style.font.bold)
    if t.isupper() or (runs and all(r.bold or r.font.bold or sb for r in runs)):
        return 1
    return 0


def docx_is_bullet(p):
    t = p.text.strip()
    sn = (p.style.name or "") if p.style is not None else ""
    return bool(p._p.xpath("./w:pPr/w:numPr") or "List" in sn or BULLET.match(t))


def looks_education(title, org=""):
    h = f"{title} {org}"
    return bool(EDU_RX.search(h) or EDU_CS.search(h))


def extract_text(path, ext):
    if ext == ".docx":
        from docx import Document
        d = Document(path)
        lines = []
        for p in d.paragraphs:
            t = p.text
            if not t.strip():
                lines.append(""); continue
            b = docx_is_bullet(p)
            lines.append(("• " if b and not BULLET.match(t.strip()) else "") + ("" if b else "\x01" * docx_cue(p)) + t)
        for t in d.tables:
            for r in t.rows:
                lines.append(" | ".join(c.text for c in r.cells))
        return "\n".join(lines)
    if ext == ".pdf":
        from pypdf import PdfReader
        rd = PdfReader(path)
        if len(rd.pages) > 20:
            raise ValueError("PDF has too many pages (max 20)")
        text = ""
        try:
            import fitz
            ls = []
            for pg in fitz.open(path):
                for b in pg.get_text("dict")["blocks"]:
                    for l in b.get("lines", []):
                        sp = [x for x in l["spans"] if x["text"].strip()]
                        t = "".join(x["text"] for x in sp).strip()
                        if t:
                            bold = all((x["flags"] & 16) or "bold" in x["font"].lower() for x in sp)
                            ls.append(("\x01" if bold and len(t.split()) <= 6 else "") + t)
            text = "\n".join(ls)
        except Exception:
            pass
        if len(text.strip()) < 30:
            text = "\n".join((p.extract_text() or "") for p in rd.pages)
        if len(text.strip()) < 30:
            raise ValueError("No text layer found (scanned PDF). OCR is not enabled in this build.")
        return text
    return open(path, encoding="utf-8", errors="ignore").read()


def _dates(head):
    m = DATE.search(head)
    if m:
        return m.group("s"), m.group("e"), DATE.sub("", head)
    m = YEAR.search(head)
    return ("", m.group(1), head.replace(m.group(0), "", 1)) if m else ("", "", head)


def _entry(c):
    head = " | ".join(c["head"])
    start, end, clean = _dates(head)
    clean = clean.strip(" ()|,–—-:")
    seps = r"\s+\|\s+|\s+[—–-]\s+|\s+at\s+|\s*@\s*|\s*\|\s*" + (r"|,\s+(?=[A-Z])" if c["kind"] == "education" else "")
    parts = [p.strip(" ,") for p in re.split(seps, clean) if p.strip(" ,")]
    body = clean + " " + " ".join(c["bullets"])
    extra = [t.strip() for x in c["skills_extra"] for t in re.split(r"[,;|/•]", x) if 1 < len(t.strip()) < 40]
    e = {"kind": c["kind"], "title": parts[0] if parts else clean, "organization": parts[1] if len(parts) > 1 else "",
         "start_date": start, "end_date": end, "bullets": c["bullets"], "links": c["links"],
         "skills": list(dict.fromkeys(extra + find_skills(body))), "section": c.get("section", ""),
         "conf": 0.55 + (0.15 if (start or end) else 0) + (0.1 if c["bullets"] else 0) + (0.1 if len(parts) > 1 else 0)}
    return reclass(e)


def reclass(e):
    """Fix obvious section mix-ups (e.g. a degree that landed under Projects)."""
    k = e["kind"]
    if k != "education" and (looks_education(e["title"], e["organization"]) or
                             (k in ("project", "extra", "custom") and INST_RX.search(e["organization"]) and not e.get("skills"))):
        e["kind"] = "education"
    elif k not in ("certification", "education") and CERT_RX.search(f"{e['title']} {e['organization']}") and len(e["bullets"]) <= 1:
        e["kind"] = "certification"
    return e


LINK_RX = re.compile(r"(?:https?://|www\.)\S+|(?:[\w-]+\.)?(?:linkedin|github|gitlab|kaggle|leetcode|medium|behance|dribbble|hackerrank|codeforces|huggingface|stackoverflow)\.(?:com|co|org|io)/\S+"
                     r"|\b[\w-]+\.(?:dev|io|me|com|in|co|app|tech|xyz|site|page|ai|org|net)\b(?:/\S*)?", re.I)


def _phone(text):
    for m in PHONE.finditer(text):
        if 10 <= len(re.sub(r"\D", "", m.group(0))) <= 15:
            return m.group(0).strip()
    return ""


def _header_links(text):
    return list(dict.fromkeys(x.rstrip(".,;|)(") for x in LINK_RX.findall(text)))[:6]


def _profile_links(text):
    return list(dict.fromkeys(re.findall(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+", text, re.I) +
                              re.findall(r"(?:https?://)?(?:www\.)?github\.com/[\w-]+(?![\w/-])", text, re.I)))[:3]


def heuristic(text):
    lines = [(l.lstrip("\x01").rstrip(), len(l) - len(l.lstrip("\x01"))) for l in text.splitlines()]
    clean = "\n".join(l for l, _ in lines)
    first = next((l.strip() for l, _ in lines if l.strip()), "")
    hdr = []
    for l, cue in lines:  # header zone = everything above the first real section heading
        if l.strip():
            if hdr and classify_heading(l.strip(), cue):
                break
            hdr.append(l.strip())
    htext = "\n".join(hdr)
    em = EMAIL.search(htext) or EMAIL.search(clean)
    out = {"contact": {"name": first if len(first) < 60 and "@" not in first else "", "email": em.group(0) if em else "",
                       "phone": _phone(htext) or _phone(clean), "links": _header_links(EMAIL.sub(" ", htext)) or _profile_links(clean)},
           "summary": "", "skills": [], "entries": []}
    section, title, cur, cons, seen_first = None, "", [], None, False
    cur = None

    def new(text_head):
        nonlocal cur
        cur = {"kind": section, "head": [text_head], "bullets": [], "links": [], "skills_extra": [], "section": title}
        cons.append(cur)
    cons = []
    for l, cue in lines:
        s = l.strip()
        if not s:
            continue
        if not seen_first:
            seen_first = True
            if not classify_heading(s, cue):
                continue
        b = BULLET.match(s)
        if not b:
            k = classify_heading(s, cue)
            if k:
                section, title, cur = k, (s.strip(": ").title() if k == "custom" else ""), None
                continue
        if section is None:
            continue
        txt = s[b.end():] if b else s
        if section == "summary":
            out["summary"] += (" " if out["summary"] else "") + txt
        elif section == "skills":
            body = txt.split(":", 1)[-1]
            out["skills"] += [t.strip() for t in re.split(r"[,;|•/]", body) if 1 < len(t.strip()) < 40 and len(t.split()) <= 3] + find_skills(txt)
        elif b:
            if cur is None:
                new(txt)
            else:
                cur["bullets"].append(txt)
        else:
            tm = TECH.match(s)
            if tm and cur:
                cur["skills_extra"].append(tm.group(1)); continue
            if re.match(r"(https?://|www\.|github\.com|linkedin\.com)", s, re.I) and cur:
                cur["links"].append(s); continue
            if cur is not None:
                if cur["bullets"] and s[0].islower():
                    cur["bullets"][-1] += " " + s; continue
                detail = section == "education" and re.search(r"cgpa|gpa|percentage|\d\s?%|marks|grade", s, re.I)
                if detail or len(s) > 70 or s.endswith(".") or s[0].islower() or ACTION.match(s):
                    cur["bullets"].append(s); continue
                dated = bool(_dates(" ".join(cur["head"]))[1])
                if len(cur["head"]) < 2 and not cur["bullets"] and (section in ("job", "internship") or not dated) and len(s) <= 90:
                    cur["head"].append(s); continue
            new(s)
    out["entries"] = [_entry(c) for c in cons]
    out["skills"] = list(dict.fromkeys(out["skills"] or find_skills(clean)))
    return out


TASK = ("Extract this resume into JSON: {\"contact\":{\"name\",\"email\",\"phone\",\"links\":[]},\"summary\":str,\"skills\":[str],"
        "\"entries\":[{\"kind\":\"job|internship|project|education|certification|achievement|custom\",\"section\":\"heading if custom\",\"title\","
        "\"organization\",\"start_date\",\"end_date\",\"bullets\":[str],\"skills\":[str],\"links\":[str]}]}. Put degrees/schools in education, "
        "side work and academic work in project. Copy wording faithfully; leave unknown fields empty.")


def parse(text):
    if llm.available():
        try:
            p = llm.complete_json(TASK, text.replace("\x01", "")[:20000])
            p.setdefault("contact", {}); p.setdefault("summary", ""); p.setdefault("skills", []); p.setdefault("entries", [])
            for e in p["entries"]:
                for k in ("title", "organization", "start_date", "end_date", "section"):
                    e[k] = str(e.get(k) or "")
                e["bullets"] = [str(b) for b in e.get("bullets") or []]
                e["links"] = [str(b) for b in e.get("links") or []]
                e["skills"] = e.get("skills") or find_skills(e["title"] + " " + " ".join(e["bullets"]))
                e["kind"] = e.get("kind") if e.get("kind") in ("job", "internship", "project", "education", "certification", "achievement", "custom", "extra") else "project"
                e["conf"] = 0.85
                reclass(e)
            return p
        except Exception:
            pass
    return heuristic(text)
