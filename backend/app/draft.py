"""Evidence-grounded drafting + deterministic validation. Uses EVERY verified detail, ordered by relevance, and only
trims (lowest relevance first) when the page limit would be exceeded. The LLM (optional) only proposes wording."""
import json, math, re
from . import llm, match
from . import links as lk
from .ingest import find_skills
from .render import TEMPLATES, blocks, nd

NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?%?")
GROUPS = [("Languages", "python java javascript typescript c++ c# sql kotlin swift"),
          ("Web & APIs", "react next.js node.js fastapi flask django spring boot html css tailwind rest graphql flutter android streamlit"),
          ("Data & ML", "pandas numpy scikit-learn tensorflow pytorch keras opencv nlp machine learning deep learning data analysis data visualization tableau power bi excel spark hadoop kafka airflow etl data pipelines model evaluation llm computer vision statistics matplotlib"),
          ("Databases", "postgresql mysql sqlite mongodb redis"),
          ("Cloud & DevOps", "docker kubernetes aws azure gcp git linux ci/cd terraform microservices"),
          ("Tools & Practices", "pytest selenium playwright unit testing agile scrum jira figma")]


def _group(skill):
    for g, words in GROUPS:
        if re.search(r"(?<![\w+#.])" + re.escape(skill.lower()) + r"(?![\w+#])", words):
            return g
    return "Other"


def _join(a):
    return a[0] if len(a) == 1 else ", ".join(a[:-1]) + " and " + a[-1]


def _rank_bullets(bullets, req):
    wanted = {s["name"].lower() for s in req["required_skills"] + req["preferred_skills"]}
    rt = match.toks(" ".join(req["responsibilities"]))
    return sorted(bullets, key=lambda b: -(2 * sum(1 for s in find_skills(b) if s.lower() in wanted) + len(match.toks(b) & rt) / 5))


def _llm_pack(sel, facts, req):
    keep = [f for _, f, _ in sel] + [f for f in facts if f["kind"] in ("education", "summary")]
    payload = json.dumps({"facts": [{"id": f["id"], "kind": f["kind"], "title": f["data"].get("title") or f["data"].get("text"),
                                     "organization": f["data"].get("organization"), "bullets": f["data"].get("bullets", []),
                                     "skills": f["data"].get("skills", [])} for f in keep], "requirements": req})
    task = ("Return {\"summary\":{\"text\",\"evidence_ids\":[]},\"items\":[{\"fact_id\",\"bullets\":[{\"text\",\"evidence_ids\":[]}]}]}. "
            "summary = a 2-3 sentence professional INTRODUCTION (who the candidate is, strongest relevant skills and experience, "
            "and the role sought). Rewrite each item's bullets as concise, ATS-friendly, action-verb lines that emphasise the job's "
            "keywords, keeping ALL original information (up to 6 bullets per item). Use ONLY the facts given: no new numbers, tools or claims. "
            "Every bullet and the summary must cite evidence_ids from the facts.")
    return llm.complete_json(task, payload, 6000)


def _summary(facts, ent, req, skills, names, dom=()):
    ev, sents = [], []
    own = next((f for f in facts if f["kind"] == "summary" and f["data"].get("text")), None)
    edu = next((f for f in facts if f["kind"] == "education" and f["data"].get("title")), None)
    if own:
        t = " ".join(re.split(r"(?<=[.!?])\s+", own["data"]["text"].strip())[:2])[:300]
        sents.append(t); ev.append(own["id"])
    elif edu:
        d = edu["data"]
        sents.append(f"Candidate with a {d['title']} background" + (f" from {d['organization']}" if d.get("organization") else "") + ".")
        ev.append(edu["id"])
    top = [s["name"] for s in skills if s["name"].lower() in names][:4] or [s["name"] for s in skills][:4]
    if top:
        roles = [e["title"] + (f" at {e['organization']}" if e["organization"] else "") for e in ent if e["kind"] in ("job", "internship")][:2]
        projs = [e["title"] for e in ent if e["kind"] == "project"][:2]
        how = (["as " + " and ".join(roles)] if roles else []) + (["through projects such as " + " and ".join(projs)] if projs else [])
        sents.append(f"Hands-on experience with {_join(top)}" + (" gained " + " and ".join(how) if how else "") + (f", with a focus on {_join(list(dom))}" if dom else "") + ".")
        ev += [e["fact_id"] for e in ent]
        if req.get("title"):
            sents.append(f"Seeking the {req['title']} role to contribute {_join(top[:3])} skills.")
    return " ".join(sents), list(dict.fromkeys(ev))


def _build(facts, req, cfg, template, use_llm):
    notes = []
    byid = {f["id"]: f for f in facts}
    contact = lk.normalize_contact(next((f["data"] for f in facts if f["kind"] == "contact"), {}))[0]
    contact = {**contact, "links": lk.all_links(contact)}  # LinkedIn, GitHub, Portfolio, then other links
    sel = sorted(((match.score_item(f["data"], req, f["priority"])["score"], f, match.score_item(f["data"], req, f["priority"]))
                  for f in facts if f["kind"] in ("job", "internship", "project")), key=lambda t: -t[0])
    out = None
    if use_llm and llm.available() and sel:
        try:
            out = _llm_pack(sel, facts, req)
        except Exception:
            notes.append({"type": "info", "message": "AI unavailable; used rule-based drafting."})
    llm_items = {i.get("fact_id"): i.get("bullets", []) for i in (out or {}).get("items", [])}

    def entry(f, s):
        d, bl, ev = f["data"], [], []
        for b in llm_items.get(f["id"], [])[:8]:
            ids = [i for i in b.get("evidence_ids", []) if i in byid]
            if ids and b.get("text"):
                bl.append(b["text"]); ev.append(ids)
            else:
                notes.append({"type": "unsupported", "message": f"Dropped an AI bullet without valid evidence ({d.get('title', '')})."})
        if not bl:
            bl = _rank_bullets(d.get("bullets", []), req)
            ev = [[f["id"]] for _ in bl]
        return {"fact_id": f["id"], "kind": f["kind"], "title": d.get("title", ""), "name": d.get("title", ""),
                "organization": d.get("organization", ""), "start_date": d.get("start_date", ""), "end_date": d.get("end_date", ""),
                "technologies": sorted(d.get("skills", []), key=lambda x: x.lower() not in names), "links": d.get("links", []), "bullets": bl, "evidence": ev, "relevance": s["score"], "matched_skills": s["matched_skills"]}
    names = {s["name"].lower() for s in req["required_skills"] + req["preferred_skills"]}
    req_first = {s["name"].lower() for s in req["required_skills"]}
    ent = [entry(f, s) for _, f, s in sel]
    plain = lambda k: [{"fact_id": f["id"], "title": f["data"].get("title", ""), "organization": f["data"].get("organization", ""),
                        "start_date": f["data"].get("start_date", ""), "end_date": f["data"].get("end_date", ""),
                        "bullets": f["data"].get("bullets", [])} for f in facts if f["kind"] == k]
    prof = list(dict.fromkeys([s for f in facts if f["kind"] == "skills" for s in f["data"].get("skills", [])] +
                              [s for e in ent for s in e["technologies"]]))[:40]
    order = [g for g, _ in GROUPS] + ["Other"]
    evf = lambda w: [f["id"] for f in facts if w.lower() in match.item_text(f["data"]).lower() or w.lower() in {x.lower() for x in f["data"].get("skills", [])}]
    ptext = " ".join(match.item_text(f["data"]) for f in facts).lower()
    key = sorted([x for x in prof if x.lower() in names], key=lambda x: x.lower() not in req_first)
    rest = sorted([x for x in prof if x.lower() not in names], key=lambda x: (order.index(_group(x)), x.lower()))
    terms = [t for t in req.get("keywords", []) if t.lower() in ptext][:8]
    skills = ([{"name": x, "group": "Key Skills", "evidence_ids": evf(x)} for x in key] +
              [{"name": t, "group": "Key Skills", "evidence_ids": evf(t)} for t in terms] +
              [{"name": x, "group": _group(x), "evidence_ids": evf(x)} for x in rest])
    dom = [x for x in req["domain_terms"] if x in ptext][:2]
    summ, sev = _summary(facts, ent, req, skills, names, dom)
    if out and out.get("summary", {}).get("text"):
        ids = [i for i in out["summary"].get("evidence_ids", []) if i in byid]
        if ids:
            summ, sev = out["summary"]["text"], ids
    ach = [x for f in facts if f["kind"] == "achievement" for x in [f["data"].get("title", "")] + f["data"].get("bullets", []) if x]
    extra = [x for f in facts if f["kind"] == "extra" for x in [f["data"].get("title", "")] + f["data"].get("bullets", []) if x]
    cust = {}
    for f in facts:
        if f["kind"] == "custom":
            d = f["data"]
            cust.setdefault((d.get("section") or "Additional").strip().title(), []).append({
                "fact_id": f["id"], "title": d.get("title", ""), "organization": d.get("organization", ""), "start_date": d.get("start_date", ""),
                "end_date": d.get("end_date", ""), "bullets": d.get("bullets", []), "technologies": d.get("skills", [])})
    used = [e["fact_id"] for e in ent] + [f["id"] for f in facts if f["kind"] in ("education", "certification", "achievement", "extra", "custom", "contact", "skills", "summary")]
    resume = {"contact": contact, "summary": summ, "summary_evidence": sev, "skills": skills,
              "experience": [e for e in ent if e["kind"] != "project"], "projects": [e for e in ent if e["kind"] == "project"],
              "education": plain("education"), "certifications": plain("certification"), "achievements": ach, "additional": extra, "custom": [{"section": k, "entries": v} for k, v in cust.items()],
              "template": template, "used_fact_ids": used, "link_style": cfg.get("link_style", "url")}
    return resume, notes


def lines_per_page(cfg):
    return int((11 - 2 * cfg["margin"]) * 72 / (cfg["size"] * 1.3))


def est_lines(r, cfg):
    cpl = max(40, int((8.5 - 2 * cfg["margin"]) * 72 / (cfg["size"] * 0.5)))
    n = 3
    for _, _, items in blocks(r, cfg):
        n += 2
        for it in items:
            n += math.ceil(len(it[1]) / cpl) if it[0] == "line" else (1 if (it[1] or it[2] or it[3]) else 0) + (1 if it[5] else 0) + sum(math.ceil((len(b) + 2) / cpl) for b in it[4])
    return n


def build_draft(facts, req, cfg, template):
    """Include everything, ordered by relevance; if over the page limit trim the least relevant bullets, then items."""
    r, notes = _build(facts, req, cfg, template, True)
    cap = lines_per_page(cfg) * cfg.get("max_pages", 2)
    cut_b, cut_i = 0, []
    items = lambda: r["experience"] + r["projects"]
    while est_lines(r, cfg) > cap:
        cand = [e for e in items() if len(e["bullets"]) > 2]
        if cand:
            e = min(cand, key=lambda e: (e["relevance"], -len(e["bullets"])))
            e["bullets"].pop(); e["evidence"].pop() if e["evidence"] else None; cut_b += 1
        elif len(items()) > 1:
            e = min(items(), key=lambda e: e["relevance"])
            for sec in ("experience", "projects"):
                r[sec] = [x for x in r[sec] if x is not e]
            r["used_fact_ids"] = [i for i in r["used_fact_ids"] if i != e["fact_id"]]
            cut_i.append(e["title"])
        else:
            break
    if cut_b or cut_i:
        notes.append({"type": "info", "message": f"To fit {cfg.get('max_pages', 2)} pages, trimmed {cut_b} low-relevance bullet(s)" +
                      (f" and left out: {', '.join(cut_i)}" if cut_i else "") + "."})
    return r, notes


def validate(resume, facts, req=None):
    byid = {f["id"]: f for f in facts}
    W = []
    ev_text = lambda ids: " ".join(match.item_text(byid[i]["data"]) for i in ids if i in byid).lower()

    def check(text, ids, where):
        ids = [i for i in ids if i in byid]
        if not ids:
            W.append({"type": "unsupported", "message": f"{where}: no supporting evidence for “{text[:70]}”"}); return
        et = ev_text(ids)
        for n in NUM.findall(text):
            if n.strip(".,").lower() not in et:
                W.append({"type": "unsupported", "message": f"{where}: number “{n}” not found in evidence"})
        evs = {x.lower() for x in find_skills(et)}
        for s in find_skills(text):
            if s.lower() not in et and s.lower() not in evs:
                W.append({"type": "unsupported", "message": f"{where}: tool “{s}” not found in evidence"})
    if resume.get("summary"):
        check(resume["summary"], resume.get("summary_evidence", []), "Summary")
    for sec in ("experience", "projects", "education", "certifications", "custom"):
        for it in (resume.get(sec, []) if sec != "custom" else [e for c in resume.get("custom", []) for e in c["entries"]]):
            f = byid.get(it.get("fact_id"))
            if not f:
                W.append({"type": "unsupported", "message": f"{it.get('title', '?')}: not linked to a profile fact"}); continue
            for k in ("title", "organization", "start_date", "end_date"):
                if it.get(k, "") != f["data"].get(k, ""):
                    W.append({"type": "changed", "message": f"{it.get('title', '')}: {k} differs from your verified profile"})
            if not f["verified"] and f["confidence"] < 0.7:
                W.append({"type": "review", "message": f"“{it.get('title', '')}” was auto-extracted with low confidence — verify it"})
            ev = it.get("evidence", [])
            for j, b in enumerate(it.get("bullets", [])):
                check(b, ev[j] if j < len(ev) else [it["fact_id"]], it.get("title", ""))
    for s in resume.get("skills", []):
        if not [i for i in s.get("evidence_ids", []) if i in byid]:
            W.append({"type": "unsupported", "message": f"Skill “{s['name']}” has no evidence"})
    return W


def resume_text(r):
    parts = [r.get("summary", "")] + [s["name"] for s in r.get("skills", [])]
    for sec in ("experience", "projects", "education", "certifications"):
        for i in r.get(sec, []):
            parts += [i.get("title", ""), i.get("organization", "")] + i.get("bullets", []) + i.get("technologies", [])
    return "\n".join(parts + r.get("achievements", []) + r.get("additional", []))


def _prof(facts):
    p = set()
    for f in facts:
        p |= {s.lower() for s in f["data"].get("skills", [])} | {s.lower() for s in find_skills(match.item_text(f["data"]))}
    return p


def profile_gaps(facts, req):
    p = _prof(facts)
    return {"missing_required": [s["name"] for s in req["required_skills"] if s["name"].lower() not in p],
            "missing_preferred": [s["name"] for s in req["preferred_skills"] if s["name"].lower() not in p]}


def analyze(r, facts, req, cfg=None):
    """Keyword coverage + ATS checklist + unused details. A transparency aid, NOT a hiring probability."""
    cfg = cfg or TEMPLATES["classic"]
    txt = resume_text(r)
    low = txt.lower()
    found = {s.lower() for s in find_skills(txt)}
    prof = _prof(facts)
    wanted = {s["name"]: s["importance"] for s in req["required_skills"] + req["preferred_skills"]}
    tot = sum(wanted.values())
    kw = sum(w for k, w in wanted.items() if k.lower() in found) / tot if tot else 0
    terms = req.get("keywords", [])
    tf = [t for t in terms if t.lower() in low]
    rt = match.toks(" ".join(req["responsibilities"] + terms))
    resp = min(1.0, 2 * len(rt & match.toks(txt)) / len(rt)) if rt else 0
    dom = req["domain_terms"]
    dom_hit = [d for d in dom if d in low]
    parts = [(0.5, kw, tot > 0), (0.15, len(tf) / len(terms) if terms else 0, bool(terms)), (0.2, resp, bool(rt)),
             (0.15, len(dom_hit) / len(dom) if dom else 0, bool(dom))]
    den = sum(w for w, _, ok in parts if ok) or 1
    score = round(100 * sum(w * v for w, v, ok in parts if ok) / den)
    gaps = profile_gaps(facts, req)
    c, items = r.get("contact", {}), r.get("experience", []) + r.get("projects", [])
    det = []
    for k, label in (("name", "name"), ("email", "email"), ("phone", "phone number")):
        if not c.get(k):
            det.append({"field": k, "message": f"Add your {label} — it's missing from the resume."})
    if not r.get("summary"):
        det.append({"field": "summary", "message": "No summary — add skills to your profile or write one."})
    if not r.get("education"):
        det.append({"field": "education", "message": "No education entry found in your profile."})
    if not items:
        det.append({"field": "experience", "message": "No projects/internships/jobs found — add some to your profile."})
    for i in items:
        if not (i.get("start_date") or i.get("end_date")) and i.get("kind") != "project":
            det.append({"field": "dates", "message": f"“{i['title']}” has no dates."})
        if not i.get("bullets"):
            det.append({"field": "bullets", "message": f"“{i['title']}” has no description bullets."})
    if items and not any(re.search(r"\d", b) for i in items for b in i.get("bullets", [])):
        det.append({"field": "metrics", "message": "No numbers/results in any bullet. Add real metrics to your profile entries if you have them (never invent)."})
    if re.search(r"certif", " ".join(req.get("responsibilities", [])), re.I) and not r.get("certifications"):
        det.append({"field": "certifications", "message": "The job mentions certifications; none are in your profile."})
    used = set(r.get("used_fact_ids", []))
    unused = [f["data"].get("title") or f["kind"] for f in facts if f["kind"] in ("job", "internship", "project") and f["id"] not in used]
    ats, pages = [], est_lines(r, cfg) / max(1, lines_per_page(cfg))
    add = lambda ok, m: ats.append({"ok": bool(ok), "message": m})
    add(True, "Single-column layout, standard font, real bullets — no tables, text boxes, images or header/footer content")
    add(True, "Standard section headings (Professional Summary, Technical Skills, Work Experience, Projects, Education)")
    add(c.get("email") and c.get("phone"), "Email and phone are plain text in the body" if c.get("email") and c.get("phone") else "Add both email and phone so parsers can find them")
    baddates = [i["title"] for i in r.get("experience", []) + r.get("projects", []) + r.get("education", [])
                for d in (nd(i.get("start_date", "")), nd(i.get("end_date", ""))) if d and not re.fullmatch(r"((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) )?\d{4}|Present", d)]
    add(not baddates, "Dates use one consistent format (Mon YYYY – Mon YYYY)" if not baddates else f"Unrecognised date format in: {', '.join(dict.fromkeys(baddates))[:80]}")
    long_b = [b for i in items for b in i.get("bullets", []) if len(b) > 220]
    add(not long_b, "Bullets are concise" if not long_b else f"{len(long_b)} bullet(s) are over 220 characters — shorten them")
    add(pages <= cfg.get("max_pages", 2) + 0.05, f"Estimated length ≈ {pages:.1f} page(s)")
    add(kw >= 0.6 or not tot, f"Job keyword coverage {round(100 * kw)}% (aim for 60%+ using only true skills)")
    add(len(re.findall(r"[.!?]", r.get("summary", ""))) >= 2, "Summary reads as a short introduction (2+ sentences)")
    return {"score": score, "keyword_coverage": round(100 * kw), "covered": [k for k in wanted if k.lower() in found],
            "unused_supported": [k for k in wanted if k.lower() in prof and k.lower() not in found],
            "missing_required": gaps["missing_required"], "missing_preferred": gaps["missing_preferred"],
            "terms_found": tf, "terms_missing": [t for t in terms if t not in tf], "domain_found": dom_hit, "domain_missing": [d for d in dom if d not in dom_hit], "missing_details": det, "links_report": links_report(r),
            "ats": ats, "unused_facts": unused}


def links_report(r):
    c = r.get("contact", {})
    shown = [{"label": lk.label(u, c.get("portfolio", "")), "url": u, "where": "Contact"} for u in c.get("links", [])]
    for sec in ("experience", "projects", "education", "certifications"):
        shown += [{"label": lk.label(u), "url": u, "where": i.get("title", sec)} for i in r.get(sec, []) for u in i.get("links", [])]
    have = {x["label"] for x in shown}
    return {"shown": shown, "missing": [n for n in ("LinkedIn", "GitHub") if n not in have],
            "projects_without_link": [{"fact_id": p["fact_id"], "title": p["title"]} for p in r.get("projects", []) if not p.get("links")]}
