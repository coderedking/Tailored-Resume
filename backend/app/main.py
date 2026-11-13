import hashlib, json, os, re, secrets, shutil, time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import db, draft, ingest, llm, match, review
from . import links as lk
from .render import TEMPLATES, extract_style, norm_font_size, to_docx, to_pdf

MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024
ALLOWED = {".pdf": b"%PDF", ".docx": b"PK", ".txt": None}
app = FastAPI(title="Tailored Resume Platform")
api = APIRouter(prefix="/api")
db.init()


# ---------- auth (PBKDF2 passwords, opaque server-side session tokens stored hashed) ----------
def _hp(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def _session(user_id):
    t = secrets.token_urlsafe(32)
    db.x("insert into sessions values(?,?,?)", (hashlib.sha256(t.encode()).hexdigest(), user_id, time.time() + 7 * 86400))
    return t


def user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    h = hashlib.sha256(authorization[7:].encode()).hexdigest()
    r = db.q("select user_id from sessions where token_hash=? and expires_at>?", (h, time.time()), True)
    if not r:
        raise HTTPException(401, "Session expired")
    return r["user_id"]


def audit(uid, event, rid=""):
    db.x("insert into audit(user_id,event,resource_id,ts) values(?,?,?,?)", (uid, event, rid, time.time()))


class Cred(BaseModel):
    email: str
    password: str


@api.post("/auth/register")
def register(c: Cred):
    email = c.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(c.password) < 8:
        raise HTTPException(400, "Valid email and a password of 8+ characters required")
    if db.q("select 1 from users where email=?", (email,), True):
        raise HTTPException(409, "Email already registered")
    uid, salt = db.uid("user"), secrets.token_hex(16)
    db.x("insert into users values(?,?,?,?,?)", (uid, email, _hp(c.password, salt), salt, time.time()))
    audit(uid, "register")
    return {"token": _session(uid), "email": email}


@api.post("/auth/login")
def login(c: Cred):
    r = db.q("select * from users where email=?", (c.email.strip().lower(),), True)
    if not r or not secrets.compare_digest(r["pw_hash"], _hp(c.password, r["salt"])):
        raise HTTPException(401, "Invalid email or password")
    audit(r["id"], "login")
    return {"token": _session(r["id"]), "email": r["email"]}


@api.get("/me")
def me(uid: str = Depends(user)):
    r = db.q("select email from users where id=?", (uid,), True)
    return {"email": r["email"], "llm_enabled": llm.available()}


@api.get("/templates")
def templates(uid: str = Depends(user)):
    out = [{"id": k, "label": v["label"], "custom": False} for k, v in TEMPLATES.items()]
    return out + [{"id": r["id"], "label": r["name"], "custom": True} for r in db.q("select id,name from templates where user_id=?", (uid,))]


TPL_TYPES = {".docx": b"PK", ".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8", ".webp": b"RIFF"}


@api.post("/templates")
def add_template(file: UploadFile = File(...), uid: str = Depends(user)):
    name = Path(file.filename or "").name
    ext = Path(name).suffix.lower()
    data = file.file.read(MAX_UPLOAD + 1)
    if ext not in TPL_TYPES or not data.startswith(TPL_TYPES[ext]) or len(data) > MAX_UPLOAD:
        raise HTTPException(400, "Upload a reference resume as DOCX, PDF, PNG, JPG or WEBP (max %d MB)" % (MAX_UPLOAD // 1048576))
    tid = db.uid("tpl")
    d = Path(db.STORAGE) / uid
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{tid}{ext}"
    path.write_bytes(data)
    try:
        st = extract_style(str(path), ext)
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Could not read this reference file")
    label = Path(name).stem[:40] + " (your template)"
    db.x("insert into templates values(?,?,?,?,?,?)", (tid, uid, label, str(path), json.dumps(st), time.time()))
    return {"id": tid, "label": label, "style": st}


@api.delete("/templates/{tid}")
def del_template(tid: str, uid: str = Depends(user)):
    r = db.q("select path from templates where id=? and user_id=?", (tid, uid), True)
    if r:
        Path(r["path"]).unlink(missing_ok=True)
        db.x("delete from templates where id=? and user_id=?", (tid, uid))
    return {"deleted": True}


# ---------- files + ingestion ----------
def save_parsed(uid, fid, p):
    existing, n = db.facts_of(uid), 0
    kinds = {f["kind"] for f in existing}
    c = p.get("contact") or {}
    if "contact" not in kinds and any(c.values()):
        db.add_fact(uid, "contact", lk.normalize_contact(c)[0], 0.8, fid); n += 1
    if "summary" not in kinds and p.get("summary"):
        db.add_fact(uid, "summary", {"text": p["summary"]}, 0.8, fid); n += 1
    sk = list(dict.fromkeys(p.get("skills") or []))
    old = next((f for f in existing if f["kind"] == "skills"), None)
    if old:
        merged = list(dict.fromkeys(old["data"]["skills"] + sk))
        db.x("update facts set data=?, updated_at=? where id=? and user_id=?", (json.dumps({"skills": merged}), time.time(), old["id"], uid))
    elif sk:
        db.add_fact(uid, "skills", {"skills": sk}, 0.8, fid); n += 1
    for e in p.get("entries", []):
        data = {k: e.get(k, "" if k not in ("bullets", "skills", "links") else []) for k in ("title", "organization", "start_date", "end_date", "bullets", "skills", "links")}
        if e["kind"] == "custom":
            data["section"] = e.get("section") or "Additional"
        same = [f for f in existing if f["kind"] == e["kind"] and f["data"].get("title", "").lower() == data["title"].lower()]
        if same:
            if any((f["data"].get("start_date"), f["data"].get("end_date")) == (data["start_date"], data["end_date"]) for f in same):
                continue
            data["conflict"] = True
        db.add_fact(uid, e["kind"], data, e.get("conf", 0.6), fid); n += 1
    return n


@api.post("/files")
def upload(file: UploadFile = File(...), uid: str = Depends(user)):
    name = Path(file.filename or "").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Only PDF, DOCX or TXT files are allowed")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD // 1048576} MB)")
    if ALLOWED[ext] and not data.startswith(ALLOWED[ext]):
        raise HTTPException(400, "File content does not match its extension")
    sha = hashlib.sha256(data).hexdigest()
    dup = db.q("select id from files where user_id=? and sha256=?", (uid, sha), True)
    if dup:
        return {"id": dup["id"], "status": "duplicate", "facts_added": 0}
    fid = db.uid("file")
    d = Path(db.STORAGE) / uid
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{fid}{ext}"
    path.write_bytes(data)
    db.x("insert into files values(?,?,?,?,?,?,?)", (fid, uid, name, str(path), sha, "processing", time.time()))
    n, err = 0, None
    try:
        n = save_parsed(uid, fid, ingest.parse(ingest.extract_text(str(path), ext)))
        status = "needs_review"
    except Exception as e:
        status, err = "failed", str(e)[:200]
    db.x("update files set parse_status=? where id=?", (status, fid))
    audit(uid, "file_upload", fid)
    return {"id": fid, "status": status, "facts_added": n, "error": err}


@api.get("/files")
def files(uid: str = Depends(user)):
    return [dict(r) for r in db.q("select id,filename,parse_status,created_at from files where user_id=?", (uid,))]


@api.get("/files/{fid}/status")
def file_status(fid: str, uid: str = Depends(user)):
    r = db.q("select id,parse_status from files where id=? and user_id=?", (fid, uid), True)
    if not r:
        raise HTTPException(404, "Not found")
    return dict(r)


@api.delete("/files/{fid}")
def del_file(fid: str, uid: str = Depends(user)):
    r = db.q("select path from files where id=? and user_id=?", (fid, uid), True)
    if not r:
        raise HTTPException(404, "Not found")
    Path(r["path"]).unlink(missing_ok=True)
    db.x("delete from facts where source_file_id=? and user_id=? and verified=0", (fid, uid))
    db.x("update facts set source_file_id=NULL where source_file_id=? and user_id=?", (fid, uid))
    db.x("delete from files where id=? and user_id=?", (fid, uid))
    audit(uid, "file_delete", fid)
    return {"deleted": True}


# ---------- profile ----------
class FactIn(BaseModel):
    kind: Optional[str] = None
    data: Optional[dict] = None
    verified: Optional[bool] = None
    priority: Optional[int] = None


@api.get("/profile")
def profile(uid: str = Depends(user)):
    out = db.facts_of(uid)
    for f in out:
        if f["kind"] == "contact":
            f["data"] = lk.normalize_contact(f["data"])[0]  # older data: split links into LinkedIn / GitHub / Portfolio / others
    return out


class TextIn(BaseModel):
    text: str


@api.get("/profile/analysis")
def profile_analysis(uid: str = Depends(user)):
    return review.analyze_profile(db.facts_of(uid))


@api.post("/profile/text")
def add_text(b: TextIn, uid: str = Depends(user)):
    if not 20 <= len(b.text.strip()) <= 20000:
        raise HTTPException(400, "Paste between 20 and 20000 characters")
    p = ingest.parse(b.text)
    if not p["entries"] and not p["skills"]:  # no headings: treat the whole paste as one detail block
        p["entries"] = [ingest.heuristic("X\nProjects\n" + b.text)["entries"][0]] if len(b.text.split("\n")) else []
    return {"facts_added": save_parsed(uid, None, p)}


@api.post("/profile/facts")
def add_fact(b: FactIn, uid: str = Depends(user)):
    if b.kind not in {"contact", "summary", "skills", "job", "internship", "project", "education", "certification", "achievement", "extra", "custom"}:
        raise HTTPException(400, "Unknown kind")
    data = b.data or {}
    if b.kind == "contact":
        data, errs = lk.normalize_contact(data, strict=True)
        if errs:
            raise HTTPException(400, "; ".join(errs))
    return {"id": db.add_fact(uid, b.kind, data, 1.0, None, 1)}


@api.patch("/profile/facts/{fid}")
def patch_fact(fid: str, b: FactIn, uid: str = Depends(user)):
    r = db.q("select * from facts where id=? and user_id=?", (fid, uid), True)
    if not r:
        raise HTTPException(404, "Not found")
    data = json.dumps(b.data) if b.data is not None else r["data"]
    if b.data is not None:
        b.data.pop("conflict", None)
        if (b.kind or r["kind"]) == "contact":
            b.data, errs = lk.normalize_contact(b.data, strict=True)
            if errs:
                raise HTTPException(400, "; ".join(errs))
        data = json.dumps(b.data)
    ver = int(b.verified) if b.verified is not None else (1 if b.data is not None else r["verified"])
    kinds = {"contact", "summary", "skills", "job", "internship", "project", "education", "certification", "achievement", "extra", "custom"}
    kind = b.kind if b.kind in kinds else r["kind"]
    db.x("update facts set kind=?, data=?, verified=?, priority=?, updated_at=? where id=? and user_id=?",
         (kind, data, ver, b.priority if b.priority is not None else r["priority"], time.time(), fid, uid))
    return db.fact_row(db.q("select * from facts where id=?", (fid,), True))


@api.delete("/profile/facts/{fid}")
def del_fact(fid: str, uid: str = Depends(user)):
    db.x("delete from facts where id=? and user_id=?", (fid, uid))
    return {"deleted": True}


# ---------- job description + tailoring ----------
class JD(BaseModel):
    text: str


class DraftIn(BaseModel):
    job_id: str
    template: str = "classic"
    font_size: str = "auto"  # "auto" (fill the page), "ref" (template's own size) or 8–14 (pt)
    link_style: str = "url"  # "url" = show web addresses (most ATS-safe), "label" = LinkedIn | GitHub | ...


class VersionIn(BaseModel):
    resume_json: dict


class ExportIn(BaseModel):
    format: str


def cfg_for(uid, tid):
    if tid in TEMPLATES:
        return {**TEMPLATES[tid]}
    r = db.q("select path,style_json from templates where id=? and user_id=?", (tid, uid), True)
    if r and Path(r["path"]).exists():
        st = json.loads(r["style_json"])
        return {**st, "base": r["path"]} if st.get("source") == "docx" else st
    return {**TEMPLATES["classic"]}


def _req(uid, jid):
    j = db.q("select req_json from jobs where id=? and user_id=?", (jid, uid), True)
    return json.loads(j["req_json"]) if j else None


class SkillsIn(BaseModel):
    skills: list[str]


@api.post("/profile/skills")
def add_skills(b: SkillsIn, uid: str = Depends(user)):
    new = [x.strip() for x in b.skills if 0 < len(x.strip()) <= 40][:20]
    f = next((f for f in db.facts_of(uid) if f["kind"] == "skills"), None)
    if f:
        merged = list(dict.fromkeys(f["data"].get("skills", []) + new))
        db.x("update facts set data=?, verified=1, updated_at=? where id=? and user_id=?", (json.dumps({"skills": merged}), time.time(), f["id"], uid))
    else:
        db.add_fact(uid, "skills", {"skills": new}, 1.0, None, 1)
    return {"added": new}


@api.post("/job-descriptions")
def add_jd(b: JD, uid: str = Depends(user)):
    if len(b.text.strip()) < 30 or len(b.text) > 20000:
        raise HTTPException(400, "Job description must be 30–20000 characters")
    req = match.analyze_jd(b.text)
    jid = db.uid("jd")
    db.x("insert into jobs values(?,?,?,?,?)", (jid, uid, b.text, json.dumps(req), time.time()))
    facts = db.facts_of(uid)
    return {"id": jid, "requirements": req, "ranking": match.rank(facts, req), "profile_gaps": draft.profile_gaps(facts, req)}


@api.post("/resume-drafts")
def make_draft(b: DraftIn, uid: str = Depends(user)):
    if b.template not in TEMPLATES and not db.q("select 1 from templates where id=? and user_id=?", (b.template, uid), True):
        raise HTTPException(400, "Unknown template")
    req = _req(uid, b.job_id)
    if not req:
        raise HTTPException(404, "Job description not found")
    facts = db.facts_of(uid)
    cfg = cfg_for(uid, b.template)
    cfg["link_style"] = "label" if b.link_style == "label" else "url"
    fs = norm_font_size(b.font_size)
    resume, notes = draft.build_draft(facts, req, {**cfg, "size": float(fs)} if fs not in ("auto", "ref") else cfg, b.template)
    resume["font_size"] = fs
    warns = notes + draft.validate(resume, facts)
    vid = db.uid("ver")
    db.x("insert into versions values(?,?,?,?,?,?,?,?)", (vid, uid, b.job_id, b.template, json.dumps(resume), json.dumps(warns), "draft", time.time()))
    audit(uid, "draft_created", vid)
    return {"id": vid, "job_id": b.job_id, "template": b.template, "resume": resume, "warnings": warns, "report": draft.analyze(resume, facts, req, cfg_for(uid, b.template))}


def _ver(vid, uid):
    r = db.q("select * from versions where id=? and user_id=?", (vid, uid), True)
    if not r:
        raise HTTPException(404, "Not found")
    return r


@api.get("/resume-versions")
def versions(uid: str = Depends(user)):
    return [dict(r) for r in db.q("select id,job_id,template,status,created_at from versions where user_id=? order by created_at desc", (uid,))]


@api.get("/resume-versions/{vid}")
def get_ver(vid: str, uid: str = Depends(user)):
    r = _ver(vid, uid)
    res, req = json.loads(r["resume_json"]), _req(uid, r["job_id"])
    return {"id": vid, "job_id": r["job_id"], "template": r["template"], "resume": res, "warnings": json.loads(r["warnings_json"]),
            "report": draft.analyze(res, db.facts_of(uid), req, cfg_for(uid, r["template"])) if req else None}


def sanitize_links(resume):
    """Keep only valid web addresses in the resume's links; report what was dropped."""
    notes = []

    def keep(lst):
        out = []
        for x in lst or []:
            if lk.valid_url(x):
                out.append(lk.canon(x))
            elif x and str(x).strip():
                notes.append({"type": "link", "message": f"Link “{x}” was ignored — not a valid web address."})
        return lk.dedupe(out)
    c = resume.get("contact") or {}
    c["links"] = keep(c.get("links"))
    resume["contact"] = c
    for sec in ("experience", "projects", "education", "certifications"):
        for it in resume.get(sec, []):
            it["links"] = keep(it.get("links"))
    for cu in resume.get("custom", []):
        for it in cu.get("entries", []):
            it["links"] = keep(it.get("links"))
    if resume.get("link_style") not in ("url", "label"):
        resume["link_style"] = "url"
    resume["font_size"] = norm_font_size(resume.get("font_size"))
    return notes


class CheckIn(BaseModel):
    urls: list[str]


@api.post("/links/check")
def check_links(b: CheckIn, uid: str = Depends(user)):
    if not lk.rate_ok(uid):
        raise HTTPException(429, "Too many link checks — wait a minute")
    return {"results": lk.check_links(b.urls)}


@api.patch("/resume-versions/{vid}")
def patch_ver(vid: str, b: VersionIn, uid: str = Depends(user)):
    r = _ver(vid, uid)
    facts, req = db.facts_of(uid), _req(uid, r["job_id"])
    link_notes = sanitize_links(b.resume_json)
    warns = link_notes + draft.validate(b.resume_json, facts)
    db.x("update versions set resume_json=?, warnings_json=?, status='edited' where id=? and user_id=?", (json.dumps(b.resume_json), json.dumps(warns), vid, uid))
    return {"warnings": warns, "report": draft.analyze(b.resume_json, facts, req, cfg_for(uid, r["template"])) if req else None}


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@api.get("/resume-versions/{vid}/docx")
def ver_docx(vid: str, uid: str = Depends(user)):
    r = _ver(vid, uid)
    return Response(to_docx(json.loads(r["resume_json"]), cfg_for(uid, r["template"])), media_type=DOCX_MIME)


@api.post("/resume-versions/{vid}/exports")
def export(vid: str, b: ExportIn, uid: str = Depends(user)):
    r = _ver(vid, uid)
    if b.format not in ("docx", "pdf"):
        raise HTTPException(400, "format must be docx or pdf")
    blob = (to_docx if b.format == "docx" else to_pdf)(json.loads(r["resume_json"]), cfg_for(uid, r["template"]))
    eid = db.uid("exp")
    d = Path(db.STORAGE) / uid
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{eid}.{b.format}"
    path.write_bytes(blob)
    db.x("insert into exports values(?,?,?,?,?,?)", (eid, uid, vid, b.format, str(path), time.time()))
    db.x("update versions set status='approved' where id=? and user_id=?", (vid, uid))
    audit(uid, "export", eid)
    return {"id": eid}


@api.get("/exports/{eid}")
def get_export(eid: str, uid: str = Depends(user)):
    r = db.q("select * from exports where id=? and user_id=?", (eid, uid), True)
    if not r:
        raise HTTPException(404, "Not found")
    return FileResponse(r["path"], filename=f"resume.{r['fmt']}")


@api.delete("/account")
def del_account(uid: str = Depends(user)):
    shutil.rmtree(Path(db.STORAGE) / uid, ignore_errors=True)
    for t in ("sessions", "files", "facts", "jobs", "versions", "exports", "templates"):
        db.x(f"delete from {t} where user_id=?", (uid,))
    db.x("delete from users where id=?", (uid,))
    db.x("delete from audit where user_id=?", (uid,))
    return {"deleted": True}


app.include_router(api)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
