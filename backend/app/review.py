"""Profile analysis: goes through every extracted detail and reports what was found, what looks wrong and one-click fixes."""
import re
from .ingest import looks_education, CERT_RX, INST_RX
from . import links as lk

LABEL = {"job": "job", "internship": "internship", "project": "project", "education": "education", "certification": "certification",
         "achievement": "achievement", "custom": "custom section", "extra": "extra", "skills": "skills", "contact": "contact", "summary": "summary"}


def analyze_profile(facts):
    counts, issues = {}, []
    for f in facts:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    add = lambda f, sev, msg, fix=None: issues.append({"fact_id": f["id"] if f else None, "severity": sev, "message": msg, "fix": fix})
    seen = {}
    for f in facts:
        k, d = f["kind"], f["data"]
        t = d.get("title") or d.get("name") or k
        if k in ("job", "internship", "project", "certification", "achievement", "custom", "extra"):
            if looks_education(d.get("title", ""), d.get("organization", "")) and k != "education":
                add(f, "high", f"“{t}” looks like an education entry but is filed as {LABEL[k]}.", {"kind": "education", "label": "Move to Education"})
            elif k not in ("certification",) and CERT_RX.search(f"{t} {d.get('organization', '')}") and len(d.get("bullets", [])) <= 1:
                add(f, "med", f"“{t}” looks like a certification.", {"kind": "certification", "label": "Move to Certifications"})
            key = (k, t.lower())
            if key in seen:
                add(f, "med", f"“{t}” appears twice — delete one.")
            seen[key] = 1
        if k in ("job", "internship"):
            if not (d.get("start_date") or d.get("end_date")):
                add(f, "med", f"“{t}” has no dates.")
            if not d.get("organization"):
                add(f, "low", f"“{t}” has no organisation/company.")
        if k in ("job", "internship", "project") and not d.get("bullets"):
            add(f, "med", f"“{t}” has no description bullets — add what you did and achieved.")
        if k == "project" and not d.get("skills"):
            add(f, "low", f"“{t}” lists no technologies — add them so job keywords can match.")
        if k == "education":
            if not d.get("organization"):
                add(f, "med", f"“{t}” has no institution.")
            if not (d.get("start_date") or d.get("end_date")):
                add(f, "low", f"“{t}” has no year.")
        if d.get("conflict"):
            add(f, "med", f"“{t}” exists twice with different dates — keep the correct one.")
        if not f["verified"] and f["confidence"] < 0.7:
            add(f, "low", f"“{t}” was read with low confidence — please check it.")
    kinds = set(counts)
    if "contact" not in kinds:
        add(None, "high", "No contact details found (name, email, phone).")
    else:
        c = next(f for f in facts if f["kind"] == "contact")["data"]
        for k in ("name", "email", "phone"):
            if not c.get(k):
                add(None, "high", f"Contact: {k} is missing.")
    if "education" not in kinds:
        add(None, "high", "No education found — use “Add something new”.")
    if not kinds & {"project", "job", "internship"}:
        add(None, "high", "No projects or experience found — use “Add something new”.")
    if "project" not in kinds:
        add(None, "med", "No projects found. If your resume has a projects section, add them or fix their type below.")
    sk = next((f for f in facts if f["kind"] == "skills"), None)
    if not sk or len(sk["data"].get("skills", [])) < 5:
        add(None, "med", "Fewer than 5 skills listed — add the tools you use.")
    order = {"high": 0, "med": 1, "low": 2}
    issues.sort(key=lambda i: order[i["severity"]])
    score = max(0, 100 - 15 * sum(i["severity"] == "high" for i in issues) - 6 * sum(i["severity"] == "med" for i in issues) - 2 * sum(i["severity"] == "low" for i in issues))
    cf = next((f for f in facts if f["kind"] == "contact"), None)
    c = lk.normalize_contact(cf["data"])[0] if cf else {}
    found = [{"label": n, "url": c[k]} for n, k in (("LinkedIn", "linkedin"), ("GitHub", "github"), ("Portfolio", "portfolio")) if c.get(k)]
    found += [{"label": lk.label(u), "url": u} for u in c.get("links", [])]
    nolink = [{"fact_id": f["id"], "title": f["data"].get("title", "")} for f in facts if f["kind"] == "project" and not f["data"].get("links")]
    urls = lk.dedupe([x["url"] for x in found] + [u for f in facts if f["kind"] in ("project", "job", "internship") for u in f["data"].get("links", [])])
    missing = [n for n in ("LinkedIn", "GitHub") if n not in {x["label"] for x in found}]
    return {"counts": counts, "issues": issues, "completeness": score,
            "links": {"found": found, "missing": missing, "optional_missing": [] if c.get("portfolio") else ["Portfolio"], "projects_without_link": nolink, "all_urls": urls}}
