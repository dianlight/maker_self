# Columns (`inventario` tab)

Order from `scripts/sync_sheet_to_git.py` EXPECTED:

`id, categoria, codice, quantita, descrizione, interfaccia, note, datasheet_url, datasheet_last_check, posizione, foto_drive_id, foto_url, ai_proposta, ai_stato, updated_at`

## Rules

- Required by `schema.json`: `id, categoria, codice, quantita` (`quantita` int >= 0).
- `id`: slug `categoria-codice-n` or `slug(categoria)-slug(codice)-NN`. Must be unique. Reuse `slug()` from `sync_sheet_to_git`.
- `categoria`: lowercase kebab preferred (`ic-components`, `componenti`, `sensori`). Keep existing spelling when incrementing.
- `codice`: exact part code as user typed (`1N4001`, `INA226`). Case preserved.
- `quantita`: int, clamp >= 0. Bulk add = increment existing or set for new.
- `descrizione`: functional + package. Never leave marketing fluff.
- `interfaccia`: analogica/digitale/I2C/SPI/through-hole/SMD or empty.
- `datasheet_url`: http(s) only, validated. `datasheet_last_check` set by `check_datasheets.py`, do not hand-edit.
- `posizione`: box name verbatim (`Diod Box`, `Sensor Box`). New values also go to the Google Form dropdown (see SKILL.md §4).
- `foto_drive_id`/`foto_url`: Form upload id vs official link. Bulk skill sets `foto_url` only; uploads stay on Drive.
- `ai_proposta`/`ai_stato`: leave empty on manual bulk rows. Values: ``, `da_verificare`, `confermato`, `scartato`, `retry`.
- `updated_at`: UTC ISO-8601 at write time.
- Empty-row rule from `to_parts`: skip rows with no `id/codice/descrizione/foto_drive_id`.
- Dedup key for bulk: `(categoria, codice)` exact match after strip. `codice == "?"` never dedups.
