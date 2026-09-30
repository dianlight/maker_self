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
import base64
import json
import os
import re
import uuid
import requests

SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
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
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def sheet_tab_id(svc):
    meta = svc.spreadsheets().get(spreadsheetId=SHEET_ID,
                                  fields="sheets.properties").execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == SHEET_TAB:
            return s["properties"]["sheetId"]
    return 0


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
    tab_id = sheet_tab_id(svc)
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


if __name__ == "__main__":
    main()
