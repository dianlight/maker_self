"""Sheet -> git snapshot. Solo lettura Sheet, scrittura snapshot/."""
import json
import os
import re
import yaml

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

EXPECTED = ["id", "categoria", "codice", "quantita", "descrizione", "interfaccia",
            "note", "datasheet_url", "datasheet_last_check", "posizione", "foto_drive_id", "foto_url",
            "ai_proposta", "ai_stato", "updated_at"]


def slug(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "na"


def fetch_rows():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    res = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z").execute()
    return res.get("values", [])


def to_parts(values):
    if not values:
        return []
    header = [h.strip() for h in values[0]]
    idx = {h: i for i, h in enumerate(header)}
    parts = []
    for n, r in enumerate(values[1:], 1):
        g = lambda c: r[idx[c]].strip() if c in idx and idx[c] < len(r) else ""
        if not (g("id") or g("codice") or g("descrizione") or g("foto_drive_id")):
            continue  # riga vuota, mai in snapshot
        categoria, codice = g("categoria"), g("codice")
        try:
            q = int(g("quantita") or 0)
        except ValueError:
            q = 0
        pid = g("id") or f"{slug(categoria)}-{slug(codice)}-{n:02d}"
        parts.append({
            "id": pid, "categoria": categoria, "codice": codice, "quantita": max(q, 0),
            "descrizione": g("descrizione"), "interfaccia": g("interfaccia"),
            "note": g("note"), "datasheet_url": g("datasheet_url"),
            "datasheet_last_check": g("datasheet_last_check"),
            "posizione": g("posizione"), "foto_drive_id": g("foto_drive_id"),
            "foto_url": g("foto_url"), "ai_proposta": g("ai_proposta"),
            "ai_stato": g("ai_stato"), "updated_at": g("updated_at"),
        })
    return parts


def main():
    if not (SHEET_ID and SA_JSON):
        print("SHEET_ID o GOOGLE_SERVICE_ACCOUNT_JSON mancanti, skip.")
        return
    parts = to_parts(fetch_rows())
    os.makedirs("snapshot/categories", exist_ok=True)
    os.makedirs("snapshot/thumbs", exist_ok=True)
    with open("snapshot/inventory.json", "w") as f:
        json.dump(parts, f, ensure_ascii=False, indent=2)
    by_cat = {}
    for p in parts:
        by_cat.setdefault(p["categoria"] or "misc", []).append(p)
    for cat, items in by_cat.items():
        with open(f"snapshot/categories/{slug(cat)}.yaml", "w") as f:
            yaml.safe_dump(items, f, allow_unicode=True, sort_keys=False)
    print(f"snapshot: {len(parts)} parti, {len(by_cat)} categorie")


if __name__ == "__main__":
    main()
