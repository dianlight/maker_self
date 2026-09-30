"""Promuove le risposte del Form nel tab inventario (append o incremento quantita).

Legge 'Risposte del modulo 1', per ogni riga non ancora promossa:
- se codice+categoria esistono gia' -> incrementa quantita
- altrimenti -> append nuova riga con id generato
Marca la riga promossa scrivendo 'si' nella colonna K (promosso).
"""
import json
import os
import re
from datetime import datetime, timezone

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
RESP_TAB = os.environ.get("RESP_TAB", "Risposte del modulo 1")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# colonne del tab risposte (come create dal Form)
RESP_COLS = ["timestamp", "categoria", "codice", "quantita", "descrizione",
             "interfaccia", "posizione", "note", "dataheet_url", "foto", "promosso"]
# colonne del tab inventario
INV_COLS = ["id", "categoria", "codice", "quantita", "descrizione", "interfaccia",
            "note", "datasheet_url", "posizione", "foto_drive_id", "foto_url",
            "ai_proposta", "ai_stato", "updated_at"]


def slug(s):
    s = (s or "").lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "na"


def extract_drive_id(url):
    if not url:
        return ""
    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url) or re.search(r"/d/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else ""


def sheets_svc():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def main():
    if not (SHEET_ID and SA_JSON):
        print("SHEET_ID o GOOGLE_SERVICE_ACCOUNT_JSON mancanti, skip promote.")
        return
    svc = sheets_svc()

    resp = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{RESP_TAB}'!A1:Z").execute().get("values", [])
    if not resp:
        print("Tab risposte vuoto.")
        return
    header = [h.strip() for h in resp[0]]
    idx = {h: i for i, h in enumerate(header)}

    inv = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z").execute().get("values", [])
    inv_header = [h.strip() for h in inv[0]] if inv else []
    inv_idx = {h: i for i, h in enumerate(inv_header)}

    # indici esistenti per deduplica (solo codice significativo)
    existing = {}
    for r in inv[1:]:
        g = lambda c: r[inv_idx[c]].strip() if c in inv_idx and inv_idx[c] < len(r) else ""
        cod, cat = g("codice"), g("categoria")
        if cod and cod != "?":
            existing[(cat, cod)] = r

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_append = []
    to_update = []  # (row_number, new_quantita)
    promoted = []   # row numbers da marcare

    for n, r in enumerate(resp[1:], 2):
        g = lambda c: r[idx[c]].strip() if c in idx and idx[c] < len(r) else ""
        if g("promosso").lower() in ("si", "sì", "true", "1"):
            continue
        cod, cat = g("codice"), g("categoria")
        foto_url = g("foto")
        if not (cod or g("descrizione") or foto_url):
            continue
        try:
            q = int(g("quantita") or 1)
        except ValueError:
            q = 1
        foto_id = extract_drive_id(foto_url)
        ts = g("timestamp") or now

        if cod and cod != "?" and (cat, cod) in existing:
            row = existing[(cat, cod)]
            cur = row[inv_idx["quantita"]] if inv_idx.get("quantita", 99) < len(row) else "0"
            try:
                new_q = int(cur or 0) + q
            except ValueError:
                new_q = q
            to_update.append((inv.index(row) + 1, new_q))
            promoted.append(n)
            print(f"riga {n}: incremento {cat}/{cod} -> {new_q}")
        else:
            pid = f"{slug(cat)}-{slug(cod)}-{n:02d}"
            to_append.append([pid, cat, cod, str(q), g("descrizione"), g("interfaccia"),
                              g("note"), g("dataheet_url"), g("posizione"), foto_id,
                              foto_url, "", "", ts])
            promoted.append(n)
            print(f"riga {n}: nuovo {pid}")

    if to_append:
        svc.spreadsheets().values().append(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": to_append}).execute()
    for row_num, new_q in to_update:
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!D{row_num}",
            valueInputOption="RAW", body={"values": [[str(new_q)]]}).execute()
    for n in promoted:
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"'{RESP_TAB}'!K{n}",
            valueInputOption="RAW", body={"values": [["si"]]}).execute()

    print(f"promosse {len(promoted)} risposte ({len(to_append)} nuove, {len(to_update)} incrementi)")


if __name__ == "__main__":
    main()
