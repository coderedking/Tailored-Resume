"""Render ONE canonical resume JSON into ATS-friendly DOCX and PDF. A template (built-in, or style extracted from the
user's reference resume: DOCX / PDF / image) controls typography only, never content or structure."""
import html, io, json, re
from collections import Counter
from docx import Document
from copy import deepcopy
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from docx.text.paragraph import Paragraph as DocPara
from docx.text.run import Run
from .ingest import HEAD, classify_heading, docx_cue, docx_is_bullet
from . import links as lk

BASE = dict(font="Calibri", size=10.5, margin=0.8, name_size=17.5, name_align=1, name_color="000000", head_size=None,
            head_color="000000", head_caps=True, head_border=True, max_pages=2)
TEMPLATES = {
    "classic": {**BASE, "label": "Classic — clean single column"},
    "compact": {**BASE, "label": "Compact — dense, fits more", "size": 9.5, "margin": 0.6, "name_size": 16.5},
    "modern": {**BASE, "label": "Modern — accent headings", "head_color": "2B59D9", "head_border": True, "name_align": 0, "name_color": "1B2A4A"},
}
ATS_FONTS = {"calibri", "arial", "times new roman", "cambria", "georgia", "garamond", "helvetica", "verdana", "tahoma",
             "trebuchet ms", "book antiqua", "century gothic", "calibri light", "arial narrow", "segoe ui", "palatino linotype"}
MON3 = {m: m.title() for m in "jan feb mar apr may jun jul aug sep oct nov dec".split()}


def ats_font(f):
    return f if (f or "").lower() in ATS_FONTS else "Calibri"


def nd(s):
    """Normalise a date to 'Mon YYYY' / 'YYYY' / 'Present' for consistent ATS parsing."""
    s = (s or "").strip()
    if s.lower() in ("present", "current", "ongoing", "till date", "now"):
        return "Present"
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
    return f"{MON3[m.group(1)[:3].lower()]} {m.group(2)}" if m and m.group(1)[:3].lower() in MON3 else s


def _dates(i):
    s, e = nd(i.get("start_date", "")), nd(i.get("end_date", ""))
    return f"{s} – {e}" if s and e else (s or e)


LINK_COLOR = "0563C1"
URL_RX = (r"(?:https?://|www\.)[^\s|,;()<>]+"
          r"|(?:[\w-]+\.)?(?:linkedin\.com|github\.com|gitlab\.com|bitbucket\.org|behance\.net|dribbble\.com|medium\.com|kaggle\.com|leetcode\.com|"
          r"codeforces\.com|hackerrank\.com|huggingface\.co|dev\.to|twitter\.com|x\.com|youtube\.com|stackoverflow\.com)/[^\s|,;()<>]+"
          r"|[\w.+-]+@[\w-]+\.[\w.-]*\w")


def _disp(link):
    return re.sub(r"^(https?://)?(www\.)?", "", (link or "").strip()).rstrip("/")


def _href(t):
    if "@" in t and not t.lower().startswith(("http", "www")):
        return "mailto:" + t
    return t if t.lower().startswith(("http://", "https://", "mailto:", "tel:")) else "https://" + t


def _show(l, r):
    """Visible link text: the web address (default, most ATS-safe) or a label such as LinkedIn / GitHub / GitHub: repo-name."""
    return lk.label(l, r.get("contact", {}).get("portfolio", "")) if r.get("link_style") == "label" else _disp(l)


def _entry_links(r):
    groups = [r.get(x, []) for x in ("experience", "projects", "education", "certifications")] + [c2["entries"] for c2 in r.get("custom", [])]
    return {lk.canon(l) for items in groups for i in items for l in i.get("links", [])}


def header(r):
    c, skip = r.get("contact", {}), _entry_links(r)  # a link shown on a project is not repeated in the contact line
    shown = [_show(l, r) for l in c.get("links", []) if lk.canon(l) not in skip]
    return c.get("name", ""), " | ".join(x for x in [c.get("email"), c.get("phone"), *shown] if x)


def _known(r):
    """(known, label_keys): visible text -> target URL. label_keys are label texts (LinkedIn, GitHub...) that may only be
    linked inside the generated contact line / link lines, never inside ordinary sentences."""
    c, k, lab = r.get("contact", {}), {}, set()
    for l in c.get("links", []):
        k[_show(l, r)] = _href(l.strip())
    if r.get("link_style") == "label":
        lab = set(k)
    if c.get("email"):
        k[c["email"]] = "mailto:" + c["email"]
    if c.get("phone"):
        k[c["phone"]] = "tel:" + re.sub(r"[^\d+]", "", c["phone"])
    groups = [r.get(x, []) for x in ("experience", "projects", "education", "certifications")] + [c2["entries"] for c2 in r.get("custom", [])]
    for items in groups:
        for i in items:
            for l in i.get("links", []):
                k[_show(l, r)] = _href(l.strip())
                if r.get("link_style") == "label":
                    lab.add(_show(l, r))
    return k, lab


def _rx(known):
    return re.compile("|".join([re.escape(x) for x in sorted(known, key=len, reverse=True)] + [URL_RX]), re.I)


def _pdf_markup(text, known):
    out, pos = [], 0
    for m in _rx(known).finditer(text):
        t = m.group(0).rstrip(".,;)")
        out.append(html.escape(text[pos:m.start()]))
        out.append(f'<a href="{html.escape(known.get(t) or _href(t), quote=True)}" color="#{LINK_COLOR}"><u>{html.escape(t)}</u></a>')
        pos = m.start() + len(t)
    return "".join(out) + html.escape(text[pos:])


def _txt_run(src, text):
    nr = OxmlElement("w:r")
    rp = src.find(qn("w:rPr"))
    if rp is not None:
        nr.append(deepcopy(rp))
    t = OxmlElement("w:t"); t.text = text; t.set(qn("xml:space"), "preserve")
    nr.append(t)
    return nr


def _hyperlink(src, text, url, part):
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), part.relate_to(url, RT.HYPERLINK, is_external=True)); h.set(qn("w:history"), "1")
    nr = _txt_run(src, text)
    rn = Run(nr, None)
    rn.font.color.rgb = RGBColor.from_string(LINK_COLOR); rn.font.underline = True
    h.append(nr)
    return h


def _linkify(d, r):
    """Turn contact links, e-mail, phone and any URL in the text into real clickable Word hyperlinks (visible text is kept for ATS)."""
    known, lab = _known(r)
    rx_all = _rx(known)
    rx_url = _rx({k: v for k, v in known.items() if k not in lab})
    gen = {header(r)[1]} | {it[5] for _, _, items in blocks(r) for it in items if it[0] == "entry" and it[5]}
    for p in d.paragraphs:
        rx = rx_all if p.text.strip() in gen else rx_url
        for run in list(p._p.findall(qn("w:r"))):
            if run.find(qn("w:tab")) is not None or run.find(qn("w:br")) is not None:
                continue
            t = "".join(x.text or "" for x in run.findall(qn("w:t")))
            if not t or not rx.search(t):
                continue
            pos, new = 0, []
            for m in rx.finditer(t):
                link = m.group(0).rstrip(".,;)")
                if m.start() > pos:
                    new.append(_txt_run(run, t[pos:m.start()]))
                new.append(_hyperlink(run, link, known.get(link) or _href(link), d.part))
                pos = m.start() + len(link)
            if pos < len(t):
                new.append(_txt_run(run, t[pos:]))
            for el in new:
                run.addprevious(el)
            p._p.remove(run)


DEFAULT_HEADS = {"summary": "Professional Summary", "skills": "Technical Skills", "experience": "Work Experience", "projects": "Projects",
                 "education": "Education", "certifications": "Certifications", "achievements": "Achievements", "additional": "Additional Information"}
REF2KEY = {"summary": "summary", "skills": "skills", "job": "experience", "internship": "experience", "project": "projects", "education": "education",
           "certification": "certifications", "achievement": "achievements", "extra": "additional"}
KEY2REF = {"summary": ["summary"], "skills": ["skills"], "experience": ["job", "internship"], "projects": ["project"], "education": ["education"],
           "certifications": ["certification"], "achievements": ["achievement"], "additional": ["extra"], "custom": ["custom", "achievement"]}
STD_ORDER = ["summary", "skills", "experience", "projects", "education", "certifications", "achievements", "custom", "additional"]


def _tech(i, r):
    t = ", ".join(i.get("technologies", []))
    return " | ".join(x for x in (("Technologies: " + t) if t else "", " | ".join(_show(l, r) for l in i.get("links", []))) if x)


def _ent(items, r):
    return [("entry", i.get("title", ""), i.get("organization", ""), _dates(i), i.get("bullets", []), _tech(i, r)) for i in items]


def blocks(r, cfg=None):
    """[(key, heading, items)] in the reference's section order/wording when known. Entry = ('entry', title, org, dates, bullets, tech)."""
    sec = {}
    if r.get("summary"):
        sec["summary"] = [("line", r["summary"])]
    if r.get("skills"):
        g = {}
        for s in r["skills"]:
            g.setdefault(s.get("group", "Skills"), []).append(s["name"])
        sec["skills"] = [("line", f"{k}: {', '.join(v)}") for k, v in g.items()]
    for k in ("experience", "projects", "education", "certifications"):
        if r.get(k):
            sec[k] = _ent(r[k], r)
    if r.get("achievements"):
        sec["achievements"] = [("entry", "", "", "", r["achievements"], "")]
    for c in r.get("custom", []):
        if c.get("entries"):
            sec["custom:" + c["section"]] = _ent(c["entries"], r)
    if r.get("additional"):
        sec["additional"] = [("entry", "", "", "", r["additional"], "")]
    heads, order = dict(DEFAULT_HEADS), []
    for x in (cfg or {}).get("sections", []):
        k = REF2KEY.get(x["kind"])
        if k and k not in order:
            order.append(k); heads[k] = x["text"]
    keys = []
    for k in order + [k for k in STD_ORDER if k not in order]:
        keys += [x for x in sec if x.startswith("custom:")] if k == "custom" else [k]
    return [(k, heads.get(k, k.split(":", 1)[-1]), sec[k]) for k in keys if sec.get(k)]


# ---------------- reference style extraction (DOCX / PDF / image) ----------------
def _clamp(v, lo, hi):
    return round(min(max(v, lo), hi), 1)


def _color(run, para=None):
    for f in (run.font, getattr(getattr(para, "style", None), "font", None)):
        try:
            if f is not None and f.color is not None and f.color.rgb is not None:
                return str(f.color.rgb)
        except Exception:
            pass
    return None


def _style_docx(path):
    d = Document(path)
    st = dict(BASE, label="Your reference resume", source="docx")
    s = d.sections[0]
    if s.left_margin:
        st["margin"] = _clamp(s.left_margin / 914400, 0.4, 1.5)
    n = d.styles["Normal"]
    if n.font.name:
        st["font"] = n.font.name
    if n.font.size:
        st["size"] = _clamp(n.font.size.pt, 8, 12.5)
    paras = [p for p in d.paragraphs if p.text.strip()]
    run0 = lambda p: next((r for r in p.runs if r.text.strip()), None)
    if paras:
        p = paras[0]
        st["name_align"] = 1 if p.alignment == WD_ALIGN_PARAGRAPH.CENTER else 0
        r = run0(p)
        if r:
            if r.font.size:
                st["name_size"] = _clamp(r.font.size.pt, 12, 28)
            st["name_color"] = _color(r, p) or "000000"
            if r.font.name and not n.font.name:
                st["font"] = r.font.name
        if not n.font.size:
            for q in paras[2:]:
                rr = run0(q)
                if rr and rr.font.size:
                    st["size"] = _clamp(rr.font.size.pt, 8, 12.5)
                    break
    for p in paras:
        if p.text.strip().strip(":").lower() in HEAD:
            r = run0(p)
            st["head_caps"] = p.text.strip().isupper()
            st["head_border"] = "w:pBdr" in p._p.xml
            if r:
                st["head_color"] = _color(r, p) or "000000"
                if r.font.size:
                    st["head_size"] = _clamp(r.font.size.pt, 9, 16)
            break
    try:
        m = _learn(d)
    except Exception:
        m = None
    if m:
        st["mimic"], st["sections"] = m, m["sections"]
    return st


def _learn(d):
    """Learn the reference's structure: heading wording/order and prototype paragraphs (name, contact, heading, entry, sub-line,
    bullet, body). Generation later clones these paragraphs so the new resume looks like the reference. Tables => not ATS-safe, skip."""
    if d.tables:
        return None
    paras = d.paragraphs
    idx = [i for i, p in enumerate(paras) if p.text.strip()]
    if len(idx) < 5:
        return None
    P, tabs, sections, cur, pos, headed = {}, set(), [], None, 0, False
    hastab = lambda p: bool(p._p.xpath("./w:pPr/w:tabs") or p._p.xpath(".//w:r/w:tab"))
    for n, i in enumerate(idx):
        p, t = paras[i], paras[i].text.strip()
        bullet = docx_is_bullet(p)
        k = None if (bullet or n == 0) else classify_heading(t, docx_cue(p))
        if not headed and not k:
            if n == 0:
                P["name"] = i
            elif "contact" not in P:
                P["contact"] = i
            continue
        if k:
            headed, cur, pos = True, k, 0
            sections.append({"kind": k, "text": t}); P.setdefault("heading", i)
            continue
        if bullet:
            P.setdefault(f"{cur}|bullet", i); P.setdefault("*|bullet", i); pos = -1
        elif cur in ("skills", "summary"):
            P.setdefault(f"{cur}|body", i); P.setdefault("*|body", i)
        else:
            pos = 1 if pos <= 0 else pos + 1
            role = "entry" if pos == 1 else "sub" if pos == 2 else "body"
            for key in (f"{cur}|{role}", f"*|{role}"):
                P.setdefault(key, i)
            if role == "entry" and hastab(p):
                tabs.update({f"{cur}|entry", "*|entry"})
    if "heading" not in P or not sections:
        return None
    return {"sections": sections, "protos": P, "tabs": sorted(tabs), "head_upper": all(x["text"].isupper() for x in sections)}


def _clean_font(f):
    f = re.sub(r"^[A-Z]{6}\+", "", str(f or ""))
    f = re.split(r"[-,]", f)[0]
    f = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", f)
    f = re.sub(r"\s*(PS)?MT$", "", f).strip()
    return {"Helvetica": "Arial", "Times": "Times New Roman", "Times Roman": "Times New Roman"}.get(f, f) or "Calibri"


def _lines_fitz(path):
    import fitz
    pg = fitz.open(path)[0]
    out = []
    for b in pg.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            sp = [s for s in l["spans"] if s["text"].strip()]
            if sp:
                s0 = max(sp, key=lambda s: len(s["text"]))
                out.append(dict(text="".join(s["text"] for s in sp).strip(), size=s0["size"], font=s0["font"], color="%06X" % s0["color"],
                                x0=l["bbox"][0], x1=l["bbox"][2], y0=l["bbox"][1], bold=bool(s0["flags"] & 16) or "bold" in s0["font"].lower()))
    rules = [d["rect"].y0 for d in pg.get_drawings() if d["rect"].height < 3 and d["rect"].width > 0.3 * pg.rect.width]
    return out, pg.rect.width, rules


def _lines_pypdf(path):
    from pypdf import PdfReader
    pg = PdfReader(path).pages[0]
    W, H = float(pg.mediabox.width), float(pg.mediabox.height)
    rows = {}

    def vis(t, cm, tm, fd, fs):
        if t.strip():
            sz = abs((fs or 10) * (tm[0] or 1) * (cm[0] or 1))
            x, y = tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5]
            rows.setdefault(round(y / 2), []).append((x, sz, t, str((fd or {}).get("/BaseFont", ""))))
    pg.extract_text(visitor_text=vis)
    out = []
    for k in sorted(rows, reverse=True):
        r = sorted(rows[k])
        sz = max(a[1] for a in r)
        out.append(dict(text=" ".join(a[2].strip() for a in r), size=sz, font=r[0][3], color="000000", x0=r[0][0],
                        x1=r[-1][0] + len(r[-1][2]) * r[-1][1] * 0.5, y0=H - k * 2))
    return out, W, []


def _style_pdf(path):
    try:
        lines, W, rules = _lines_fitz(path)
    except Exception:
        lines, W, rules = _lines_pypdf(path)
    st = dict(BASE, label="Your reference resume", source="pdf")
    lines = sorted([l for l in lines if l["text"]], key=lambda l: l["y0"])
    if not lines:
        return st
    f = lines[0]
    st["name_size"] = _clamp(f["size"], 12, 28)
    st["name_align"] = 1 if abs((f["x0"] + f["x1"]) / 2 - W / 2) < 0.07 * W and f["x1"] - f["x0"] < 0.85 * W else 0
    st["name_color"] = f["color"]
    body = lines[2:] or lines
    sizes = Counter()
    fonts = Counter()
    for l in body:
        sizes[round(l["size"], 1)] += len(l["text"]); fonts[l["font"]] += len(l["text"])
    st["size"] = _clamp(sizes.most_common(1)[0][0], 8, 12.5)
    st["font"] = _clean_font(fonts.most_common(1)[0][0])
    st["margin"] = _clamp(min(l["x0"] for l in lines) / 72, 0.4, 1.5)
    secs = []
    for l in lines:
        k = classify_heading(l["text"], 1 if l.get("bold") else 0)
        if k and k != "custom":
            secs.append({"kind": k, "text": l["text"]})
    if secs:
        st["sections"] = secs
    for l in lines:
        if l["text"].strip(":").lower() in HEAD:
            st["head_caps"] = l["text"].isupper()
            st["head_size"] = _clamp(l["size"], 9, 16)
            st["head_color"] = l["color"]
            st["head_border"] = any(l["y0"] - 2 <= y <= l["y0"] + l["size"] * 2.5 for y in rules)
            break
    return st


def _style_image(path):
    """No OCR needed: estimate layout from ink geometry (name alignment/size, body size, margin, accent colour, rules)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    H = 1100
    W = max(300, int(im.size[0] * H / im.size[1]))
    im = im.resize((W, H))
    rgb = list(im.getdata())
    gray = [(r * 3 + g * 6 + b) // 10 for r, g, b in rgb]
    bands, cur, sat, nsat = [], None, Counter(), Counter()
    for y in range(H):
        xs = [x for x in range(W) if gray[y * W + x] < 160]
        if xs:
            if cur and y - cur["y1"] <= 2:
                cur["y1"] = y; cur["x0"] = min(cur["x0"], xs[0]); cur["x1"] = max(cur["x1"], xs[-1]); cur["ink"] += len(xs)
            else:
                cur = dict(y0=y, y1=y, x0=xs[0], x1=xs[-1], ink=len(xs)); bands.append(cur)
        for r, g, b in rgb[y * W:(y + 1) * W]:
            if max(r, g, b) - min(r, g, b) > 60 and max(r, g, b) < 235:
                k = (r // 32, g // 32, b // 32)
                sat[k] += 1
                if len(bands) == 1:
                    nsat[k] += 1
    for b in bands:
        b["h"] = b["y1"] - b["y0"] + 1
    rules = [b for b in bands if b["h"] <= 4 and b["x1"] - b["x0"] > 0.45 * W and b["ink"] > 0.8 * b["h"] * (b["x1"] - b["x0"])]
    text = [b for b in bands if b["h"] >= 5 and b not in rules]
    st = dict(BASE, label="Your reference resume", source="image")
    if not text:
        return st
    hx = lambda c: "%02X%02X%02X" % tuple(min(255, v * 32 + 16) for v in c.most_common(1)[0][0]) if c else "000000"
    n, k = text[0], 0.78
    st["name_size"] = _clamp(n["h"] * k, 12, 28)
    st["name_align"] = 1 if abs((n["x0"] + n["x1"]) / 2 - W / 2) < 0.07 * W and n["x1"] - n["x0"] < 0.85 * W else 0
    hs = sorted(b["h"] for b in text[2:]) or [n["h"]]
    st["size"] = _clamp(hs[len(hs) // 2] * k, 8, 12.5)
    st["margin"] = _clamp(min(b["x0"] for b in text) / W * 8.5, 0.4, 1.5)
    st["head_border"] = bool(rules)
    st["name_color"] = hx(nsat)
    st["head_color"] = hx(sat)
    return st


def extract_style(path, ext=".docx"):
    ext = ext.lower()
    if ext == ".docx":
        return _style_docx(path)
    if ext == ".pdf":
        return _style_pdf(path)
    return _style_image(path)


# ---------------- DOCX ----------------
def _border(p, color):
    pPr = p._p.get_or_add_pPr()
    b, bt = OxmlElement("w:pBdr"), OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", color)):
        bt.set(qn(k), v)
    b.append(bt)
    pPr.append(b)


def _strip_headers(d):
    for s in d.sections:  # ATS parsers often skip headers/footers, so nothing important may live there
        for hf in (s.header, s.footer, s.first_page_header, s.first_page_footer, s.even_page_header, s.even_page_footer):
            try:
                if not hf.is_linked_to_previous:
                    for el in list(hf._element):
                        hf._element.remove(el)
                    hf._element.append(OxmlElement("w:p"))
            except Exception:
                pass


FONT_SIZES = [9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 14]
AUTO_SIZES = [9, 9.5, 10, 10.5, 11, 11.5, 12]  # auto-fit never goes above 12 pt body text
_FIT = {}


def norm_font_size(v):
    """'auto' (fill the page), 'ref' (keep the template's own sizes) or a number between 8 and 14 (as text)."""
    if v in ("auto", "ref"):
        return v
    try:
        return str(min(max(round(float(v) * 2) / 2, 8), 14)).rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "auto"


def _scaled(cfg, size, space=1.0):
    k = size / cfg["size"]
    return {**cfg, "size": size, "name_size": round(cfg["name_size"] * k, 1), "head_size": round(cfg["head_size"] * k, 1) if cfg.get("head_size") else None,
            "space": space, "scale": k, "_resolved": True, "_override": True}


def resolve_cfg(r, cfg):
    """Applies the resume's font-size choice. 'auto' = the largest text size and spacing that keeps the same number of
    pages (so the page is used instead of leaving a blank bottom); 'ref' = template's own sizes; number = that body size."""
    if cfg.get("_resolved"):
        return cfg
    fs = norm_font_size(r.get("font_size", "auto"))
    if fs == "ref":
        return {**cfg, "scale": 1.0, "space": 1.0, "_resolved": True}
    if fs != "auto":
        return _scaled(cfg, float(fs))
    key = json.dumps([r, cfg], sort_keys=True, default=str)
    if key in _FIT:
        return _FIT[key]
    base = cfg["size"]
    pages = lambda c: _pdf(r, c)[1]
    p0 = max(1, pages({**cfg, "space": 1.0, "scale": 1.0, "_resolved": True}))
    size = base
    for sz in sorted([x for x in AUTO_SIZES if x > base], reverse=True):
        if pages(_scaled(cfg, sz)) <= p0:
            size = sz
            break
    space = next((sp for sp in (1.5, 1.35, 1.2, 1.1) if pages(_scaled(cfg, size, sp)) <= p0), 1.0)
    if len(_FIT) > 64:
        _FIT.clear()
    _FIT[key] = _scaled(cfg, size, space)
    return _FIT[key]


def _apply_override(made, d, cfg):
    """Reference-DOCX mode: give the new resume ONE regular set of sizes instead of the reference's mixed ones."""
    size, sp = cfg["size"], cfg.get("space", 1.0)
    d.styles["Normal"].font.size = Pt(size)
    sizes = {"name": cfg["name_size"], "head": cfg.get("head_size") or size + 1, "tech": size - 0.5}
    for p, role in made:
        for rn in p.runs:
            rn.font.size = Pt(sizes.get(role, size))
        pf = p.paragraph_format
        for a in ("space_before", "space_after"):
            v = getattr(pf, a)
            if v is not None:
                setattr(pf, a, Pt(v.pt * sp))
        if isinstance(pf.line_spacing, float):
            pf.line_spacing = round(pf.line_spacing * (1 + (sp - 1) * 0.5), 2)


def _mimic(r, cfg):
    """Clone the reference resume's own paragraphs (fonts, numbering, tabs, borders, spacing) and pour the new content in."""
    d = Document(cfg["base"])
    m, P = cfg["mimic"], cfg["mimic"]["protos"]
    paras = d.paragraphs
    snap = {k: deepcopy(paras[i]._p) for k, i in P.items() if i < len(paras)}
    body = d.element.body
    for el in list(body):
        if el.tag != qn("w:sectPr"):
            body.remove(el)
    _strip_headers(d)
    sect = body.find(qn("w:sectPr"))
    pre = {}
    for k, el in snap.items():
        if k.endswith("|bullet"):
            mm = re.match(r"^[•▪●◦·\-–*]\s*", "".join(t.text or "" for t in el.iter(qn("w:t"))).strip())
            pre[k] = mm.group(0) if mm else ""
    drop = {qn(x) for x in ("w:r", "w:hyperlink", "w:ins", "w:del", "w:bookmarkStart", "w:bookmarkEnd", "w:proofErr")}

    made = []

    def add(proto, parts, role="body"):
        if proto is None:
            p = d.add_paragraph()
            for t, _, b in parts:
                rn = p.add_run(t)
                if b is not None:
                    rn.bold = b
            made.append((p, role))
            return p
        el = deepcopy(proto)
        rprs = [deepcopy(x.find(qn("w:rPr"))) if x.find(qn("w:rPr")) is not None else None for x in el.iter(qn("w:r"))
                if "".join(t.text or "" for t in x.iter(qn("w:t"))).strip()]
        for ch in list(el):
            if ch.tag in drop:
                el.remove(ch)
        sect.addprevious(el) if sect is not None else body.append(el)
        para = DocPara(el, d._body)
        for t, slot, b in parts:
            rn = para.add_run()
            if rprs and rprs[slot] is not None:
                rn._r.insert(0, deepcopy(rprs[slot]))
            rn.text = t
            if b is not None:
                rn.bold = b
        made.append((para, role))
        return para

    def look(key, role):
        for k in KEY2REF.get(key.split(":")[0], []) + ["*"]:
            if f"{k}|{role}" in snap:
                return k, snap[f"{k}|{role}"]
        return None, None
    name, contact = header(r)
    pn = add(snap.get("name"), [(name, 0, None)], "name")
    if contact:
        if "contact" in snap:
            add(snap["contact"], [(contact, 0, None)], "contact")
        else:
            pc = add(snap.get("*|body"), [(contact, 0, False)], "contact"); pc.alignment = pn.alignment
    for key, head, items in blocks(r, cfg):
        add(snap["heading"], [(head.upper() if m.get("head_upper") else head, 0, None)], "head")
        for it in items:
            if it[0] == "line":
                k, bp = look(key, "body")
                lab = it[1].split(": ", 1)
                if bp is None:
                    k, bp = look(key, "bullet")
                    add(bp, [((pre.get(f"{k}|bullet", "") if k else "") + it[1], 0, None)])
                elif key == "skills" and len(lab) == 2:
                    add(bp, [(lab[0] + ": ", 0, True), (lab[1], -1, False)])
                else:
                    add(bp, [(it[1], 0, None)])
                continue
            _, title, org, dates, bullets, tech = it
            if title or org or dates:
                ke, ep = look(key, "entry")
                _, sp = look(key, "sub") if ke and f"{ke}|sub" in snap else (None, None)
                tab = ke and f"{ke}|entry" in m["tabs"]
                parts = [(title, 0, None)]
                if sp is None:
                    if org:
                        parts.append((" | " + org if title else org, -1, False))
                    if dates:
                        parts.append(("\t" + dates if tab else " | " + dates, -1, False))
                    add(ep, parts)
                else:
                    if dates and tab:
                        parts.append(("\t" + dates, -1, False))
                    add(ep, parts)
                    add(sp, [(" | ".join(x for x in (org, "" if tab else dates) if x), 0, None)])
            if tech:
                kb, bp = look(key, "body")
                add(bp or snap.get("*|bullet"), [(tech, 0, False)], "tech").runs[-1].italic = True
            for b in bullets:
                kb, bp = look(key, "bullet")
                add(bp, [((pre.get(f"{kb}|bullet", "") if kb else "") + b, 0, None)]) if bp is not None else add(None, [("• " + b, 0, None)])
    if cfg.get("_override"):
        _apply_override(made, d, cfg)
    _linkify(d, r)
    buf = io.BytesIO(); d.save(buf)
    return buf.getvalue()


def to_docx(r, cfg):
    cfg = resolve_cfg(r, cfg)
    if cfg.get("base") and cfg.get("mimic"):
        try:
            return _mimic(r, cfg)
        except Exception:
            pass  # fall back to style-only rendering
    d = Document(cfg["base"]) if cfg.get("base") else Document()
    if cfg.get("base"):
        body = d.element.body
        for el in list(body):
            if el.tag != qn("w:sectPr"):
                body.remove(el)
        _strip_headers(d)
        sc = d.sections[0]
        width = (sc.page_width - sc.left_margin - sc.right_margin) / 914400
    else:
        for s in d.sections:
            s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = Inches(cfg["margin"])
        width = 8.5 - 2 * cfg["margin"]
    font = ats_font(cfg["font"])
    sp = cfg.get("space", 1.0)
    st = d.styles["Normal"]
    st.font.name, st.font.size = font, Pt(cfg["size"])
    st.paragraph_format.space_after = Pt(1 * sp)
    st.paragraph_format.line_spacing = round(1.0 + (sp - 1) * 0.5, 2)
    name, contact = header(r)
    d.core_properties.title = f"{name} — Resume".strip(" —")
    d.core_properties.author = name
    align = WD_ALIGN_PARAGRAPH.CENTER if cfg["name_align"] == 1 else WD_ALIGN_PARAGRAPH.LEFT
    p = d.add_paragraph(); p.alignment = align; p.paragraph_format.space_after = Pt(1)
    run = p.add_run(name); run.bold = True; run.font.size = Pt(cfg["name_size"]); run.font.color.rgb = RGBColor.from_string(cfg["name_color"].upper())
    p = d.add_paragraph(contact); p.alignment = align
    hs = cfg.get("head_size") or cfg["size"] + 1
    for key, head, items in blocks(r, cfg):
        p = d.add_paragraph()
        if cfg["head_border"]:
            _border(p, cfg["head_color"])
        p.paragraph_format.space_before = Pt(9 * sp); p.paragraph_format.space_after = Pt(2 * sp); p.paragraph_format.keep_with_next = True
        run = p.add_run(head.upper() if cfg["head_caps"] else head)
        run.bold = True; run.font.size = Pt(hs); run.font.color.rgb = RGBColor.from_string(cfg["head_color"].upper())
        for it in items:
            if it[0] == "line":
                q = d.add_paragraph(); lab = it[1].split(": ", 1)
                if key == "skills" and len(lab) == 2:
                    q.add_run(lab[0] + ": ").bold = True; q.add_run(lab[1])
                else:
                    q.add_run(it[1])
                continue
            _, title, org, dates, bullets, tech = it
            if title or org or dates:
                q = d.add_paragraph(); pf = q.paragraph_format
                pf.space_before = Pt(4 * sp); pf.space_after = Pt(0); pf.keep_with_next = True
                q.add_run(title).bold = True
                if org:
                    q.add_run((" | " if title else "") + org)
                if dates:
                    pf.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT); q.add_run("\t" + dates)
            if tech:
                q = d.add_paragraph(); q.paragraph_format.space_after = Pt(0)
                rr = q.add_run(tech); rr.italic = True; rr.font.size = Pt(cfg["size"] - 0.5)
            for b in bullets:
                try:
                    q = d.add_paragraph(b, style="List Bullet")
                except KeyError:
                    q = d.add_paragraph("• " + b)
                q.paragraph_format.left_indent = Inches(0.25); q.paragraph_format.first_line_indent = Inches(-0.18)
                q.paragraph_format.space_after = Pt((sp - 1) * 2)
    _linkify(d, r)
    buf = io.BytesIO(); d.save(buf)
    return buf.getvalue()


# ---------------- PDF (real selectable text) ----------------
def to_pdf(r, cfg):
    return _pdf(r, resolve_cfg(r, cfg))[0]


def _pdf(r, cfg):
    serif = any(k in ats_font(cfg["font"]).lower() for k in ("times", "georgia", "garamond", "cambria", "antiqua", "palatino"))
    f, fb = ("Times-Roman", "Times-Bold") if serif else ("Helvetica", "Helvetica-Bold")
    sz = cfg["size"]
    buf = io.BytesIO()
    m = cfg["margin"] * inch
    W = letter[0] - 2 * m - 12  # SimpleDocTemplate frames carry 6pt padding per side
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m,
                            title=(header(r)[0] or "Resume") + " — Resume", author=header(r)[0])
    sp = cfg.get("space", 1.0)
    base = ParagraphStyle("b", fontName=f, fontSize=sz, leading=sz * 1.25 * (1 + (sp - 1) * 0.5))
    al = 1 if cfg["name_align"] == 1 else 0
    big = ParagraphStyle("n", parent=base, fontName=fb, fontSize=cfg["name_size"], leading=cfg["name_size"] * 1.2, alignment=al,
                         textColor=colors.HexColor("#" + cfg["name_color"]))
    ctr = ParagraphStyle("c", parent=base, alignment=al)
    hd = ParagraphStyle("h", parent=base, fontName=fb, fontSize=cfg.get("head_size") or sz + 1, spaceBefore=9 * sp, spaceAfter=2 * sp,
                        textColor=colors.HexColor("#" + cfg["head_color"]), keepWithNext=1)
    bl = ParagraphStyle("bl", parent=base, leftIndent=14, bulletIndent=4, spaceAfter=(sp - 1) * 2)
    rt = ParagraphStyle("rt", parent=base, alignment=2)
    e = html.escape
    known, lab = _known(r)
    known_url = {k: v for k, v in known.items() if k not in lab}
    M = lambda t: _pdf_markup(t, known)  # contact line & link lines (may contain labels)
    MU = lambda t: _pdf_markup(t, known_url)  # ordinary sentences: only real URLs / e-mails
    name, contact = header(r)
    fl = [Paragraph(e(name), big), Paragraph(M(contact), ctr)]
    for key, head, items in blocks(r, cfg):
        fl.append(Paragraph(e(head.upper() if cfg["head_caps"] else head), hd))
        if cfg["head_border"]:
            fl.append(HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#" + cfg["head_color"]), spaceAfter=3))
        for it in items:
            if it[0] == "line":
                lab_ = it[1].split(": ", 1)
                fl.append(Paragraph(f"<font name='{fb}'>{e(lab_[0])}:</font> {e(lab_[1])}" if key == "skills" and len(lab_) == 2 else MU(it[1]), base))
                continue
            _, title, org, dates, bullets, tech = it
            if title or org or dates:
                left = Paragraph(f"<font name='{fb}'>{e(title)}</font>" + (f" | {e(org)}" if org else ""), base)
                if dates:
                    dw = max(1.5 * inch, stringWidth(dates, f, sz) + 8)  # date column grows with the text size so dates never wrap
                    t = Table([[left, Paragraph(e(dates), rt)]], colWidths=[W - dw, dw])
                    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
                    fl += [Spacer(1, 3 * sp), t]
                else:
                    fl += [Spacer(1, 3 * sp), left]
            if tech:
                fl.append(Paragraph(f"<i>{M(tech)}</i>", base))
            fl += [Paragraph(MU(b), bl, bulletText="•") for b in bullets]
    doc.build(fl)
    return buf.getvalue(), doc.page
