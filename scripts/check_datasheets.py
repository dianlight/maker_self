"""Controlla i datasheet (colonna H) a rotazione: max N righe per run.

Seleziona le righe mai controllate o con `datasheet_last_check` piu' vecchio,
verifica l'URL con una richiesta HTTP (404 = non valido) e scrive un report
JSON con le righe da sistemare. Aggiorna sempre `datasheet_last_check`.

Env: GOOGLE_SERVICE_ACCOUNT_JSON, SHEET_ID, SHEET_TAB (default inventario),
MAX_ROWS_PER_RUN (default 25), HTTP_TIMEOUT (default 10),
TARGETS_FILE (default /tmp/datasheet_targets.json),
REPORT_FILE (default snapshot/datasheet-report.json).
"""
import json
import os
import re
from datetime import date, datetime

import requests

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
MAX_ROWS = int(os.environ.get("MAX_ROWS_PER_RUN", "25") or 25)
TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "10") or 10)
CHECK_COL = "datasheet_last_check"
URL_COL = "datasheet_url"
TARGETS_FILE = os.environ.get("TARGETS_FILE", "/tmp/datasheet_targets.json")
REPORT_FILE = os.environ.get("REPORT_FILE", "snapshot/datasheet-report.json")

INVALID_STATUSES = {404, 410}


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


def check_url(url):
    """(stato, dettaglio). stato: ok | invalid | unknown."""
    url = (url or "").strip()
    if not url:
        return "empty", "cella vuota"
    if not url.startswith(("http://", "https://")):
        return "invalid", "URL non http(s)"
    try:
        r = requests.head(url, timeout=TIMEOUT, allow_redirects=True,
                          headers={"User-Agent": "maker-self-datasheet-check/1.0"})
        if r.status_code in INVALID_STATUSES:
            return "invalid", f"HTTP {r.status_code}"
        if 200 <= r.status_code < 400:
            return "ok", f"HTTP {r.status_code}"
        return "unknown", f"HTTP {r.status_code}"
    except requests.RequestException as e:
        # fallback GET: alcuni host rifiutano HEAD
        try:
            r = requests.get(url, timeout=TIMEOUT, allow_redirects=True,
                             stream=True, headers={"User-Agent": "maker-self-datasheet-check/1.0"})
            r.close()
            if r.status_code in INVALID_STATUSES:
                return "invalid", f"HTTP {r.status_code}"
            if 200 <= r.status_code < 400:
                return "ok", f"HTTP {r.status_code}"
            return "unknown", f"HTTP {r.status_code}"
        except requests.RequestException as e2:
            return "invalid", f"rete: {type(e2).__name__} ({e})"


def ensure_check_column(svc, header):
    """Aggiunge la colonna datasheet_last_check se manca. Ritorna l'indice."""
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
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!{col_letter(ci)}1",
        valueInputOption="RAW", body={"values": [[CHECK_COL]]}).execute()
    print(f"colonna {CHECK_COL} creata in {col_letter(ci)}1.")
    return ci


def main():
    if not (SHEET_ID and SA_JSON):
        print("SHEET_ID o GOOGLE_SERVICE_ACCOUNT_JSON mancanti, skip.")
        return
    svc = sheets_svc()
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z").execute().get("values", [])
    if not vals:
        print("Sheet vuoto.")
        return
    header = [h.strip() for h in vals[0]]
    idx = {h: i for i, h in enumerate(header)}
    if URL_COL not in idx:
        print(f"colonna {URL_COL} mancante.")
        return
    ci = ensure_check_column(svc, header)

    rows = []
    for n, r in enumerate(vals[1:], 2):
        g = lambda c, i=idx: r[i[c]].strip() if c in i and i[c] < len(r) else ""
        if not (g("id") or g("codice") or g("descrizione")):
            continue  # riga vuota
        rows.append({"n": n, "id": g("id"), "categoria": g("categoria"),
                     "codice": g("codice"), "descrizione": g("descrizione"),
                     "url": g(URL_COL),
                     "last": parse_check(r[ci] if ci < len(r) else "")})
    # mai controllate prima, poi le piu' vecchie
    rows.sort(key=lambda x: (x["last"] is not None, x["last"] or date.min))
    selected = rows[:MAX_ROWS]
    print(f"righe totali: {len(rows)}, controllate in questo run: {len(selected)}")

    today = date.today().isoformat()
    report, targets, stamps = [], [], []
    for row in selected:
        status, detail = check_url(row["url"])
        entry = {"row": row["n"], "id": row["id"], "codice": row["codice"],
                 "categoria": row["categoria"], "descrizione": row["descrizione"],
                 "old_url": row["url"], "status": status, "detail": detail,
                 "checked_at": today}
        report.append(entry)
        if status in ("empty", "invalid"):
            targets.append(entry)
        stamps.append({"range": f"{SHEET_TAB}!{col_letter(ci)}{row['n']}",
                       "values": [[today]]})
        print(f"riga {row['n']} [{row['codice']}]: {status} ({detail})")
    if stamps:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"valueInputOption": "RAW", "data": stamps}).execute()
    os.makedirs(os.path.dirname(REPORT_FILE) or ".", exist_ok=True)
    with open(REPORT_FILE, "w") as f:
        json.dump({"checked_at": today, "rows": report}, f,
                  ensure_ascii=False, indent=2)
    with open(TARGETS_FILE, "w") as f:
        json.dump(targets, f, ensure_ascii=False, indent=2)
    print(f"report: {REPORT_FILE} ({len(report)} righe), "
          f"da sistemare: {len(targets)} -> {TARGETS_FILE}")


if __name__ == "__main__":
    main()
