"""Catalogo foto nuove via LLM agnostico OpenAI-compatibile. Scrive solo colonne AI."""
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

PROMPT = ("Sei un catalogatore di componenti elettronici. Dalla foto restituisci SOLO JSON "
          "con chiavi: codice, categoria, descrizione, interfaccia, quantita, posizione, "
          "datasheet_url, note. Se incerto usa stringa vuota, quantita 1.")


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


def vision_llm(jpg_bytes):
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
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "temperature": 0.1,
    }
    r = requests.post(f"{BASE_URL}/chat/completions", headers=headers,
                      json=body, timeout=120)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    content = msg.get("content") or msg.get("reasoning") or ""
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
    for n, r in enumerate(vals[1:], 2):
        g = lambda c: r[idx[c]] if c in idx and idx[c] < len(r) else ""
        if g("foto_drive_id") and not g("ai_stato"):
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


if __name__ == "__main__":
    main()
