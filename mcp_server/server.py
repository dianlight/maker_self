"""MCP stdio locale: lettura snapshot + write tools su Google Sheet."""
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0", "google-auth>=2.0", "google-api-python-client>=2.0", "PyYAML>=6.0"]
# ///
import json
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import yaml
from mcp.server.mcpserver import MCPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(BASE, "snapshot", "inventory.json")

sys.path.insert(0, os.path.join(BASE, "scripts"))
from sync_sheet_to_git import slug, to_parts  # noqa: E402 — reuse parsing logic

mcp = MCPServer("maker-self")

# --- env ---------------------------------------------------------------------
SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_TAB = os.environ.get("SHEET_TAB", "inventario")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
SA_PATH = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "")


def _resolve_sa() -> dict:
    if SA_JSON:
        return json.loads(SA_JSON)
    if SA_PATH:
        p = SA_PATH if os.path.isabs(SA_PATH) else os.path.join(BASE, SA_PATH)
        with open(p) as f:
            return json.load(f)
    p = os.path.join(BASE, "service-account.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    raise RuntimeError(
        "Google credentials mancanti: set GOOGLE_SERVICE_ACCOUNT_JSON, "
        "GOOGLE_SERVICE_ACCOUNT_JSON_PATH oppure metti service-account.json nella root.")


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def sheets_svc():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        _resolve_sa(), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def fetch_values() -> list:
    svc = sheets_svc()
    res = svc.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:Z").execute()
    return res.get("values", [])


def refresh_snapshot(values: list) -> tuple:
    parts = to_parts(values)
    os.makedirs(os.path.join(BASE, "snapshot", "categories"), exist_ok=True)
    os.makedirs(os.path.join(BASE, "snapshot", "thumbs"), exist_ok=True)
    with open(SNAP, "w") as f:
        json.dump(parts, f, ensure_ascii=False, indent=2)
    by_cat = {}
    for p in parts:
        by_cat.setdefault(p["categoria"] or "misc", []).append(p)
    for cat, items in by_cat.items():
        with open(os.path.join(BASE, "snapshot", "categories", f"{slug(cat)}.yaml"), "w") as f:
            yaml.safe_dump(items, f, allow_unicode=True, sort_keys=False)
    return len(parts), len(by_cat)


def find_row(values: list, part_id: str):
    """Ritorna (sheet_row_number, header_idx_dict) o (-1, idx).
    Matcha per id esplicito oppure per l'id generato da to_parts
    (slug(categoria)-slug(codice)-NN) quando la cella id e' vuota."""
    if not values:
        return -1, {}
    header = [h.strip() for h in values[0]]
    idx = {h: i for i, h in enumerate(header)}

    def g(r, c):
        return r[idx[c]].strip() if c in idx and idx[c] < len(r) else ""

    for sheet_row, r in enumerate(values[1:], 2):
        sid = g(r, "id")
        if sid:
            if sid == part_id:
                return sheet_row, idx
            continue
        categoria, codice = g(r, "categoria"), g(r, "codice")
        gen = f"{slug(categoria)}-{slug(codice)}-{sheet_row - 1:02d}"
        if gen == part_id and (g(r, "codice") or g(r, "descrizione") or g(r, "foto_drive_id")):
            return sheet_row, idx
    return -1, idx


def write_cells(svc, updates: list):
    """updates: [(sheet_row, col_idx, value), ...] — unico batchUpdate RAW."""
    data = [{"range": f"{SHEET_TAB}!{col_letter(c)}{row}", "values": [[val]]}
            for row, c, val in updates]
    svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "RAW", "data": data}).execute()


# --- read tools (originali) ---------------------------------------------------
def load():
    try:
        with open(SNAP) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


@mcp.tool()
def search_parts(q: str = "", categoria: str = "") -> str:
    """Cerca parti per testo libero e/o categoria. Ritorna JSON lista (max 20)."""
    ql, cl = q.lower(), categoria.lower()
    out = [p for p in load()
           if (not ql or ql in json.dumps(p, ensure_ascii=False).lower())
           and (not cl or cl in (p.get("categoria") or "").lower())][:20]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def get_part(id: str) -> str:
    """Dettaglio parte per id."""
    for p in load():
        if p.get("id") == id:
            return json.dumps(p, ensure_ascii=False, indent=2)
    return json.dumps({"error": "not found"})


@mcp.tool()
def stock_check(id: str) -> str:
    """Quantita + posizione, sola lettura."""
    for p in load():
        if p.get("id") == id:
            return json.dumps({k: p.get(k) for k in
                               ("id", "codice", "quantita", "posizione", "categoria")},
                              ensure_ascii=False)
    return json.dumps({"error": "not found"})


# --- write tools (nuovi) -----------------------------------------------------
@mcp.tool()
def update_inventory() -> str:
    """Riscrive snapshot/inventory.json e le category yaml dallo Sheet (sola lettura Sheet)."""
    if not SHEET_ID:
        return json.dumps({"error": "SHEET_ID non configurato"})
    try:
        parts, cats = refresh_snapshot(fetch_values())
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"status": "ok", "parts": parts, "categories": cats})


@mcp.tool()
def stock_use(id: str, qty: int) -> str:
    """Rimuove qty dalla quantita della parte id sullo Sheet (clamp a 0). Aggiorna snapshot."""
    if not SHEET_ID:
        return json.dumps({"error": "SHEET_ID non configurato"})
    if qty <= 0:
        return json.dumps({"error": "qty deve essere > 0", "qty": qty})
    values = fetch_values()
    sheet_row, idx = find_row(values, id)
    if sheet_row == -1:
        return json.dumps({"error": "not found", "id": id})
    if "quantita" not in idx:
        return json.dumps({"error": "colonna quantita mancante nello Sheet"})
    cur_row = values[sheet_row - 1]
    cur = cur_row[idx["quantita"]].strip() if idx["quantita"] < len(cur_row) else ""
    try:
        cur_q = int(cur or 0)
    except ValueError:
        cur_q = 0
    new_q = max(cur_q - qty, 0)
    ts = datetime.now(timezone.utc).isoformat()
    svc = sheets_svc()
    updates = [(sheet_row, idx["quantita"], new_q)]
    if "updated_at" in idx:
        updates.append((sheet_row, idx["updated_at"], ts))
    write_cells(svc, updates)
    parts, cats = refresh_snapshot(fetch_values())
    return json.dumps({"id": id, "row": sheet_row, "before": cur_q, "after": new_q,
                       "snapshot": {"parts": parts, "categories": cats}})


@mcp.tool()
def set_part(id: str, categoria: Optional[str] = None, codice: Optional[str] = None,
             quantita: Optional[str] = None, descrizione: Optional[str] = None,
             interfaccia: Optional[str] = None, note: Optional[str] = None,
             datasheet_url: Optional[str] = None, posizione: Optional[str] = None) -> str:
    """Aggiorna i campi utente della parte id sullo Sheet. Campi non passati restano invariati.
    Campi modificabili: categoria, codice, quantita, descrizione, interfaccia, note, datasheet_url, posizione."""
    if not SHEET_ID:
        return json.dumps({"error": "SHEET_ID non configurato"})
    values = fetch_values()
    sheet_row, idx = find_row(values, id)
    if sheet_row == -1:
        return json.dumps({"error": "not found", "id": id})
    fields = {"categoria": categoria, "codice": codice, "descrizione": descrizione,
              "interfaccia": interfaccia, "note": note, "datasheet_url": datasheet_url,
              "posizione": posizione}
    if quantita is not None:
        try:
            fields["quantita"] = max(int(quantita), 0)
        except ValueError:
            return json.dumps({"error": "quantita non valida", "quantita": quantita})
    cur_row = values[sheet_row - 1]
    updates = []
    for col, val in fields.items():
        if val is None or col not in idx:
            continue
        cur = cur_row[idx[col]].strip() if idx[col] < len(cur_row) else ""
        if col == "quantita":
            try:
                cur_val = int(cur or 0)
            except ValueError:
                cur_val = 0
            if cur_val == val:
                continue
        elif cur == (val or ""):
            continue
        updates.append((sheet_row, idx[col], val))
    if not updates:
        return json.dumps({"id": id, "row": sheet_row, "status": "unchanged"})
    ts = datetime.now(timezone.utc).isoformat()
    if "updated_at" in idx:
        updates.append((sheet_row, idx["updated_at"], ts))
    svc = sheets_svc()
    write_cells(svc, updates)
    parts, cats = refresh_snapshot(fetch_values())
    return json.dumps({"id": id, "row": sheet_row, "status": "updated",
                       "changes": len(updates) - (1 if "updated_at" in idx else 0),
                       "snapshot": {"parts": parts, "categories": cats}})


@mcp.tool()
def stock_add_batch(items: str, dry_run: bool = False) -> str:
    """Aggiunge molte parti in una volta sullo Sheet (bulk mode).

    items: JSON array di oggetti {categoria, codice, quantita, descrizione?,
      interfaccia?, note?, datasheet_url?, posizione?, foto_url?}.
    Se (categoria, codice) esiste gia' (strip exact, codice != '?'),
    incrementa quantita; altrimenti appende una nuova riga con id slug unico.
    dry_run=true: solo preview, nessuna scrittura. Aggiorna snapshot alla fine.
    """
    import re as _re

    if not SHEET_ID:
        return json.dumps({"error": "SHEET_ID non configurato"})
    try:
        parsed = json.loads(items) if isinstance(items, str) else items
    except Exception as e:
        return json.dumps({"error": f"items non e' JSON valido: {e}"})
    if not isinstance(parsed, list) or not parsed:
        return json.dumps({"error": "items deve essere una lista non vuota"})
    values = fetch_values()
    if not values:
        return json.dumps({"error": "Sheet vuoto o illeggibile"})
    header = [h.strip() for h in values[0]]
    idx = {h: i for i, h in enumerate(header)}
    for col in ("categoria", "codice", "quantita"):
        if col not in idx:
            return json.dumps({"error": f"colonna {col} mancante nello Sheet"})
    ts = datetime.now(timezone.utc).isoformat()

    def _g(row, col):
        return row[idx[col]].strip() if idx[col] < len(row) else ""

    # indice esistenti per incremento
    existing_row = {}
    existing_ids = set()
    for n, r in enumerate(values[1:], 2):
        rid = _g(r, "id") if "id" in idx else ""
        if rid:
            existing_ids.add(rid)
        cod, cat = _g(r, "codice"), _g(r, "categoria")
        if cod and cod != "?" and (cat, cod) not in existing_row:
            existing_row[(cat, cod)] = n

    def _unique_id(base):
        base = _re.sub(r"[^a-z0-9]+", "-", (base or "").lower()).strip("-") or "noid"
        cand, k = base, 2
        while cand in existing_ids:
            cand = f"{base}-{k}"
            k += 1
        existing_ids.add(cand)
        return cand

    def _norm(it):
        cat = str(it.get("categoria") or "").strip()
        cod = str(it.get("codice") or "").strip()
        try:
            q = max(int(it.get("quantita") or 0), 0)
        except (ValueError, TypeError):
            raise ValueError(f"quantita non valida per {cod}: {it.get('quantita')!r}")
        if not (cod or (it.get("descrizione") or "").strip()):
            raise ValueError("ogni item vuole almeno codice o descrizione")
        return {
            "categoria": cat, "codice": cod, "quantita": q,
            "descrizione": str(it.get("descrizione") or "").strip(),
            "interfaccia": str(it.get("interfaccia") or "").strip(),
            "note": str(it.get("note") or "").strip(),
            "datasheet_url": str(it.get("datasheet_url") or "").strip(),
            "posizione": str(it.get("posizione") or "").strip(),
            "foto_url": str(it.get("foto_url") or "").strip(),
        }

    try:
        norms = [_norm(it) for it in parsed]
    except ValueError as e:
        return json.dumps({"error": str(e)})

    increments = []  # (row, new_q, codice)
    appends = []     # full Sheet rows
    for it in norms:
        key = (it["categoria"], it["codice"])
        if it["codice"] and it["codice"] != "?" and key in existing_row:
            row_n = existing_row[key]
            cur = values[row_n - 1][idx["quantita"]].strip() \
                if idx["quantita"] < len(values[row_n - 1]) else ""
            try:
                new_q = int(cur or 0) + it["quantita"]
            except ValueError:
                new_q = it["quantita"]
            increments.append({"row": row_n, "codice": it["codice"], "after": new_q})
        else:
            pid = _unique_id(f"{slug(it['categoria'])}-{slug(it['codice']) or 'row'}")
            row = [""] * len(header)

            def _set(c, v):
                if c in idx:
                    row[idx[c]] = v

            _set("id", pid)
            _set("categoria", it["categoria"])
            _set("codice", it["codice"])
            _set("quantita", str(it["quantita"]))
            _set("descrizione", it["descrizione"])
            _set("interfaccia", it["interfaccia"])
            _set("note", it["note"])
            _set("datasheet_url", it["datasheet_url"])
            _set("posizione", it["posizione"])
            _set("foto_url", it["foto_url"])
            _set("updated_at", ts)
            appends.append({"id": pid, "codice": it["codice"], "row_values": row})

    if dry_run:
        return json.dumps({"status": "preview", "increments": increments,
                           "appends": [{"id": a["id"], "codice": a["codice"]} for a in appends]},
                          ensure_ascii=False)
    svc = sheets_svc()
    for inc in increments:
        upd = [(inc["row"], idx["quantita"], str(inc["after"]))]
        if "updated_at" in idx:
            upd.append((inc["row"], idx["updated_at"], ts))
        write_cells(svc, upd)
    if appends:
        last = 1
        for i, r in enumerate(values, 1):
            if any((c or "").strip() for c in r):
                last = i
        nxt = last + 1
        meta = svc.spreadsheets().get(
            spreadsheetId=SHEET_ID, fields="sheets.properties").execute()
        props = next(s["properties"] for s in meta["sheets"]
                     if s["properties"]["title"] == SHEET_TAB)
        need = nxt + len(appends) - 1
        if need > props["gridProperties"]["rowCount"]:
            svc.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={"requests": [
                {"appendDimension": {"sheetId": props["sheetId"], "dimension": "ROWS",
                                     "length": need - props["gridProperties"]["rowCount"]}}]}).execute()
        svc.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A{nxt}",
            valueInputOption="RAW",
            body={"values": [a["row_values"] for a in appends]}).execute()
    parts, cats = refresh_snapshot(fetch_values())
    return json.dumps({"status": "ok", "incremented": len(increments), "appended": len(appends),
                       "increments": increments,
                       "appends": [{"id": a["id"], "codice": a["codice"]} for a in appends],
                       "snapshot": {"parts": parts, "categories": cats}}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
