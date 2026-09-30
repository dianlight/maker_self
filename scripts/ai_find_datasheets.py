"""Cerca datasheet validi via web search per le righe segnalate.

Legge TARGETS_FILE (prodotto da check_datasheets.py), per ogni riga cerca
il datasheet con il provider configurato, valida i candidati via HTTP e
scrive il primo valido in colonna H + aggiorna datasheet_last_check.
Non sovrascrive mai un URL diventato valido nel frattempo.

Provider: lista in WEBSEARCH_PROVIDERS (es. "exa,firecrawl,parallel,tinyfish",
fallback singolo WEBSEARCH_PROVIDER). Uso a rotazione: ogni giorno si parte
da un provider diverso e se uno fallisce si passa al successivo.
Chiavi: EXA_API_KEY, FIRECRAWL_API_KEY, PARALLEL_API_KEY, TINYFISH_API_KEY
oppure WEBSEARCH_API_KEY condivisa come fallback.
Se piu' candidati sono validi e l'LLM e' configurato (LLM_BASE_URL/MODEL),
chiede al modello di scegliere il migliore; altrimenti prende il primo.

Env: GOOGLE_SERVICE_ACCOUNT_JSON, SHEET_ID, SHEET_TAB, TARGETS_FILE,
WEBSEARCH_PROVIDERS, WEBSEARCH_PROVIDER, WEBSEARCH_API_KEY,
EXA_API_KEY, FIRECRAWL_API_KEY, PARALLEL_API_KEY, TINYFISH_API_KEY,
LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_KEY, HTTP_TIMEOUT (default 10).
"""
import json
import os
from datetime import date

import requests

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
TARGETS_FILE = os.environ.get("TARGETS_FILE", "/tmp/datasheet_targets.json")
PROVIDERS = [p.strip().lower() for p in
             os.environ.get("WEBSEARCH_PROVIDERS",
                            os.environ.get("WEBSEARCH_PROVIDER", "")).split(",")
             if p.strip()]
SHARED_KEY = os.environ.get("WEBSEARCH_API_KEY", "")
KEY_ENVS = {"exa": "EXA_API_KEY", "firecrawl": "FIRECRAWL_API_KEY",
            "parallel": "PARALLEL_API_KEY", "tinyfish": "TINYFISH_API_KEY"}
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "10") or 10)
BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "")
KEY = os.environ.get("LLM_KEY", "")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "")
URL_COL = "datasheet_url"
CHECK_COL = "datasheet_last_check"


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
                      json={"objective": f"Find the official manufacturer datasheet (PDF) for this electronic component: {q}",
                            "search_queries": [q, f"{q} filetype:pdf"]},
                      timeout=60)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("results", []) if x.get("url")]


def search_tinyfish(q, key):
    r = requests.get("https://api.search.tinyfish.ai",
                     headers={"X-API-Key": key},
                     params={"query": q, "language": "en"}, timeout=30)
    r.raise_for_status()
    return [x["url"] for x in r.json().get("results", []) if x.get("url")]


SEARCHERS = {"exa": search_exa, "firecrawl": search_firecrawl,
             "parallel": search_parallel, "tinyfish": search_tinyfish}


def provider_key(name):
    return os.environ.get(KEY_ENVS[name], "") or SHARED_KEY


def web_search_all(q, order, dead):
    """Prova i provider in ordine; salta quelli senza chiave o gia' morti.
    Ritorna (provider, urls). urls vuoto solo se nessuno ha risposto utile."""
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


def url_alive(url):
    try:
        r = requests.head(url, timeout=TIMEOUT, allow_redirects=True,
                          headers={"User-Agent": "maker-self-datasheet-check/1.0"})
        if r.status_code in (404, 410):
            return None
        if 200 <= r.status_code < 400:
            return r.url or url
    except requests.RequestException:
        pass
    try:
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=True,
                         stream=True, headers={"User-Agent": "maker-self-datasheet-check/1.0"})
        r.close()
        if r.status_code in (404, 410):
            return None
        if 200 <= r.status_code < 400:
            return r.url or url
    except requests.RequestException:
        return None
    return None


def llm_pick_best(part, candidates):
    """Chiede al modello di scegliere il miglior datasheet. Fallback: primo."""
    if not (BASE_URL and MODEL):
        return candidates[0]
    import uuid
    prompt = ("Scegli l'URL del datasheet ufficiale piu' affidabile per questo "
              f"componente elettronico: codice={part['codice']!r}, "
              f"descrizione={part['descrizione']!r}, categoria={part['categoria']!r}. "
              "Candidati:\n" + "\n".join(f"- {u}" for u in candidates) +
              "\nRispondi SOLO con l'URL scelto, nient'altro.")
    headers = {"Content-Type": "application/json"}
    if KEY:
        headers["Authorization"] = f"Bearer {KEY}"
    if LLM_PROVIDER == "opencode-go":
        headers["x-opencode-session"] = uuid.uuid4().hex
    try:
        r = requests.post(f"{BASE_URL}/chat/completions", headers=headers,
                          json={"model": MODEL,
                                "messages": [{"role": "user", "content": prompt}],
                                "temperature": 0.0, "max_tokens": 500},
                          timeout=120)
        r.raise_for_status()
        choice = r.json()["choices"][0]["message"].get("content", "").strip()
        for u in candidates:
            if u in choice:
                return u
    except Exception as e:
        print(f"  LLM fallito ({e}), uso primo candidato.")
    return candidates[0]


def build_queries(t):
    base = f"{t['codice']} {t['descrizione']}".strip()
    if base:
        return [f"{base} datasheet pdf", f"{base} datasheet"]
    return []


def main():
    if not (SHEET_ID and SA_JSON):
        print("SHEET_ID o GOOGLE_SERVICE_ACCOUNT_JSON mancanti, skip.")
        return
    try:
        with open(TARGETS_FILE) as f:
            targets = json.load(f)
    except FileNotFoundError:
        print(f"{TARGETS_FILE} assente, niente da fare.")
        return
    if not targets:
        print("nessuna riga da sistemare.")
        return
    bad = [p for p in PROVIDERS if p not in SEARCHERS]
    if bad:
        print(f"WEBSEARCH_PROVIDERS non validi: {bad}, skip. "
              f"Scegli tra: {', '.join(SEARCHERS)}")
        return
    if not PROVIDERS:
        print("WEBSEARCH_PROVIDERS/WEBSEARCH_PROVIDER vuoti, skip.")
        return
    # rotazione giornaliera: ogni giorno parte un provider diverso
    start = date.today().toordinal() % len(PROVIDERS)
    order = PROVIDERS[start:] + PROVIDERS[:start]
    print(f"provider in ordine: {', '.join(order)}")
    dead = set()
    svc = sheets_svc()
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z").execute().get("values", [])
    header = [h.strip() for h in vals[0]] if vals else []
    idx = {h: i for i, h in enumerate(header)}
    if URL_COL not in idx or CHECK_COL not in idx:
        print(f"colonne {URL_COL}/{CHECK_COL} mancanti.")
        return
    ui, ci = idx[URL_COL], idx[CHECK_COL]
    today = date.today().isoformat()
    fixed = 0
    for t in targets:
        n = t["row"]
        cur = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEET_TAB}!{col_letter(ui)}{n}:{col_letter(ci)}{n}"
            ).execute().get("values", [[]])[0]
        cur_url = cur[0].strip() if len(cur) > 0 else ""
        if cur_url and cur_url != t["old_url"] and url_alive(cur_url):
            print(f"riga {n} [{t['codice']}]: URL cambiato nel frattempo ed e' valido, salto.")
            continue
        found = []
        for q in build_queries(t):
            print(f"riga {n} [{t['codice']}]: cerco {q!r}...")
            used, urls = web_search_all(q, order, dead)
            if used:
                print(f"  risultati via {used}.")
            for cand in urls:
                alive = url_alive(cand)
                if alive and alive not in found:
                    found.append(alive)
                if len(found) >= 3:
                    break
            if found:
                break
        if not found:
            print(f"riga {n} [{t['codice']}]: nessun candidato valido.")
            continue
        best = llm_pick_best(t, found)
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID, body={"valueInputOption": "RAW", "data": [
                {"range": f"{SHEET_TAB}!{col_letter(ui)}{n}", "values": [[best]]},
                {"range": f"{SHEET_TAB}!{col_letter(ci)}{n}", "values": [[today]]},
            ]}).execute()
        fixed += 1
        print(f"riga {n} [{t['codice']}]: H <- {best}")
    print(f"sistemate {fixed}/{len(targets)} righe.")


if __name__ == "__main__":
    main()
