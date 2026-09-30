"""Catalogo foto nuove via LLM agnostico OpenAI-compatibile.

Scrive ai_proposta + ai_stato; se categoria e' vuota la riempie
diretto dalla proposta (normalizzata alle categorie note). Mai
sovrascritte se gia' presenti/confermato."""

CATEGORIES = ["sensori", "comunication", "displays", "motors-hardware",
              "ic-components", "pu", "led", "componenti", "mcu",
              "resistors", "mcu-board", "circuits"]

# proposta -> colonna inventario (solo celle vuote, mai sovrascritture)
PREFILL = [("codice", "codice"), ("categoria", "categoria"),
           ("descrizione", "descrizione"), ("interfaccia", "interfaccia"),
           ("quantita", "quantita"), ("posizione", "posizione"),
           ("note", "note"), ("datasheet_url", "datasheet_url")]

GRAY = {"red": 0.55, "green": 0.55, "blue": 0.55}

# colonne i cui valori possono essere ereditati dalla riga duplicata
CARRY_COLS = ["descrizione", "interfaccia", "note", "datasheet_url", "posizione"]


def norm_key(s):
    return (s or "").strip().lower()


def slug_key(s):
    return slug(s or "")


def to_int(v, default=0):
    try:
        return int(float(str(v or "").strip() or default))
    except (ValueError, TypeError):
        return default


def find_match(vals, idx, n, categoria, codice, skip=()):
    """Riga esistente con stessa categoria + stesso codice (normalizzati)."""
    cc, cd = norm_key(categoria), slug_key(codice)
    if not (cc and cd):
        return None
    ci, di = idx.get("categoria"), idx.get("codice")
    if ci is None or di is None:
        return None
    for m, r2 in enumerate(vals[1:], 2):
        if m == n or m in skip:
            continue
        c2 = norm_key(r2[ci]) if ci < len(r2) else ""
        d2 = slug_key(r2[di]) if di < len(r2) else ""
        if c2 == cc and d2 == cd:
            return m
    return None


def read_row(svc, n, cols=None):
    """Rilettura fresca riga n (valori). cols: lista indici o None=tutto."""
    if cols is None:
        rng = f"{SHEET_TAB}!A{n}:N{n}"
    else:
        rng = (f"{SHEET_TAB}!{chr(65 + min(cols))}{n}:"
               f"{chr(65 + max(cols))}{n}")
    r = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=rng).execute().get("values", [])
    return r[0] if r else []


def row_formats(svc, n, cols):
    """Colori testo celle (per distinguere grigio-AI da nero-utente)."""
    try:
        g = svc.spreadsheets().get(
            spreadsheetId=SHEET_ID,
            ranges=[f"{SHEET_TAB}!{chr(65 + min(cols))}{n}:{chr(65 + max(cols))}{n}"],
            includeGridData=True,
            fields="sheets.data.rowData.values("
                   "formattedValue,userEnteredFormat.textFormat.foregroundColor)").execute()
        return g["sheets"][0]["data"][0].get("rowData", [{}])[0].get("values", [])
    except Exception:
        return []


def merge_duplicate(svc, drv, inv_tab_id, resp_tab_id, vals, idx, n, row, prop, match):
    """Accorpa riga foto n nella riga esistente match. True se riuscita."""
    q[H] = to_int(row[idx["quantita"]]) if "quantita" in idx and idx["quantita"] < len(row) else 1
    if qtd <= 0:
        qtd = to_int(prop.get("quantita", 1), 1)
    tgt = read_row(svc, match)
    if not tgt:
        return False
    di = idx.get("quantita", 3)
    new_q = to_int(tgt[di] if di < len(tgt) else 0) + qtd
    svc.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!{chr(65 + di)}{match}",
        valueInputOption="RAW", body={"values": [[str(new_q)]]}).execute()
    # eredita info utili solo in celle vuote, preservando il colore
    cols = [idx[c] for c in CARRY_COLS if c in idx]
    if cols:
        cells = row_formats(svc, n, cols)
        reqs = []
        for k, ci in enumerate(cols):
            v = row[ci].strip() if ci < len(row) else ""
            t = tgt[ci].strip() if ci < len(tgt) else ""
            if not v or t:
                continue
            fg = (cells[k].get("userEnteredFormat", {}).get("textFormat", {})
                  .get("foregroundColor")) if k < len(cells) else None
            cell = {"userEnteredValue": {"stringValue": v}}
            if fg:
                cell["userEnteredFormat"] = {"textFormat": {"foregroundColor": fg}}
            reqs.append({"updateCells": {
                "range": {"sheetId": inv_tab_id, "startRowIndex": match - 1,
                          "endRowIndex": match, "startColumnIndex": ci,
                          "endColumnIndex": ci + 1},
                "rows": [{"values": [cell]}],
                "fields": "userEnteredValue,userEnteredFormat.textFormat.foregroundColor"}})
        if reqs:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()
    fid = (row[idx["foto_drive_id"]].strip()
           if "foto_drive_id" in idx and idx["foto_drive_id"] < len(row) else "")
    if fid and not trash_file(drv, fid):
        print(f"riga {n}: merge quantita ok, foto non cestinata (riprova manuale).")
        return False
    if resp_tab_id:
        for k in linked_responses(svc, fid, ""):
            delete_rows(svc, resp_tab_id, [k])
    print(f"riga {n}: duplicato di riga {match}, quantita -> {new_q}.")
    return True


def linked_responses(svc, fid, ts):
    """Righe risposte promosse e collegate (per foto o timestamp)."""
    try:
        resp = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{RESP_TAB}'!A1:Z").execute().get("values", [])
    except Exception:
        return []
    kill = []
    for m, rr in enumerate(resp[1:], 2):
        prom = rr[10].strip().lower() if len(rr) > 10 else ""
        if prom not in ("si", "sì", "true", "1"):
            continue
        fcell = rr[9] if len(rr) > 9 else ""
        tcell = rr[0].strip() if rr else ""
        if (fid and fid in fcell) or (ts and tcell == ts):
            kill.append(m)
    return kill


def delete_rows(svc, tab_id, rows):
    if rows:
        svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": [
            {"deleteDimension": {"range": {"sheetId": tab_id, "dimension": "ROWS",
                                           "startIndex": k - 1, "endIndex": k}}}
            for k in sorted(rows, reverse=True)]}).execute()
import base64
import json
import os
import re
import uuid
import requests

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
RESP_TAB = os.environ.get("RESP_TAB", "Risposte del modulo 1")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "")
KEY = os.environ.get("LLM_KEY", "")
PROVIDER = os.environ.get("LLM_PROVIDER", "")

PROMPT = ("Sei un catalogatore di componenti elettronici. Guarda bene la foto e "
          "restituisci SOLO JSON con chiavi: codice, categoria, descrizione, interfaccia, "
          "quantita, posizione, datasheet_url, note. categoria deve essere una di: "
          + ", ".join(CATEGORIES) + ". Descrivi sempre cio' che vedi in descrizione "
          "(tipo componente, quantita visibile, package, scritte leggibili) e fai la "
          "migliore ipotesi per codice e categoria; usa stringa vuota solo se davvero "
          "impossibile. quantita default 1.")


def slug(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or ""


def sheets_svc(creds_scopes):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=creds_scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def drive_svc():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def sheet_tab_id(svc, title):
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID,
                                  fields="sheets.properties").execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == title:
            return s["properties"]["sheetId"]
    return 0


def trash_file(drv, fid):
    """Cestina la foto Drive. True se ok o gia' sparita."""
    from googleapiclient.errors import HttpError
    try:
        drv.files().update(fileId=fid, body={"trashed": True}).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            print("foto gia' eliminata.")
            return True
        print(f"cestino foto fallito (HTTP {e.resp.status}, serve ruolo Editor sulla cartella).")
        return False
    except Exception as e:
        print(f"cestino foto fallito ({e}).")
        return False


def cleanup_confirmed(svc, drv, inv_tab_id, resp_tab_id, n, idx, row, vals):
    """Riga confermata: cestina foto (se non riusata), pulisci J/K,
    elimina righe risposte consumate e collegate."""
    g = lambda c: row[idx[c]].strip() if c in idx and idx[c] < len(row) else ""
    fid = g("foto_drive_id")
    ts = g("updated_at")
    if not (fid or ts):
        return
    if fid:
        refs = sum(1 for i, r2 in enumerate(vals[1:], 2)
                   if i != n and "foto_drive_id" in idx
                   and idx["foto_drive_id"] < len(r2)
                   and r2[idx["foto_drive_id"]].strip() == fid)
        if refs:
            print(f"riga {n}: foto riusata da {refs} righe, tengo il file.")
        elif not trash_file(drv, fid):
            return  # non orfanare nulla, riprova al prossimo run
        else:
            cj = chr(ord("A") + idx["foto_drive_id"])
            ck = chr(ord("A") + idx["foto_url"])
            svc.spreadsheets().values().update(
                spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!{cj}{n}:{ck}{n}",
                valueInputOption="RAW", body={"values": [["", ""]]}).execute()
            print(f"riga {n}: foto cestinata, riferimenti puliti.")
    if not resp_tab_id:
        return
    kill = linked_responses(svc, fid, ts)
    if kill:
        delete_rows(svc, resp_tab_id, kill)
        print(f"riga {n}: eliminate {len(kill)} righe risposte.")


def prefill_gray(svc, tab_id, n, idx, row, prop):
    """Riempie le celle A-I vuote con la proposta, testo grigio."""
    reqs = []
    for pkey, col in PREFILL:
        if col not in idx:
            continue
        v = str(prop.get(pkey, "") or "").strip()
        cur = row[idx[col]].strip() if idx[col] < len(row) else ""
        if not v or cur:
            continue
        reqs.append({"updateCells": {
            "range": {"sheetId": tab_id, "startRowIndex": n - 1, "endRowIndex": n,
                      "startColumnIndex": idx[col], "endColumnIndex": idx[col] + 1},
            "rows": [{"values": [{
                "userEnteredValue": {"stringValue": v},
                "userEnteredFormat": {"textFormat": {"foregroundColor": GRAY}}}]}],
            "fields": "userEnteredValue,userEnteredFormat.textFormat.foregroundColor"}})
    if reqs:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()
        print(f"riga {n}: precompilati {len(reqs)} campi in grigio.")


def finalize_black(svc, tab_id, n, idx, row):
    """Conferma: riporta il testo A-I a nero (solo celle valorizzate)."""
    reqs = []
    for _, col in PREFILL:
        if col not in idx:
            continue
        if not (idx[col] < len(row) and row[idx[col]].strip()):
            continue
        reqs.append({"updateCells": {
            "range": {"sheetId": tab_id, "startRowIndex": n - 1, "endRowIndex": n,
                      "startColumnIndex": idx[col], "endColumnIndex": idx[col] + 1},
            "rows": [{"values": [{
                "userEnteredFormat": {"textFormat": {"foregroundColor": {}}}}]}],
            "fields": "userEnteredFormat.textFormat.foregroundColor"}})
    if reqs:
        try:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=SHEET_ID, body={"requests": reqs}).execute()
            print(f"riga {n}: confermata, testo nero.")
        except Exception as e:
            print(f"riga {n}: reset colore fallito ({e}).")


def vision_llm(jpg_bytes):
    from PIL import Image
    import io
    try:
        img = Image.open(io.BytesIO(jpg_bytes)).convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        jpg_bytes = buf.getvalue()
    except Exception:
        pass
    b64 = base64.b64encode(jpg_bytes).decode()
    headers = {"Content-Type": "application/json"}
    if KEY:
        headers["Authorization"] = f"Bearer {KEY}"
    # opencode-go (zen/go) routes chat requests by session
    if PROVIDER == "opencode-go":
        headers["x-opencode-session"] = uuid.uuid4().hex
    body = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT + " Output: ONLY the JSON object, no other text."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    r = requests.post(f"{BASE_URL}/chat/completions", headers=headers,
                      json=body, timeout=180)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = (msg.get("content") or msg.get("reasoning")
               or msg.get("reasoning_content") or "")
    # keep only the JSON object if the model wrapped it in prose or fences
    m = re.search(r"\{.*\}", content, re.S)
    return m.group(0) if m else content.strip()


def main():
    if not (SHEET_ID and SA_JSON and BASE_URL and MODEL):
        print("secrets mancanti (Sheet o LLM), skip AI.")
        return
    svc = sheets_svc(["https://www.googleapis.com/auth/spreadsheets"])
    vals = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z").execute().get("values", [])
    if not vals:
        print("Sheet vuoto.")
        return
    header = [h.strip() for h in vals[0]]
    idx = {h: i for i, h in enumerate(header)}
    if "foto_drive_id" not in idx or "ai_stato" not in idx:
        print("colonne foto_drive_id/ai_stato mancanti.")
        return
    drv = drive_svc()
    tab_id = sheet_tab_id(svc, SHEET_TAB)
    resp_tab_id = sheet_tab_id(svc, RESP_TAB)
    pending_deletes = []
    for n, r in enumerate(vals[1:], 2):
        g = lambda c: r[idx[c]] if c in idx and idx[c] < len(r) else ""
        stato = g("ai_stato").strip()
        if g("foto_drive_id") and not stato:
            fid = g("foto_drive_id").strip()
            try:
                data = drv.files().get_media(fileId=fid).execute()
                proposta = vision_llm(data)
            except Exception as e:
                print(f"riga {n}: AI fallita ({e}), salto.")
                continue
            try:
                prop = json.loads(proposta)
            except (json.JSONDecodeError, AttributeError):
                prop = {}
            match = (find_match(vals, idx, n, prop.get("categoria", ""),
                                prop.get("codice", ""), skip=pending_deletes)
                     if prop else None)
            if match is not None and merge_duplicate(
                    svc, drv, tab_id, resp_tab_id, vals, idx, n, r, prop, match):
                col_st = chr(ord("A") + idx["ai_stato"])
                svc.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!{col_st}{n}",
                    valueInputOption="RAW", body={"values": [["confermato"]]}).execute()
                pending_deletes.append(n)
                continue
            col_ai = chr(ord("A") + idx["ai_proposta"])
            col_st = chr(ord("A") + idx["ai_stato"])
            svc.spreadsheets().values().batchUpdate(
                spreadsheetId=SHEET_ID, body={"valueInputOption": "RAW", "data": [
                    {"range": f"{SHEET_TAB}!{col_ai}{n}", "values": [[proposta[:4000]]]},
                    {"range": f"{SHEET_TAB}!{col_st}{n}", "values": [["da_verificare"]]},
                ]}).execute()
            print(f"riga {n}: proposta AI scritta.")
            try:
                prop = json.loads(proposta)
            except (json.JSONDecodeError, AttributeError):
                prop = {}
            if prop:
                prefill_gray(svc, tab_id, n, idx, r, prop)
        elif stato == "da_verificare" and g("ai_proposta"):
            try:
                prop = json.loads(g("ai_proposta"))
            except (json.JSONDecodeError, AttributeError):
                continue
            if prop:
                prefill_gray(svc, tab_id, n, idx, r, prop)
        elif stato == "confermato":
            finalize_black(svc, tab_id, n, idx, r)
            cleanup_confirmed(svc, drv, tab_id, resp_tab_id, n, idx, r, vals)
    if pending_deletes:
        # numbering del loop resta valido: eliminazioni solo a fine run
        delete_rows(svc, tab_id, pending_deletes)
        print(f"eliminate {len(pending_deletes)} righe duplicate.")


if __name__ == "__main__":
    main()
