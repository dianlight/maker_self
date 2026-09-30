# Inserimento da cellulare
1. Apri il Google Form `Nuovo componente`, compila categoria/codice/quantita/posizione e scatta foto.
2. Invia: finisce nel tab risposte, non tocca `inventario` diretto.
3. La Action giornaliera propone `ai_proposta` + `ai_stato=da_verificare` in `inventario`.
4. Conferma: i campi suggeriti dall'AI sono gia' precompilati in grigio.
   Controllali, correggi se serve e metti `ai_stato=confermato`: il testo torna nero da solo al run successivo.

# Decremento / prelievo
- Scelta fissata: modifica diretta `quantita` da app Google Sheets.
- Filtra per `categoria` o cerca `codice`, abbassa `quantita`, fatto.
- Lo snapshot git si aggiorna al run successivo, MCP legge lo snapshot.
