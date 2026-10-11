/* maker_self review actions — checkbox della tabella `Inventario`
 * nello sheet `Schede Inventario`, scrittura su `inventario`.
 *
 * Colonne azione (checkbox, riga 1 = intestazione):
 *   E = "Invalid Photo"    -> cancella foto_drive_id + foto_url, ai_stato=retry
 *   F = "Invalid Datasheet"-> cancella datasheet_url + datasheet_last_check, ai_stato=retry
 *   G = "Accept"           -> solo se ai_stato=da_verificare, allora =confermato
 *
 * Il link tra le due tabelle e' la colonna `Id` (stessa riga in
 * `Schede Inventario`, lookup per valore in `inventario!id`).
 * Le colonne di `inventario` sono risolte per nome header, mai per lettera.
 */
var REVIEW_SHEET = "Schede Inventario";
var INV_SHEET = "inventario";

var ACT_PHOTO = "invalid photo";
var ACT_DATASHEET = "invalid datasheet";
var ACT_ACCEPT = "accept";

function normHeader(v) {
  return String(v === undefined || v === null ? "" : v).trim().toLowerCase();
}

function headerIndex(sheet, name, headerRow) {
  var last = sheet.getLastColumn();
  if (!last) return -1;
  var hr = headerRow || findHeaderRow(sheet);
  var headers = sheet.getRange(hr, 1, 1, last).getValues()[0];
  var want = normHeader(name);
  for (var i = 0; i < headers.length; i++) {
    if (normHeader(headers[i]) === want) return i + 1; // 1-based
  }
  return -1;
}

/**
 * Trova la riga di intestazione: prima riga (entro le prime 6) che
 * contiene una colonna `id`. Default 1. Serve per le Tabelle Sheets
 * dove l'header potrebbe non essere in riga 1.
 */
function findHeaderRow(sheet) {
  try {
    var last = Math.min(sheet.getLastColumn(), 30);
    if (!last) return 1;
    var vals = sheet.getRange(1, 1, Math.min(sheet.getLastRow(), 6), last).getValues();
    for (var k = 0; k < vals.length; k++) {
      for (var j = 0; j < vals[k].length; j++) {
        if (normHeader(vals[k][j]) === "id") return k + 1;
      }
    }
  } catch (err) { /* noop */ }
  return 1;
}

/** Foglio per nome, case-insensitive (es. `Inventario` vs `inventario`). */
function findSheetCI(ss, name) {
  var want = normHeader(name);
  var all = ss.getSheets();
  for (var k = 0; k < all.length; k++) {
    if (normHeader(all[k].getName()) === want) return all[k];
  }
  return null;
}

/**
 * Sincronizza la colonna `Accept` in `Schede Inventario` con `ai_stato` di
 * `inventario` (col M, risolta per nome): se `confermato` la checkbox viene
 * rimossa (cella vuota + grigia = "disabilitata"), altrimenti la checkbox
 * viene (ri)aggiunta. Lanciare una volta a mano + trigger orario (le
 * scritture API dell'Action non attivano onEdit).
 */
function syncAcceptBoxes() {
  var ss = SpreadsheetApp.getActive();
  if (!ss) return;
  var inv = findSheetCI(ss, INV_SHEET);
  var rev = findSheetCI(ss, REVIEW_SHEET);
  if (!inv || !rev) { console.log("syncAccept: tab mancante"); return; }
  var invHr = findHeaderRow(inv);
  var cId = headerIndex(inv, "id", invHr);
  var cStato = headerIndex(inv, "ai_stato", invHr);
  if (cId < 1 || cStato < 1) { console.log("syncAccept: colonne inventario mancanti"); return; }
  var hrr = findHeaderRow(rev);
  var idCol = headerIndex(rev, "id", hrr);
  var aCol = headerIndex(rev, "accept", hrr);
  if (idCol < 1 || aCol < 1) { console.log("syncAccept: colonne review mancanti"); return; }

  var invLast = inv.getLastRow();
  var statoById = {};
  if (invLast > invHr) {
    var ids = inv.getRange(invHr + 1, cId, invLast - invHr, 1).getValues();
    var stati = inv.getRange(invHr + 1, cStato, invLast - invHr, 1).getValues();
    for (var i = 0; i < ids.length; i++) {
      var key = normHeader(ids[i][0]);
      if (key) statoById[key] = normHeader(stati[i][0]);
    }
  }
  var revLast = rev.getLastRow();
  if (revLast <= hrr) return;
  var n = revLast - hrr;
  var revIds = rev.getRange(hrr + 1, idCol, n, 1).getValues();
  var rng = rev.getRange(hrr + 1, aCol, n, 1);
  var vals = rng.getValues();
  var rules = rng.getDataValidations();
  var bgs = rng.getBackgrounds();
  var fgs = rng.getFontColors();
  var checkRule = SpreadsheetApp.newDataValidation().requireCheckbox().setAllowInvalid(false).build();
  var touched = false;
  for (var j = 0; j < n; j++) {
    var dis = statoById[normHeader(revIds[j][0])] === "confermato";
    if (dis) {
      if (vals[j][0] !== "" && vals[j][0] !== false) { vals[j][0] = ""; touched = true; }
      else if (vals[j][0] === true) { vals[j][0] = ""; touched = true; }
      if (rules[j][0] !== null) { rules[j][0] = null; touched = true; }
      if (bgs[j][0] !== "#e0e0e0") { bgs[j][0] = "#e0e0e0"; touched = true; }
      if (fgs[j][0] !== "#9e9e9e") { fgs[j][0] = "#9e9e9e"; touched = true; }
    } else {
      if (rules[j][0] === null) { rules[j][0] = checkRule; touched = true; }
      if (vals[j][0] !== "" && vals[j][0] !== true && vals[j][0] !== false) {
        vals[j][0] = false; touched = true;
      }
      if (bgs[j][0] !== "#ffffff") { bgs[j][0] = "#ffffff"; touched = true; }
      if (fgs[j][0] !== "#000000") { fgs[j][0] = "#000000"; touched = true; }
    }
  }
  if (!touched) { console.log("syncAccept: niente da aggiornare"); return; }
  rng.setValues(vals);
  rng.setDataValidations(rules);
  rng.setBackgrounds(bgs);
  rng.setFontColors(fgs);
  console.log("syncAccept: aggiornate " + n + " righe");
}
function debugReviewCell() {
  var ss = SpreadsheetApp.getActive();
  var r = ss.getActiveRange();
  if (!r) { ss.toast("Nessuna cella selezionata.", "maker_self", 5); return; }
  var sheet = r.getSheet();
  var hr = findHeaderRow(sheet);
  var info = "sheet=" + sheet.getName()
    + " cell=" + r.getA1Notation()
    + " headerRow=" + hr
    + " headerCol=" + sheet.getRange(hr, r.getColumn()).getValue()
    + " value=" + r.getValue()
    + " idCol=" + headerIndex(sheet, "id", hr);
  console.log(info);
  ss.toast(info, "maker_self debug", 10);
}

function findInvRowById(invSheet, idCol, id) {
  var last = invSheet.getLastRow();
  if (last < 2 || idCol < 1) return -1;
  var finder = invSheet.getRange(2, idCol, last - 1, 1)
    .createTextFinder(String(id).trim())
    .matchEntireCell(true)
    .matchCase(false);
  var found = finder.findNext();
  if (found) return found.getRow();
  // Fallback: confronto normalizzato (case-insensitive, trim)
  var want = normHeader(id);
  var vals = invSheet.getRange(2, idCol, last - 1, 1).getValues();
  for (var i = 0; i < vals.length; i++) {
    if (normHeader(vals[i][0]) === want && want !== "") return i + 2;
  }
  return -1;
}

/**
 * Gestisce il toggle delle checkbox di review. Ritorna true se l'edit
 * appartiene alle colonne azione (anche in caso di no-op/errore) cosi'
 * il chiamante non prosegue con la logica legacy.
 */
function handleReviewAction(e) {
  var r = e && e.range;
  if (!r) { console.log("review: no range"); return false; }
  var sheet = r.getSheet();
  console.log("review: sheet=" + (sheet && sheet.getName())
    + " cell=" + r.getA1Notation() + " value=" + r.getValue());
  if (!sheet || normHeader(sheet.getName()) !== normHeader(REVIEW_SHEET)) {
    console.log("review: sheet mismatch, skip");
    return false;
  }
  if (r.getNumRows() !== 1 || r.getNumColumns() !== 1) {
    console.log("review: multi-cell, skip"); return false;
  }
  var hr = findHeaderRow(sheet);
  var row = r.getRow();
  var col = r.getColumn();
  if (row <= hr) { console.log("review: header row, skip"); return false; } // intestazione tabella

  var action = normHeader(sheet.getRange(hr, col).getValue());
  console.log("review: headerRow=" + hr + " col=" + col + " action=[" + action + "]");
  if (action !== ACT_PHOTO && action !== ACT_DATASHEET && action !== ACT_ACCEPT) {
    return false;
  }

  var checked = r.getValue() === true || e.value === true ||
    normHeader(r.getValue()) === "true" ||
    normHeader(e.value) === "true";
  console.log("review: checked=" + checked);
  if (!checked) return true; // uncheck: consumato, nessun effetto

  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(10000);
  } catch (err) {
    return true;
  }
  try {
    var idCol = headerIndex(sheet, "id", hr);
    if (idCol < 1) throw new Error("Colonna `Id` non trovata in `" + REVIEW_SHEET + "`.");
    var id = String(sheet.getRange(row, idCol).getValue()).trim();
    console.log("review: idCol=" + idCol + " id=" + id);
    if (!id) throw new Error("Riga " + row + ": `Id` vuoto, nessuna azione.");

    var ss = (e && e.source) || SpreadsheetApp.getActive();
    console.log("review: ss=" + (!!ss) + " tabs=["
      + ss.getSheets().map(function (s) { return s.getName(); }).join(",") + "]");
    var inv = findSheetCI(ss, INV_SHEET);
    if (!inv) throw new Error("Tab `" + INV_SHEET + "` non trovato.");

    var invHr = findHeaderRow(inv);
    var cId = headerIndex(inv, "id", invHr);
    var cFotoId = headerIndex(inv, "foto_drive_id", invHr);
    var cFotoUrl = headerIndex(inv, "foto_url", invHr);
    var cDsUrl = headerIndex(inv, "datasheet_url", invHr);
    var cDsCheck = headerIndex(inv, "datasheet_last_check", invHr);
    var cStato = headerIndex(inv, "ai_stato", invHr);
    console.log("review: invHr=" + invHr + " cId=" + cId + " cStato=" + cStato
      + " cFotoId=" + cFotoId + " cFotoUrl=" + cFotoUrl
      + " cDsUrl=" + cDsUrl + " cDsCheck=" + cDsCheck);
    if (cId < 1 || cStato < 1) {
      throw new Error("Colonne `id`/`ai_stato` mancanti in `inventario`.");
    }

    var target = findInvRowById(inv, cId, id);
    console.log("review: target=" + target);
    if (target < 0) throw new Error("Id `" + id + "` non trovato in `inventario`.");

    if (action === ACT_ACCEPT) {
      var stato = normHeader(inv.getRange(target, cStato).getValue());
      if (stato !== "da_verificare") {
        ss.toast("Accept solo da `da_verificare` (riga `" + id + "` in `" + stato + "`).",
          "maker_self", 5);
        r.setValue(false);
        return true;
      }
      inv.getRange(target, cStato).setValue("confermato");
      ss.toast("Riga `" + id + "` confermata.", "maker_self", 5);
    } else if (action === ACT_PHOTO) {
      if (cFotoId > 0) inv.getRange(target, cFotoId).clearContent();
      if (cFotoUrl > 0) inv.getRange(target, cFotoUrl).clearContent();
      inv.getRange(target, cStato).setValue("retry");
      ss.toast("Riga `" + id + "`: foto rimossa, stato=retry.", "maker_self", 5);
    } else {
      if (cDsUrl > 0) inv.getRange(target, cDsUrl).clearContent();
      if (cDsCheck > 0) inv.getRange(target, cDsCheck).clearContent();
      inv.getRange(target, cStato).setValue("retry");
      ss.toast("Riga `" + id + "`: datasheet rimosso, stato=retry.", "maker_self", 5);
    }

    r.setValue(false); // riusabile
    SpreadsheetApp.flush();
    console.log("review: write ok action=" + action + " id=" + id + " target=" + target);
    // Le scritture da script non attivano onEdit: dispatch esplicito.
    dispatch("sheet-confirmed");
    console.log("review: dispatch ok");
    try { syncAcceptBoxes(); } catch (syncErr) { console.log("review: syncAccept " + syncErr.message); }
  } catch (err) {
    console.log("review: ERRORE " + err.message);
    try {
      r.setValue(false);
      var ui = (e && e.source) || SpreadsheetApp.getActive();
      ui.toast("Azione fallita: " + err.message, "maker_self", 8);
    } catch (ignore) { /* noop */ }
  } finally {
    try { lock.releaseLock(); } catch (ignore) { /* noop */ }
  }
  return true;
}
