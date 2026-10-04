# Inserimento da cellulare
1. Apri il Google Form `Nuovo componente`, compila categoria/codice/quantita/posizione e scatta foto.
2. Invia: finisce nel tab risposte, non tocca `inventario` diretto.
3. La Action giornaliera propone `ai_proposta` + `ai_stato=da_verificare` in `inventario`.
4. Conferma: i campi suggeriti dall'AI sono gia' precompilati in grigio.
   Controllali, correggi se serve e metti `ai_stato=confermato`: il testo torna nero da solo al run successivo.
   Alla conferma la foto viene spostata in maker_self_foto e la riga risposta eliminata, tutto automatico.
   Se la foto ritrae un pezzo gia' presente (stesso codice e categoria), la quantita viene accorpata da sola e la riga duplicata sparisce.
5. Riclassificare una riga sbagliata: metti `ai_stato=retry`.
   Alla run successiva l'AI rilegge la riga (codice, descrizione, note... + foto se c'e'),
   non le risposte del modulo, e riscrive `ai_proposta` con la classificazione corretta;
   celle vuote/grigie aggiornate, celle nere (tue) intatte.
   La riga torna `da_verificare`: controlla, copia, conferma.

# Decremento / prelievo
- Scelta fissata: modifica diretta `quantita` da app Google Sheets.
- Filtra per `categoria` o cerca `codice`, abbassa `quantita`, fatto.
- Lo snapshot git si aggiorna al run successivo, MCP legge lo snapshot.
