---
name: Inventory Bulk
description: Add or fix many inventory components at once, or add a new posizione/box. Use whenever the user says add in X Box a list of codes with quantities, fix icon/description/datasheet of a part, add a new box/position, or asks to update stock, Sheet, snapshot and Form choices together.
---

# Inventory Bulk

Bulk add / fix for maker_self. Source of truth is the Google Sheet tab `inventario`, mirrored to `snapshot/inventory.json` + `snapshot/categories/*.yaml` via MCP `update_inventory` or `scripts/sync_sheet_to_git.py`.

## 0. Clarify before writing

Parse the request into rows of `categoria, codice, quantita, posizione`. If anything is unclear, STOP and ask. Typical ambiguities:

- "Diod Box" — is that `posizione` (box) or `categoria`? Default: box name goes to `posizione`, infer `categoria` from parts (e.g. diodes -> `componenti` or existing category of similar codes). Confirm.
- Missing qty — default 1 each unless user said "10 each". Confirm.
- "icon" means `foto_url` (official photo link) and/or `foto_drive_id`. Confirm which.
- "description is wrong" — propose new `descrizione` via web search, never invent specs.
- New box/position — confirm exact spelling (e.g. `Diod Box` vs `Diode Box`) because Form choices and existing `posizione` values are case-sensitive.

Never guess quantities, box names, or datasheets silently.

## 1. Check existing stock

For each `codice`:

1. Call MCP `search_parts(q=codice)`.
2. If found, call `get_part(id)` / `stock_check(id)` to see `quantita` + `posizione`.
3. Decide: increment qty if same `categoria+codice` exists, else append new row.
4. Read `references/columns.md` for required columns and defaults.

## 2. Enrich missing columns (propose-then-confirm)

Required: `id, categoria, codice, quantita`. Complete the rest when missing:

- `descrizione`: short functional description + package when known.
- `interfaccia`: e.g. analogica/digitale/I2C/through-hole/SMD, empty if unknown.
- `datasheet_url`: must be http(s), HEAD/GET valid. Prefer manufacturer PDF. Use `websearch` + `scripts/ai_find_datasheets.py` logic.
- `foto_url`: official product photo link (no upload). Use `websearch` + `scripts/ai_find_photos.py` logic.
- `posizione`: the box name verbatim.
- `note`: origin / bulk-add date.
- `updated_at`: UTC ISO, set at write time.
- Never touch `ai_proposta/ai_stato` except leaving them empty for new rows.

Show the user a table of proposed rows and wait for explicit confirm. For fixes (e.g. INA226 icon+description), show before/after per field.

## 3. Write to Sheet via MCP

- Single fix: `set_part(id, descrizione=..., datasheet_url=..., posizione=..., ...)`.
- Bulk add: `stock_add_batch(items=[{categoria, codice, quantita, descrizione, interfaccia, note, datasheet_url, posizione, foto_url}, ...])`. It increments qty on `categoria+codice` match, else appends with slug id. Dry-run first with `dry_run=true` to preview.
- Then `update_inventory()` to rewrite `snapshot/inventory.json` + category yamls. Verify counts in the tool response.
- Never write `quantita` negative; clamp to 0. `quantita` arrives as int.

## 4. Sync Form choices for new box/position

When `posizione` or `categoria` contains a value not present in the snapshot (e.g. brand-new `Diod Box`):

1. Run `python scripts/update_form_choices.py --list` to see current Form dropdowns (needs `FORM_ID` env or `--form-id`).
2. Run `python scripts/update_form_choices.py --add-posizione "Diod Box" --add-categoria "<cat>"` to append missing choices to the Google Form `Nuovo componente`. This uses the Forms API; the Form must be shared with the service account as Editor, or run with a user OAuth token.
3. If `FORM_ID` is missing or API fails, output exact manual steps: open Form > edit `posizione`/`categoria` question > add option > save. Never silently skip — tell the user the Form is out of sync.
4. `Trigger.gs` needs no change: `onFormSubmitTrigger` fires on any new response regardless of choices.

## 5. Final checks

- `python scripts/sync_sheet_to_git.py` parity: snapshot matches Sheet (or `update_inventory` response shows expected parts/categories).
- `schema.json` required fields present, `quantita >= 0`.
- Report: rows added / incremented / fixed, Form choices updated or manual TODO, snapshot counts.
- If the daily `datasheet-check.yml` / `sync.yml` will re-validate column H, mention it — do not duplicate its work.

## Guardrails

- Sheet is truth; snapshot is derived. Never edit `snapshot/` by hand.
- One batchUpdate per write path; reuse `write_cells` / `refresh_snapshot` helpers in `mcp_server/server.py`.
- Keep questions short and single-purpose; ask before any Sheet/Form write when input was ambiguous.
