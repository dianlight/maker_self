# Instant trigger (Apps Script → GitHub)

Avvia il workflow `sync.yml` subito quando:
- arriva una nuova risposta dal Form (`form-submitted`),
- cambi la colonna M (`ai_stato`) in `confermato`/`scartato` (`sheet-confirmed`).

## Installazione (una tantum, ~5 min)

1. Crea un token GitHub: https://github.com/settings/tokens/new
   - tipo: classico, scopes: `repo` + `workflow`. Copialo subito.
2. Apri `maker_self_inventario` → **Estensioni → Apps Script**.
3. Incolla il contenuto di `Trigger.gs`, salva.
4. Nelle **Impostazioni progetto (rotella) → Proprietà script** aggiungi:
   `GITHUB_TOKEN` = il token.
5. Nel pannello **Trigger (orologio) → Aggiungi trigger** (due trigger):
   - `onFormSubmitTrigger` — origine evento: **Da foglio di lavoro**, tipo: **All'invio del modulo**.
   - `onEditTrigger` — origine evento: **Da foglio di lavoro**, tipo: **Alla modifica**.
   Autorizza con il tuo account Google quando richiesto.
6. Prova: invia una risposta di test dal Form, il run parte entro ~30s.

## Note

- Gli edit via API (l'Action stessa) non attivano onEdit: nessun loop.
- Il token è leggibile da chi ha accesso in modifica allo Sheet: ok per uso personale.
- Fallback resta lo schedule giornaliero + avvio manuale.
