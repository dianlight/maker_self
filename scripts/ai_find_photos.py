"""Cerca foto ufficiali online al posto delle foto caricate dal Form.

Le foto caricate servono solo per la prima classificazione (vision LLM in
ai_catalog.py): una volta confermata la riga, questo step cerca una foto
ufficiale online e, se la trova, scrive il solo link in foto_url, azzera
foto_drive_id e cestina la foto Drive (solo se non referenziata da altre righe).
Nessuna immagine viene salvata su Drive o nel repo.

Rotazione come check_datasheets.py: max MAX_ROWS_PER_RUN righe per run, quelle
con photo_last_check piu' vecchio (o mai controllate). Per ogni riga:
- foto Drive non ancora confermata (ai_stato != confermato) -> saltata:
  la foto serve per la prima classificazione;
- foto_url http non-Drive, viva e immagine -> ricontrollata con vision LLM
  (loghi, foto di gruppo, paesaggi/persone/animali scartati), se valida ok
  (elimina residui Drive);
- altrimenti prima il datasheet (datasheet_url): se pagina HTML o immagine
  diretta ne usa le foto, se PDF ne estrae le immagini embedded come
  riferimento visivo; poi la ricerca web come fallback. I candidati passano
  al vision LLM che ne sceglie uno valido o scarta tutto.

Regole foto (sempre applicate quando l'LLM e' configurato):
1) se il datasheet contiene una foto del componente, ha priorita';
2) mai loghi come foto valida;
3) mai foto di gruppo: solo il componente corretto, non assieme ad altri;
4) mai paesaggi, persone o animali.

Aggiorna sempre photo_last_check, scrive snapshot/photo-report.json (con gli
eventuali cestini falliti, riprovati al prossimo run) e cancella le thumbnail
vecchie in snapshot/thumbs perche' la prossima build_wiki riscarichi.

Env: GOOGLE_SERVICE_ACCOUNT_JSON, SHEET_ID, SHEET_TAB (default inventario),
MAX_ROWS_PER_RUN (default 25), HTTP_TIMEOUT (default 10),
WEBSEARCH_PROVIDERS, WEBSEARCH_PROVIDER, WEBSEARCH_API_KEY,
EXA_API_KEY, FIRECRAWL_API_KEY, PARALLEL_API_KEY, TINYFISH_API_KEY, TAVILY_API_KEY,
LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_KEY,
PHOTO_REPORT_FILE (default snapshot/photo-report.json),
PHOTO_DRY_RUN (con "1" non scrive su Sheet/Drive).
"""
import base64
import io
import json
import os
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import requests

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
MAX_ROWS = int(os.environ.get("MAX_ROWS_PER_RUN", "25") or 25)
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "10") or 10)
URL_COL = "foto_url"
FID_COL = "foto_drive_id"
STATE_COL = "ai_stato"
DS_COL = "datasheet_url"
CHECK_COL = "photo_last_check"
REPORT_FILE = os.environ.get("PHOTO_REPORT_FILE", "snapshot/photo-report.json")
DRY_RUN = os.environ.get("PHOTO_DRY_RUN", "").strip().lower() in ("1", "true", "si", "sì")
THUMBS_DIR = os.path.join("snapshot", "thumbs")

PROVIDERS = [p.strip().lower() for p in
             os.environ.get("WEBSEARCH_PROVIDERS",
                            os.environ.get("WEBSEARCH_PROVIDER", "")).split(",")
             if p.strip()]
SHARED_KEY = os.environ.get("WEBSEARCH_API_KEY", "")
KEY_ENVS = {"exa": "EXA_API_KEY", "firecrawl": "FIRECRAWL_API_KEY",
            "parallel": "PARALLEL_API_KEY", "tinyfish": "TINYFISH_API_KEY",
            "tavily": "TAVILY_API_KEY"}
BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "")
KEY = os.environ.get("LLM_KEY", "")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "")

DRIVE_ID_PATTERNS = (r"/file/d/([-\w]{20,})", r"[?&]id=([-\w]{20,})")
IMAGE_MIME = ("image/jpeg", "image/png", "image/webp", "image/gif")
MIN_BYTES = 5000               # niente icone/tracker: il wiki vuole foto vere
MAX_PAGE_BYTES = 1_000_000     # massimo letto da una pagina per estrarre immagini
MAX_PDF_BYTES = 15_000_000     # massimo scaricato da un PDF datasheet
MAX_IMG_CANDIDATES = 3         # quanti candidati web passano all'LLM
MAX_DS_IMAGES = 4              # quante foto max dal datasheet (HTML o PDF)
VISION_SIZE = 1024             # lato max immagini inviate al vision LLM
PAGE_UA = {"User-Agent": "Mozilla/5.0 (maker-self-photo-check/1.0)"}
# regole di validita' foto, condivise da check e scelta vision
PHOTO_RULES = (
    "Regole di validita' (tutte obbligatorie): "
    "1) la foto deve mostrare il componente elettronico richiesto "
    "(codice/descrizione/categoria); "
    "2) MAI loghi, icone, marchi, grafiche promozionali, schemi o piedinature: "
    "i loghi non sono mai una foto valida; "
    "3) MAI foto di gruppo: la foto deve contenere solo il componente corretto, "
    "non assieme ad altri componenti diversi; "
    "4) MAI paesaggi, persone o animali."
)
# domini da preferire quando si ordinano i candidati
PREFERRED_HOSTS = ("mouser", "digikey", "farnell", "rs-online", "lcsc", "tme.",
                   "adafruit", "sparkfun", "pololu", "arduino", "seeedstudio",
                   "dfrobot", "ti.com", "analog.com", "microchip.com", "nxp.com",
                   "infineon.com", "st.com", "texas")


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def sheets_svc():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def drive_svc():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def parse_check(v):
    """Data ultimo controllo, o None se mai controllata / illeggibile."""
    v = (v or "").strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(v).date()
    except ValueError:
        return None


def ensure_check_column(svc, header):
    """Aggiunge la colonna photo_last_check se manca. Ritorna l'indice."""
    # Sheets riempie le celle header vuote con placeholder localizzati
    # ("Colonna N"): vanno trattati come vuote
    compact = [h for h in header]
    while compact and (not compact[-1].strip()
                       or re.match(r"^(colonna|column)\s+\d+$",
                                   compact[-1].strip(), re.I)):
        compact.pop()
    if CHECK_COL in compact:
        return compact.index(CHECK_COL)
    ci = len(compact)
    if DRY_RUN:
        print(f"colonna {CHECK_COL} mancante, DRY_RUN: la creerei in {col_letter(ci)}1.")
        return ci
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!{col_letter(ci)}1",
        valueInputOption="RAW", body={"values": [[CHECK_COL]]}).execute()
    print(f"colonna {CHECK_COL} creata in {col_letter(ci)}1.")
    return ci


def drive_like(url):
    """True se l'URL e' un link Drive: e' una foto caricata, non online."""
    url = (url or "").strip()
    return any(re.search(p, url) for p in DRIVE_ID_PATTERNS)


def _ctype(r):
    return (r.headers.get("content-type") or "").split(";")[0].strip().lower()


def sniff(data):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ""


def check_image(url):
    """(ok, dettaglio) come il wiki: GET plain, solo header, niente HEAD."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False, "non http(s)"
    if drive_like(url):
        return False, "link Drive (foto caricata)"
    try:
        r = requests.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
        code, ct = r.status_code, _ctype(r)
        r.close()
    except requests.RequestException as e:
        return False, f"rete: {type(e).__name__}"
    if code in (404, 410):
        return False, f"HTTP {code}"
    if not (200 <= code < 400):
        return False, f"HTTP {code}"
    if ct not in IMAGE_MIME:
        return False, f"non immagine ({ct or 'n/a'})"
    return True, f"HTTP {code} {ct}"


def fetch_image(url):
    """Scarica come build_wiki (plain GET, timeout 30): se e' un'immagine
    valida (mime, min bytes, magic bytes) ritorna i bytes, altrimenti None.
    I bytes servono solo per validare: non vengono salvati."""
    try:
        r = requests.get(url, timeout=30, allow_redirects=True)
    except requests.RequestException:
        return None
    if not r.ok or not r.content:
        return None
    if _ctype(r) not in IMAGE_MIME:
        return None
    if len(r.content) < MIN_BYTES:
        return None
    return r.content if sniff(r.content) else None


def fetch_bytes(url, max_bytes=MAX_PDF_BYTES):
    """Scarica url: ritorna (content-type, bytes troncati). ("", b"") se ko."""
    try:
        r = requests.get(url, timeout=30, allow_redirects=True,
                         headers=PAGE_UA)
    except requests.RequestException:
        return "", b""
    if not r.ok or not r.content:
        return "", b""
    return _ctype(r), r.content[:max_bytes]


def pdf_embedded_images(pdf_bytes, max_n=MAX_DS_IMAGES):
    """Immagini raster embedded in un PDF (solo validazione, non salvate).
    Ritorna lista bytes, le piu' grandi prima. [] se PyMuPDF manca o ko."""
    try:
        import fitz
    except ImportError:
        print("  PyMuPDF mancante: foto dentro PDF non estraibili.")
        return []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        print(f"  PDF non apribile ({e}).")
        return []
    imgs = []
    try:
        for page in doc:
            for im in page.get_images(full=True):
                try:
                    data = doc.extract_image(im[0]).get("image", b"")
                except Exception:
                    continue
                if len(data) < MIN_BYTES or sniff(data) == "":
                    continue
                imgs.append(data)
                if len(imgs) >= max_n * 3:
                    break
            if len(imgs) >= max_n * 3:
                break
    finally:
        doc.close()
    imgs.sort(key=len, reverse=True)
    return imgs[:max_n]


def img_b64(data):
    """Riduce per la vision (come ai_catalog) e codifica base64."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((VISION_SIZE, VISION_SIZE))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        data = buf.getvalue()
    except Exception:
        pass
    return base64.b64encode(data).decode()


def llm_headers():
    import uuid
    h = {"Content-Type": "application/json"}
    if KEY:
        h["Authorization"] = f"Bearer {KEY}"
    if LLM_PROVIDER == "opencode-go":
        h["x-opencode-session"] = uuid.uuid4().hex
    return h


def extract_img_urls(base, html):
    """URL immagine da HTML (og:image, twitter:image, <img>). Max 12."""
    raw = []
    for m in re.finditer(r"<meta[^>]+>", html, re.I):
        tag = m.group(0)
        if re.search(r"(?:property|name)=[\"'](?:og:image|twitter:image)[\"']",
                     tag, re.I):
            cm = re.search(r"content=[\"']([^\"']+)[\"']", tag, re.I)
            if cm:
                raw.append(cm.group(1))
    raw += [m.group(1) for m in
            re.finditer(r"<img[^>]+src=[\"']([^\"']+)[\"']", html, re.I)]
    out, seen = [], set()
    for u in raw:
        u = urljoin(base, u.strip()).strip()
        if not u.startswith(("http://", "https://")):
            continue
        if re.search(r"\.svg(?:\?|$)", u, re.I):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:12]


def page_image_urls(page):
    """URL immagine trovati nella pagina (og:image, twitter:image, <img>).
    Se la URL cercata e' un'immagine diretta, ritorna quella."""
    try:
        r = requests.get(page, timeout=TIMEOUT, allow_redirects=True,
                         headers=PAGE_UA)
    except requests.RequestException:
        return []
    if not r.ok:
        return []
    if _ctype(r).startswith("image/"):
        return [page]
    html = r.content[:MAX_PAGE_BYTES].decode("utf-8", "replace")
    return extract_img_urls(page, html)


def host_score(url):
    host = urlparse(url).netloc.lower()
    return 1 if any(p in host for p in PREFERRED_HOSTS) else 0


def search_exa(q, key):
    r = requests.post("https://api.exa.ai/search",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"query": q, "numResults": 10, "type": "fast"},
                      timeout=60)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("results", []) if x.get("url")]


def search_firecrawl(q, key):
    r = requests.post("https://api.firecrawl.dev/v2/search",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"query": q, "limit": 10}, timeout=60)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("data", {}).get("web", [])
            if x.get("url")]


def search_parallel(q, key):
    r = requests.post("https://api.parallel.ai/v1/search",
                      headers={"x-api-key": key},
                      json={"objective": f"Find a product photo (image) of this electronic component: {q}",
                            "search_queries": [q, f"{q} product image"]},
                      timeout=60)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("results", []) if x.get("url")]


def search_tinyfish(q, key):
    r = requests.get("https://api.search.tinyfish.ai",
                     headers={"X-API-Key": key},
                     params={"query": q, "language": "en"}, timeout=30)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("results", []) if x.get("url")]


def search_tavily(q, key):
    r = requests.post("https://api.tavily.com/search",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"query": q, "max_results": 10}, timeout=60)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("results", []) if x.get("url")]


SEARCHERS = {"exa": search_exa, "firecrawl": search_firecrawl,
             "parallel": search_parallel, "tinyfish": search_tinyfish,
             "tavily": search_tavily}


def provider_key(name):
    return os.environ.get(KEY_ENVS[name], "") or SHARED_KEY


def web_search_all(q, order, dead):
    """Prova i provider in ordine; salta quelli senza chiave o gia' morti.
    Ritorna (provider, urls)."""
    last_err = None
    for name in order:
        if name in dead:
            continue
        key = provider_key(name)
        if not key:
            print(f"  {name}: chiave mancante ({KEY_ENVS[name]}), salto.")
            dead.add(name)
            continue
        try:
            urls = SEARCHERS[name](q, key)
        except Exception as e:
            print(f"  {name}: fallito ({e}), provo il prossimo.")
            dead.add(name)
            last_err = e
            continue
        if urls:
            return name, urls
        print(f"  {name}: zero risultati, provo il prossimo.")
    if last_err is not None and not any(n not in dead for n in order):
        print(f"  tutti i provider falliti (ultimo errore: {last_err}).")
    return None, []


def build_queries(t):
    base = f"{t['codice']} {t['descrizione']}".strip() or (t.get("categoria") or "").strip()
    if not base:
        return []
    return [f"{base} product image", f"{base} official product photo"]


def find_image_candidates(t, order, dead):
    """Cerca pagine, ne estrae le immagini e ne valida (scaricandole come il
    wiki). Ritorna lista (url, bytes, 'web'), domini noti prima."""
    found, seen = [], set()
    for q in build_queries(t):
        print(f"riga {t['n']} [{t['codice']}]: cerco immagini {q!r}...")
        used, pages = web_search_all(q, order, dead)
        if used:
            print(f"  risultati via {used}.")
        for page in pages[:6]:
            for u in page_image_urls(page):
                if u in seen:
                    continue
                seen.add(u)
                b = fetch_image(u)
                if b is not None:
                    found.append((u, b, "web"))
            if len(found) > MAX_IMG_CANDIDATES + 2:
                break
        if found:
            break
    # i domini dei distributori/produttori passano prima all'LLM
    found.sort(key=lambda x: host_score(x[0]), reverse=True)
    return found[:MAX_IMG_CANDIDATES]


def get_datasheet_candidates(t):
    """Foto dal datasheet (datasheet_url). Ritorna (linkable, pdf_refs):
    - linkable: lista (url, bytes, 'datasheet'), usabile come foto_url;
    - pdf_refs: immagini embedded nel PDF, solo riferimento visivo (un PDF
      non e' linkabile come foto, ma dice all'LLM com'e' fatto il pezzo)."""
    linkable, refs = [], []
    ds = (t.get("datasheet") or "").strip()
    if not ds or not ds.startswith(("http://", "https://")) or drive_like(ds):
        return linkable, refs
    if re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", ds, re.I):
        b = fetch_image(ds)
        if b is not None:
            print(f"  datasheet e' un'immagine diretta.")
            return [(ds, b, "datasheet")], refs
    try:
        ct, content = fetch_bytes(ds)
    except Exception as e:
        print(f"  datasheet non scaricato ({e}).")
        return linkable, refs
    if not content:
        return linkable, refs
    if ct.startswith("image/"):
        if len(content) >= MIN_BYTES and sniff(content):
            return [(ds, content, "datasheet")], refs
        return linkable, refs
    if ct == "application/pdf" or ds.lower().split("?")[0].endswith(".pdf"):
        emb = pdf_embedded_images(content)
        print(f"  datasheet PDF: {len(emb)} immagini embedded.")
        refs.extend(emb)
        return linkable, refs
    # pagina HTML: usa le foto della pagina del datasheet per prime
    try:
        html = content[:MAX_PAGE_BYTES].decode("utf-8", "replace")
    except Exception:
        return linkable, refs
    for u in extract_img_urls(ds, html):
        b = fetch_image(u)
        if b is not None:
            linkable.append((u, b, "datasheet"))
        if len(linkable) >= MAX_DS_IMAGES:
            break
    if linkable:
        print(f"  datasheet HTML: {len(linkable)} foto candidabili.")
    return linkable, refs


def llm_vision_check(part, img_bytes):
    """(valida, motivo): la foto esistente rispetta le regole? Senza LLM
    ritorna (True, fallback) per non bloccare il flusso."""
    if not (BASE_URL and MODEL):
        return True, "vision non configurata"
    text = ("Sei un revisore di foto per un inventario di componenti elettronici. "
            f"Componente: codice={part['codice']!r}, "
            f"descrizione={part['descrizione']!r}, "
            f"categoria={part['categoria']!r}. "
            + PHOTO_RULES +
            " Guarda la foto e rispondi SOLO con JSON "
            '{"ok": true/false, "reason": "motivo breve"}.')
    try:
        r = requests.post(
            f"{BASE_URL}/chat/completions", headers=llm_headers(),
            json={"model": MODEL,
                  "messages": [{"role": "user", "content": [
                      {"type": "text", "text": text},
                      {"type": "image_url",
                       "image_url": {"url": f"data:image/jpeg;base64,{img_b64(img_bytes)}"}}]}],
                  "temperature": 0.0, "max_tokens": 300,
                  "response_format": {"type": "json_object"}},
            timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"].get("content", "")
        obj = json.loads(content)
        return bool(obj.get("ok")), str(obj.get("reason", ""))[:200]
    except Exception as e:
        print(f"  vision check fallita ({e}), tengo la foto.")
        return True, f"vision fallita: {e}"


def llm_pick_best(part, candidates, pdf_refs=None):
    """Sceglie con vision tra (url, bytes, fonte). Ritorna (url|None, motivo).
    Le foto del datasheet hanno priorita' a pari validita'. Se nessuna
    rispetta le regole ritorna (None, motivo): meglio nessuna foto che una
    sbagliata. Senza LLM: primo datasheet, poi primo web (regole non verificate)."""
    linkable = [(u, b, s) for u, b, s in candidates if u]
    if not linkable:
        return None, "nessun candidato linkabile"
    if not (BASE_URL and MODEL):
        ds = next((u for u, _, s in linkable if s == "datasheet"), None)
        return (ds or linkable[0][0]), "LLM non configurato (regole non verificate)"
    pdf_refs = pdf_refs or []
    head = ("Sei un revisore di foto per un inventario di componenti elettronici. "
            f"Componente: codice={part['codice']!r}, "
            f"descrizione={part['descrizione']!r}, "
            f"categoria={part['categoria']!r}. "
            + PHOTO_RULES +
            " A pari validita' preferisci i candidati fonte 'datasheet'. "
            "Se nessuno e' valido rispondi con url vuoto. "
            "Rispondi SOLO con JSON {\"url\": \"<url scelto o stringa vuota>\", "
            "\"reason\": \"motivo breve\"}. Candidati:")
    content = [{"type": "text", "text": head}]
    for i, b in enumerate(pdf_refs[:MAX_DS_IMAGES]):
        content.append({"type": "text",
                        "text": f"Riferimento {i}: foto estratta dal PDF datasheet "
                                "(aspetto vero del componente, NON candidabile)."})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64(b)}"}})
    for i, (u, b, s) in enumerate(linkable):
        content.append({"type": "text",
                        "text": f"Candidato {i} (fonte {s}): {u}"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64(b)}"}})
    try:
        r = requests.post(
            f"{BASE_URL}/chat/completions", headers=llm_headers(),
            json={"model": MODEL,
                  "messages": [{"role": "user", "content": content}],
                  "temperature": 0.0, "max_tokens": 500,
                  "response_format": {"type": "json_object"}},
            timeout=180)
        r.raise_for_status()
        obj = json.loads(r.json()["choices"][0]["message"].get("content", "{}"))
        choice, reason = (obj.get("url") or "").strip(), str(obj.get("reason", ""))[:200]
        for u, _, _ in linkable:
            if choice and (choice == u or choice in u or u in choice):
                return u, reason or "scelta vision"
        if not choice:
            return None, reason or "nessun candidato valido"
    except Exception as e:
        print(f"  LLM fallito ({e}), uso primo candidato.")
        return linkable[0][0], f"fallback LLM fallito: {e}"
    return linkable[0][0], "fallback: risposta LLM non riconosciuta"


def trash_photo(drv, fid):
    """Cestina la foto Drive. 'trashed' | 'missing' | 'error: ...'."""
    from googleapiclient.errors import HttpError
    try:
        drv.files().update(fileId=fid, trashed=True, fields="id,trashed").execute()
        return "trashed"
    except HttpError as e:
        if e.resp.status == 404:
            return "missing"
        return f"error: HTTP {e.resp.status}"
    except Exception as e:
        return f"error: {e}"


def drop_thumb(pid):
    """Elimina la thumbnail cache: la prossima build_wiki riscarica."""
    for ext in (".jpg", ".png", ".webp", ".gif"):
        p = os.path.join(THUMBS_DIR, pid + ext)
        if os.path.isfile(p):
            try:
                os.remove(p)
                print(f"  thumbnail rimossa: {p}")
            except OSError as e:
                print(f"  thumbnail non rimossa ({e}).")


def load_trash_failed():
    """Cestini falliti del report precedente, da riprovare."""
    try:
        with open(REPORT_FILE, encoding="utf-8") as f:
            prev = json.load(f)
        return [x for x in prev.get("trash_failed", []) if isinstance(x, str)]
    except Exception:
        return []


def main():
    if not (SHEET_ID and SA_JSON):
        print("SHEET_ID o GOOGLE_SERVICE_ACCOUNT_JSON mancanti, skip.")
        return
    bad = [p for p in PROVIDERS if p not in SEARCHERS]
    if bad:
        print(f"WEBSEARCH_PROVIDERS non validi: {bad}. "
              f"Scegli tra: {', '.join(SEARCHERS)}")
    order = [p for p in PROVIDERS if p in SEARCHERS]
    if order:
        start = date.today().toordinal() % len(order)
        order = order[start:] + order[:start]
        print(f"provider in ordine: {', '.join(order)}")
    else:
        print("nessun provider di ricerca configurato: solo controllo link.")

    svc = sheets_svc()
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z").execute().get("values", [])
    if not vals:
        print("Sheet vuoto.")
        return
    header = [h.strip() for h in vals[0]]
    idx = {h: i for i, h in enumerate(header)}
    for c in (URL_COL, FID_COL, STATE_COL):
        if c not in idx:
            print(f"colonna {c} mancante.")
            return
    ci = ensure_check_column(svc, header)

    rows = []
    for n, r in enumerate(vals[1:], 2):
        g = lambda c, i=idx: r[i[c]].strip() if c in i and i[c] < len(r) else ""
        if not (g("id") or g("codice") or g("descrizione")):
            continue  # riga vuota
        rows.append({"n": n, "id": g("id"), "categoria": g("categoria"),
                     "codice": g("codice"), "descrizione": g("descrizione"),
                     "foto_url": g(URL_COL), "fid": g(FID_COL),
                     "stato": g(STATE_COL).lower(),
                     "datasheet": (r[idx[DS_COL]].strip()
                                   if DS_COL in idx and idx[DS_COL] < len(r)
                                   else ""),
                     "last": parse_check(r[ci] if ci < len(r) else "")})
    # quali righe referenziano ogni foto Drive: stato grezzo del Sheet,
    # righe "vuote" comprese (basta che abbiano il fid)
    refs = {}
    for n2, r in enumerate(vals[1:], 2):
        if FID_COL in idx and idx[FID_COL] < len(r):
            f2 = r[idx[FID_COL]].strip()
            if f2:
                refs.setdefault(f2, []).append(n2)
    # mai controllate prima, poi le piu' vecchie
    rows.sort(key=lambda x: (x["last"] is not None, x["last"] or date.min))
    selected = rows[:MAX_ROWS]
    print(f"righe totali: {len(rows)}, controllate in questo run: {len(selected)}")

    today = date.today().isoformat()
    prev_failed = load_trash_failed()
    dead, cleared, report = set(), [], []
    for t in selected:
        entry = {"row": t["n"], "id": t["id"], "codice": t["codice"],
                 "categoria": t["categoria"], "descrizione": t["descrizione"],
                 "old_url": t["foto_url"], "new_url": t["foto_url"],
                 "fid": t["fid"], "checked_at": today}
        uploaded = t["fid"] or drive_like(t["foto_url"])
        try:
            if uploaded and t["stato"] != "confermato":
                entry.update(status="skip",
                             detail="foto caricata in attesa di classificazione")
            else:
                ok, detail = check_image(t["foto_url"])
                if ok:
                    # la foto esistente deve rispettare le regole: vision check
                    raw = fetch_image(t["foto_url"])
                    if raw is None:
                        ok, detail = False, f"{detail}; download fallito"
                    else:
                        valid, why = llm_vision_check(t, raw)
                        if not valid:
                            ok, detail = False, f"foto scartata dalla vision: {why}"
                        else:
                            detail = f"{detail}; vision ok ({why})" if why else detail
                if ok and not t["fid"]:
                    entry.update(status="ok", detail=detail)
                elif ok:
                    # foto online gia' valida: il residuo Drive va tolto
                    updates = [(idx[FID_COL], "")]
                    if DRY_RUN:
                        print(f"riga {t['n']} [{t['codice']}]: DRY_RUN, tolgo foto Drive.")
                    else:
                        svc.spreadsheets().values().batchUpdate(
                            spreadsheetId=SHEET_ID,
                            body={"valueInputOption": "RAW", "data": [
                                {"range": f"{SHEET_TAB}!{col_letter(a)}{t['n']}",
                                 "values": [[v]]} for a, v in updates]}).execute()
                    cleared.append((t["fid"], t["n"]))
                    if not DRY_RUN:
                        drop_thumb(t["id"])
                    entry.update(status="replaced", fid="",
                                 new_url=t["foto_url"],
                                 detail=f"foto Drive rimossa ({detail})")
                else:
                    # datasheet prima (foto ufficiale), web search come fallback
                    ds_cands, pdf_refs = get_datasheet_candidates(t)
                    web_cands = (find_image_candidates(t, order, dead)
                                 if order and (t["codice"] or t["descrizione"]) else [])
                    seen_u = {u for u, _, _ in ds_cands}
                    cands = ds_cands + [w for w in web_cands if w[0] not in seen_u]
                    if not cands:
                        why = "nessun provider di ricerca" if not order else \
                            "nessuna immagine valida trovata"
                        entry.update(status="missing", detail=f"{detail}; {why}")
                    else:
                        best, reason = llm_pick_best(t, cands, pdf_refs)
                        if best is None:
                            entry.update(status="missing",
                                         detail=f"{detail}; scartate dalla vision "
                                                f"({len(cands)} candidati): {reason}")
                        else:
                            src = next((s for u, _, s in cands if u == best),
                                       "web")
                            had_fid = bool(t["fid"])
                            updates = [(idx[URL_COL], best)]
                            if had_fid:
                                updates.append((idx[FID_COL], ""))
                            if DRY_RUN:
                                print(f"riga {t['n']} [{t['codice']}]: DRY_RUN, foto -> {best}")
                            else:
                                svc.spreadsheets().values().batchUpdate(
                                    spreadsheetId=SHEET_ID,
                                    body={"valueInputOption": "RAW", "data": [
                                        {"range": f"{SHEET_TAB}!{col_letter(a)}{t['n']}",
                                         "values": [[v]]} for a, v in updates]}).execute()
                            if had_fid:
                                cleared.append((t["fid"], t["n"]))
                            if not DRY_RUN:
                                drop_thumb(t["id"])
                            entry.update(status="replaced" if had_fid else "set",
                                         fid="", new_url=best,
                                         detail=f"foto da {src} ({len(cands)} candidati): "
                                                f"{reason}")
            # sempre il timestamp di controllo (tranne DRY_RUN)
            if not DRY_RUN and entry["status"] != "error":
                svc.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"{SHEET_TAB}!{col_letter(ci)}{t['n']}",
                    valueInputOption="RAW", body={"values": [[today]]}).execute()
        except Exception as e:
            entry.update(status="error", detail=str(e))
            print(f"riga {t['n']} [{t['codice']}]: errore ({e})")
        report.append(entry)
        print(f"riga {t['n']} [{t['codice']}]: "
              f"{entry.get('status', '?')} ({entry.get('detail', '')})")

    # foto Drive ora orfane: per ogni fid cestinato tolgo la riga appena
    # liberata dalle righe ancora referenzianti (refs parte dal Sheet grezzo)
    for fid, n in cleared:
        if fid in refs:
            refs[fid] = [k for k in refs[fid] if k != n]
            if not refs[fid]:
                del refs[fid]
    trash_failed = []
    trashed = 0
    drv = None
    for fid in dict.fromkeys([f for f, _ in cleared] + prev_failed):
        if fid in refs:
            print(f"foto Drive {fid}: ancora usata da righe {refs[fid]}, la tengo.")
            continue
        if DRY_RUN:
            print(f"foto Drive {fid}: DRY_RUN, la cestinerei.")
            continue
        if drv is None:
            drv = drive_svc()
        res = trash_photo(drv, fid)
        print(f"foto Drive {fid}: {res}")
        if res == "trashed":
            trashed += 1
        elif res.startswith("error"):
            trash_failed.append(fid)

    if DRY_RUN:
        print(f"DRY_RUN: report non scritto ({len(report)} righe esaminate).")
        return
    os.makedirs(os.path.dirname(REPORT_FILE) or ".", exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump({"checked_at": today, "dry_run": DRY_RUN, "rows": report,
                   "trash_failed": trash_failed}, f,
                  ensure_ascii=False, indent=2)
    n_new = sum(1 for e in report if e.get("status") in ("set", "replaced"))
    print(f"report: {REPORT_FILE} ({len(report)} righe), "
          f"foto online: {n_new}, cestinate: {trashed}, "
          f"cestino falliti: {len(trash_failed)}")


if __name__ == "__main__":
    main()
