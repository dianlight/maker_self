# maker_self — Piano inventario laboratorio

## 1. Decisioni fissate
- Verità operativa: Google Sheet + foto su Drive.
- Mobile: Google Form per nuovi inserimenti con foto, app Sheets per ricerca e modifica diretta `quantita`.
- Sync una direzione sola: Sheet → git snapshot (Recommended). Niente scritture auto verso Sheet.
- Niente server SQL, niente PWA custom, niente addon HA, niente proxy `/v1` always-on in Fase 1.
- Uso da progetti: MCP `stdio` locale su Mac sopra snapshot git (`search_parts`, `get_part`, `stock_*` read-only / proposta).
- AI agnostica e configurabile via secrets/env (`LLM_PROVIDER`, `BASE_URL`, `MODEL`, `KEY`). In OpenCode si usano i modelli già configurati + MCP locale.
- Repo GitHub free: codice Action + snapshot + docs + snippet `opencode.json`.

## 2. Mappatura dati da Inventario.xlsx
Input attuale: ~12 sheet categoria (Sensori, Comunication, Displays, Motors/Hardware, IC Components, PU, LED, Componenti, MCU, Resistors, MCU Board, Circuits), ~250 voci.
Colonne origine: `Codice, Quantità, Decription, Interface, Note, Pinout/Datasheet, Position`.

Colonne Sheet target (unico Sheet `inventario` + colonna `categoria`, oppure uno sheet per categoria):
- `id` (slug stabile `categoria-codice-n`, generato da Action se vuoto)
- `categoria`
- `codice`
- `quantita` (intero)
- `descrizione`
- `interfaccia`
- `note`
- `datasheet_url`
- `posizione` (es. Sensor Box, Sensor Barrel, 20, 18, 480, MCU BOX)
- `foto_drive_id` / `foto_url`
- `ai_proposta` (JSON proposta vision-LLM)
- `ai_stato` (`da_verificare | confermato | scartato | retry`)
- `updated_at`

Snapshot git generato dalla Action:
- `snapshot/inventory.json`
- `snapshot/categories/<categoria>.yaml`
- `snapshot/thumbs/<id>.jpg`
- `schema.json` per validazione

## 3. Layout repo maker_self
- `.github/workflows/sync.yml` — schedulata + `workflow_dispatch`
- `scripts/sync_sheet_to_git.py` — lettura Sheet, export snapshot
- `scripts/ai_catalog.py` — catalogo foto nuove via LLM agnostico
- `scripts/import_excel_once.py` — import una tantum da `Inventario.xlsx`
- `mcp_server/` — MCP stdio locale (Python FastMCP) sopra `snapshot/`
- `docs/` — guida Sheet/Form/mobile + snippet `opencode.json`
- `snapshot/` — generato, committato dalla Action
- `PLAN.md` — questo file

## 4. Passi implementazione
1. Sheet template: creare Sheet `maker_self_inventario` con colonne §2 + validazione `quantita>=0`, vista filtro per `categoria`, ordinamento `posizione`.
2. Drive: cartella `maker_self_foto/` per upload Form, condivisa in lettura al service account.
3. Form mobile: `Nuovo componente` con campi `categoria, codice, quantita, descrizione, interfaccia, posizione, note, datasheet_url, foto`. Risposte in tab `FormResponses`, non scrive diretto in `inventario`.
4. GCP service account: creare progetto free, abilitare Sheets + Drive API, chiave JSON, condividere Sheet/Drive in lettura (e tab risposte in lettura). Salvare in GitHub Secrets come `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`, `DRIVE_FOLDER_ID`.
5. Secrets AI: `LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_KEY` in GitHub Secrets. Nessun provider hardcodato.
6. Scaffold repo: creare file §3 vuoti + `requirements.txt` + `schema.json` + `docs/opencode_mcp.json`.
7. Import una tantum: `scripts/import_excel_once.py` legge `Inventario.xlsx`, normalizza `Position` e `Pinout/Datasheet` in URL, genera `id`, popola Sheet via API oppure CSV per incolla. Verifica duplicati `codice+categoria`.
8. Action `sync.yml`: trigger `schedule` giornaliero + `workflow_dispatch`. Steps: checkout, setup Python, auth GCP da secrets, `sync_sheet_to_git.py`, `ai_catalog.py` solo su `foto` nuove con `ai_stato=da_verificare`, validazione schema, commit snapshot + thumbs se diff.
9. `ai_catalog.py`: per ogni foto nuova, chiama LLM vision con prompt fisso che restituisce solo JSON `{codice, categoria, descrizione, interfaccia, quantita, posizione, datasheet_url, note}`. Scrive `ai_proposta` + `ai_stato=da_verificare` su Sheet (unica scrittura Sheet consentita, solo colonne AI) e copia in snapshot. Utente conferma da Sheet copiando nei campi reali.
10. MCP locale: `mcp_server/server.py` stdio con tools `search_parts(q, categoria)`, `get_part(id)`, `stock_check(id)` sopra `snapshot/inventory.json`. Niente `stock_add` scrivente in Fase 1, solo proposta testuale. Config `opencode.json`: `{ "mcp": { "maker-self": { "type": "stdio", "command": ["python", "mcp_server/server.py"] } } }`.
11. Docs uso: `docs/mobile.md` (Form inserimento, Sheets ricerca/decremento, conferma AI), `docs/opencode.md` (snippet MCP + esempi query).
12. Backup: version history Google + git history snapshot. Nessun dato solo locale.

## 5. Criteri accettazione Fase 1
- Import Excel senza duplicati `id`, 12 categorie presenti.
- Da cellulare: nuovo pezzo via Form con foto in <2 min, ricerca e decremento `quantita` da app Sheets.
- Action verde a schedule + manuale, commit snapshot solo se diff reali.
- Foto nuova → `ai_proposta` compilata entro run successivo, mai sovrascritta se già `confermato`.
- Da opencode locale con MCP: `search_parts("BMP280")` trova voce snapshot in <2s.
- Cambio LLM solo via secrets, nessun codice da toccare.

## 6. Limiti noti / non fare
- Form è append-only, no update/scarico rapido con validazione — scarichi da app Sheets diretto (scelta utente).
- Sync bidirezionale esclusa per evitare conflitti Sheet↔git.
- Quote free: Sheets/Drive API, 15GB Drive, GitHub Actions minutes. Thumbs leggeri, foto originali restano su Drive.
- Niente PWA, HA, `/v1` provider custom in Fase 1. Rivalutare solo se Sheets non basta.

## 7. Estensioni future (fuori Fase 1)
- Proxy `/v1/chat/completions` OpenAI-compatibile con RAG snapshot per provider custom OpenCode.
- `stock_propose` → PR/commit proposta invece di sola lettura.
- Addon HA o PWA se serve uso offline / automazioni scorte.
