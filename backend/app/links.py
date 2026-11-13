"""Link handling: canonical form, validation, labels (LinkedIn / GitHub / ...), contact normalisation and a safe link checker."""
import ipaddress, os, re, socket, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import httpx

SITES = {"linkedin.com": "LinkedIn", "github.com": "GitHub", "gitlab.com": "GitLab", "kaggle.com": "Kaggle", "leetcode.com": "LeetCode",
         "medium.com": "Medium", "behance.net": "Behance", "dribbble.com": "Dribbble", "twitter.com": "X", "x.com": "X", "youtube.com": "YouTube",
         "huggingface.co": "Hugging Face", "stackoverflow.com": "Stack Overflow", "hackerrank.com": "HackerRank", "codeforces.com": "Codeforces", "dev.to": "DEV"}
ALLOW_PRIVATE = bool(os.getenv("LINKCHECK_ALLOW_PRIVATE"))  # only for automated tests


def canon(u):
    u = (u or "").strip().strip(",;|")
    if not u or ("@" in u and not re.match(r"^(https?://|www\.)", u, re.I)):
        return u
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    host = re.sub(r"^www\.", "", (p.hostname or "").lower())
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme.lower()}://{host}{port}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def host(u):
    return (urlparse(canon(u)).hostname or "").lower()


def valid_url(u):
    p = urlparse(canon(u))
    h = p.hostname or ""
    return p.scheme in ("http", "https") and (bool(re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}", h)) or (ALLOW_PRIVATE and bool(re.fullmatch(r"\d+(\.\d+){3}", h))))


def site(u):
    h = host(u)
    return next((v for d, v in SITES.items() if h == d or h.endswith("." + d)), "")


def segs(u):
    return [x for x in urlparse(canon(u)).path.split("/") if x]


def label(u, portfolio=""):
    """Short visible text for a link: LinkedIn, GitHub, Portfolio, 'GitHub: repo-name', 'domain.com/page'."""
    c = canon(u)
    if portfolio and c == canon(portfolio):
        return "Portfolio"
    s, sg = site(c), segs(c)
    if s:
        return s if len(sg) <= 1 or (s == "LinkedIn" and sg[0] == "in") else f"{s}: {sg[-1]}"
    return host(c) + (("/" + sg[-1]) if sg else "")


def dedupe(items):
    seen, out = set(), []
    for x in items:
        if x and canon(x).lower() not in seen:
            seen.add(canon(x).lower()); out.append(x)
    return out


def normalize_contact(d, strict=False):
    """Returns (clean_contact, errors). Stored form: name, email, phone, linkedin, github, portfolio, links (= other links only)."""
    d, errs = dict(d or {}), []
    out = {"name": str(d.get("name", "")).strip(), "email": str(d.get("email", "")).strip(), "phone": str(d.get("phone", "")).strip()}
    lab = {k: canon(d.get(k, "")) for k in ("linkedin", "github", "portfolio")}
    rest = [canon(x) for x in (d.get("links") or []) if str(x).strip()]
    for u in list(rest):  # derive missing labelled links from the generic list (also migrates older data)
        if not lab["linkedin"] and host(u) == "linkedin.com":
            lab["linkedin"] = u; rest.remove(u)
        elif not lab["github"] and host(u) == "github.com" and len(segs(u)) == 1:
            lab["github"] = u; rest.remove(u)
    if not lab["portfolio"]:
        for u in rest:
            if not site(u) and valid_url(u):
                lab["portfolio"] = u; rest.remove(u); break
    if strict:
        if out["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", out["email"]):
            errs.append("Email doesn't look valid (example: you@example.com)")
        digits = re.sub(r"\D", "", out["phone"])
        if out["phone"] and not 7 <= len(digits) <= 15:
            errs.append("Phone number should have 7–15 digits")
        if lab["linkedin"] and not (host(lab["linkedin"]) == "linkedin.com" and segs(lab["linkedin"])[:1] in (["in"], ["company"])):
            errs.append("LinkedIn should look like linkedin.com/in/your-name")
        if lab["github"] and not (host(lab["github"]) == "github.com" and len(segs(lab["github"])) == 1):
            errs.append("GitHub should be your profile, like github.com/your-username")
        if lab["portfolio"] and not valid_url(lab["portfolio"]):
            errs.append("Portfolio should be a web address like yourname.dev")
        errs += [f"“{u}” is not a valid web address" for u in rest if not valid_url(u)]
    out.update(lab)
    out["links"] = dedupe(rest)
    return out, errs


def all_links(c):
    return dedupe([c.get("linkedin", ""), c.get("github", ""), c.get("portfolio", ""), *c.get("links", [])])


# ---------------- link checking (server side, SSRF-guarded) ----------------
def _public(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if ALLOW_PRIVATE:
        return True
    try:
        for info in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80)):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
    except OSError:
        return True  # DNS failure is reported as 'broken' by the request itself
    return True


def check_one(url):
    u = (url or "").strip()
    if u.lower().startswith("mailto:"):
        ok = re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", u[7:])
        return {"url": u, "status": "ok" if ok else "invalid", "code": None, "detail": "Email format looks fine (not tested)" if ok else "Email format is invalid"}
    if u.lower().startswith("tel:"):
        return {"url": u, "status": "ok", "code": None, "detail": "Phone link (not tested)"}
    c = canon(u)
    if not valid_url(c):
        return {"url": u, "status": "invalid", "code": None, "detail": "Not a valid web address"}
    cur, code = c, None
    try:
        with httpx.Client(timeout=6, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (ResumeTailor link check)"}) as cl:
            for _ in range(5):
                if not _public(cur):
                    return {"url": u, "status": "invalid", "code": None, "detail": "Local/private addresses are not checked"}
                with cl.stream("GET", cur) as r:
                    code = r.status_code
                    loc = r.headers.get("location")
                if 300 <= code < 400 and loc:
                    cur = urljoin(cur, loc); continue
                break
    except httpx.TimeoutException:
        return {"url": u, "status": "broken", "code": None, "detail": "The site took too long to answer"}
    except Exception:
        return {"url": u, "status": "broken", "code": None, "detail": "Could not reach this address"}
    if code and 200 <= code < 300:
        return {"url": u, "status": "ok", "code": code, "detail": "Link works"}
    if code in (401, 403, 429, 999):
        return {"url": u, "status": "blocked", "code": code, "detail": "The site refuses automatic checks (common for LinkedIn) — open it yourself to confirm"}
    return {"url": u, "status": "broken", "code": code, "detail": "Page not found" if code in (404, 410) else f"The site answered with error {code}"}


def check_links(urls):
    urls = dedupe([u for u in urls if u])[:15]
    with ThreadPoolExecutor(max_workers=5) as ex:
        return list(ex.map(check_one, urls))


_hits = {}


def rate_ok(uid, limit=10, window=60):
    now = time.time()
    h = [t for t in _hits.get(uid, []) if now - t < window]
    if len(h) >= limit:
        _hits[uid] = h
        return False
    _hits[uid] = h + [now]
    return True
