"""Job description analysis + explainable hybrid relevance ranking (weights from the blueprint)."""
import re, datetime
from .ingest import find_skills

STOP = set("the and for with that this from are you our will have has into your their about such using use work team "
           "role able experience ability strong years year skills including etc across within who can".split())
DOMAINS = ["machine learning", "deep learning", "data science", "data engineering", "web development", "frontend", "backend",
           "full stack", "devops", "cloud", "mobile", "nlp", "computer vision", "analytics", "cybersecurity", "deployment",
           "testing", "automation", "embedded", "fintech", "healthcare", "e-commerce"]
W = dict(skill=0.35, resp=0.25, domain=0.15, recency=0.10, evidence=0.10, priority=0.05)


def toks(s):
    return {w for w in re.findall(r"[a-z][a-z+#.]{2,}", (s or "").lower()) if w not in STOP}


def item_text(d):
    return " ".join([d.get("title", ""), d.get("organization", ""), d.get("text", ""), " ".join(d.get("bullets", [])),
                     " ".join(d.get("skills", []))])


TERMS = """data pipelines|feature engineering|api design|code review|version control|object-oriented|data structures|algorithms|system design|responsive design|accessibility|performance optimization|test automation|integration testing|cross-functional|stakeholders|dashboards|reporting|data cleaning|exploratory data analysis|a/b testing|monitoring|logging|scalability|authentication|oauth|jwt|database design|infrastructure as code|containerization|orchestration|cloud computing|distributed systems|event-driven|message queues|regression|classification|clustering|neural networks|recommendation systems|time series|big data|data warehouse|data modeling|data quality|documentation|debugging|troubleshooting|agile methodology|code quality|mobile app|user interface|wireframes|prototyping|vulnerability|incident response|networking|web scraping|automation|deployment|optimization|visualization|payments|security|encryption|real-time|low latency|rest apis|web applications|machine learning models|predictive models|data analysis|problem solving|communication|collaboration""".split("|")


def extract_terms(text, known):
    low, kn = text.lower(), {k.lower() for k in known}
    out = [t for t in TERMS if re.search(r"(?<![a-z])" + re.escape(t) + r"s?(?![a-z])", low) and t not in kn]
    acr = [a for a in dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9]{1,5}\b", text)) if a.lower() not in kn and a not in
           {"AND", "THE", "FOR", "USA", "CEO", "WITH", "YOU", "OUR", "ALL", "NOT", "ARE", "BUT", "ANY", "HR", "US", "UK", "ID", "OK", "PM", "AM"}]
    return (out + acr)[:18]


def analyze_jd(text):
    mode, req, pref, resp = "required", {}, {}, []
    for line in text.splitlines():
        l = line.strip()
        if not l:
            continue
        if re.search(r"preferred|nice to have|bonus|good to have|\bplus\b", l, re.I):
            mode = "preferred"
        elif re.search(r"requirements?|must have|required|qualifications|responsibilit|what you", l, re.I) and len(l) < 60:
            mode = "required"
        for s in find_skills(l):
            (req if mode == "required" else pref).setdefault(s, 1.0 if mode == "required" else 0.5)
        if re.match(r"^[\u2022\-\*•]\s*\S", l) and len(l) > 20:
            resp.append(re.sub(r"^[\u2022\-\*•]\s*", "", l))
    for s in list(pref):
        if s in req:
            del pref[s]
    low = text.lower()
    sen = re.search(r"\b(intern(?:ship)?|entry[- ]level|junior|senior|lead|principal|staff)\b", low)
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    title = first if 0 < len(first.split()) <= 6 and ":" not in first and not re.match(r"(about|job|we |company|position|role)", first, re.I) else ""
    known = list(req) + list(pref)
    return {"title": title, "keywords": extract_terms(text, known), "required_skills": [{"name": k, "importance": v} for k, v in req.items()],
            "preferred_skills": [{"name": k, "importance": v} for k, v in pref.items()],
            "responsibilities": resp[:15], "domain_terms": [d for d in DOMAINS if d in low],
            "seniority": sen.group(1) if sen else ""}


def _recency(d):
    e = (d.get("end_date") or "").lower()
    if not e or e in ("present", "current", "ongoing"):
        return 1.0
    y = re.findall(r"\d{4}", e)
    return max(0.0, 1 - (datetime.date.today().year - int(y[-1])) / 8) if y else 0.5


def score_item(d, req, priority=0):
    txt = item_text(d)
    have = {s.lower() for s in d.get("skills", [])} | {s.lower() for s in find_skills(txt)}
    wanted = {s["name"].lower(): s["importance"] for s in req["required_skills"] + req["preferred_skills"]}
    tot = sum(wanted.values())
    skill = sum(w for k, w in wanted.items() if k in have) / tot if tot else 0
    rt, it = toks(" ".join(req["responsibilities"] + req.get("keywords", []))), toks(txt)
    resp = min(1.0, 2 * len(rt & it) / len(rt)) if rt else 0
    dom = req["domain_terms"]
    domain = sum(1 for t in dom if t in txt.lower()) / len(dom) if dom else 0
    b = d.get("bullets", [])
    evidence = min(1.0, 0.6 * min(len(b), 3) / 3 + (0.4 if any(re.search(r"\d", x) for x in b) else 0))
    br = dict(skill=skill, resp=resp, domain=domain, recency=_recency(d), evidence=evidence, priority=1.0 if priority else 0.0)
    score = sum(W[k] * v for k, v in br.items())
    return {"score": round(score, 3), "breakdown": {k: round(v, 2) for k, v in br.items()},
            "matched_skills": sorted(k for k in wanted if k in have)}


def rank(facts, req):
    out = []
    for f in facts:
        if f["kind"] in ("job", "internship", "project"):
            s = score_item(f["data"], req, f["priority"])
            out.append({"fact_id": f["id"], "kind": f["kind"], "title": f["data"].get("title", ""), **s})
    return sorted(out, key=lambda r: -r["score"])
