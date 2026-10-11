# Instant trigger + review actions (Apps Script → GitHub)

Avvia il workflow `sync.yml` subito quando:
- arriva una nuova risposta dal Form (`form-submitted`),
- cambi `ai_stato` in `inventario` in `confermato`/`scartato`/`retry` (`sheet-confirmed`),
- usi le checkbox di review in `Schede Inventario` (`sheet-confirmed`, dispatch esplicito dallo script).

## Installazione (una tantum, ~5 min)

1. Crea un token GitHub: https://github.com/settings/tokens/new
   - tipo: classico, scopes: `repo` + `workflow`. Copialo subito.
2. Apri `maker_self_inventario` → **Estensioni → Apps Script**.
3. Incolla il contenuto di `Trigger.gs` **e** di `Actions.gs` (il progetto Apps Script appiattisce i file: servono entrambi), salva.
4. Nelle **Impostazioni progetto (rotella) → Proprietà script** aggiungi:
   `GITHUB_TOKEN` = il token.
5. Nel pannello **Trigger (orologio) → Aggiungi trigger** (due trigger):
   - `onFormSubmitTrigger` — origine evento: **Da foglio di lavoro**, tipo: **All'invio del modulo**.
   - `onEditTrigger` — origine evento: **Da foglio di lavoro**, tipo: **Alla modifica**.
   Autorizza con il tuo account Google quando richiesto.
6. Prova: invia una risposta di test dal Form, il run parte entro ~30s.

## Colonne azione in `Schede Inventario`

Nella tabella `Inventario` dello sheet `Schede Inventario` aggiungi 3 colonne
con intestazione esatta (riga 1) e tipo Checkbox (**Inserisci → Casella di controllo**):

- `Invalid Photo` → cancella `foto_drive_id` + `foto_url` in `inventario`, `ai_stato=retry`
- `Invalid Datasheet` → cancella `datasheet_url` + `datasheet_last_check`, `ai_stato=retry`
- `Accept` → solo se `ai_stato=da_verificare`, allora `=confermato`; altrimenti nessun write + toast

Il link tra le tabelle è la colonna `Id`: lo script cerca l'`id` in `inventario`
lookup per valore (funziona anche con filtri/ordinamenti). La checkbox si
riabbassa da sola dopo ogni azione. Nessun trigger aggiuntivo: basta quello
`Alla modifica` su `onEditTrigger`.

## Accept disabilitato su righe `confermato`

Sheets non ha checkbox "disabilitate" per riga: `syncAcceptBoxes()` (in
`Actions.gs`) le emula. Confronta `Id` con `ai_stato` di `inventario` (col M,
risolta per nome) e per ogni riga `confermato` **rimuove** la checkbox di
`Accept` (cella vuota + grigia); sulle altre la (ri)aggiunge. Il blocco vero
resta il controllo in `handleReviewAction` (`Accept` solo da `da_verificare`).

Esecuzione una tantum: seleziona `syncAcceptBoxes` → ▶ Esegui. Poi aggiungi un
trigger orario (**orologio → Aggiungi trigger**: `syncAcceptBoxes`, origine
**Temporizzato**, ogni ora): serve perché le scritture API dell'Action
(`confermato` automatico) non attivano `onEdit`.

## Foto cliccabile in `Schede Inventario`

Hover che ingrandisce non esiste in Sheets. Alternativa scelta: thumbnail piccola
cliccabile che apre la foto grande in una nuova scheda. Incolla in `A2` della
tabella `Inventario` (locale italiano, separatore `;`) e trascina in basso:

```excel
=LET(id;$B2;url;IFERROR(VLOOKUP(id;inventario!$A:$Z;MATCH("foto_url";inventario!$1:$1;0);0);"");fid;IFERROR(VLOOKUP(id;inventario!$A:$Z;MATCH("foto_drive_id";inventario!$1:$1;0);0);"");full;IF(url<>"";url;IF(fid<>"";"https://drive.google.com/uc?export=view&id="&fid;""));IF(full="";"";HYPERLINK(full;IMAGE(full;1))))
```

(Località con virgola: sostituisci ogni `;` con `,`.) Altezza righe consigliata
80–100px, larghezza colonna ~120px. Click: seleziona la cella, poi click sul
chip del link (su mobile basta il tap). Se la cella resta vuota, la riga non ha
foto in `inventario`.

## Note

- Gli edit via API (l'Action stessa) non attivano onEdit: nessun loop.
- Il token è leggibile da chi ha accesso in modifica allo Sheet: ok per uso personale.
- Fallback resta lo schedule giornaliero + avvio manuale.
