/* maker_self instant trigger — bound script di maker_self_inventario.
 * Installazione: vedi apps-script/README.md
 */
const REPO = "dianlight/maker_self";

function dispatch(eventType) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty("GITHUB_TOKEN");
  if (!token) {
    throw new Error("GITHUB_TOKEN mancante nelle proprietà script.");
  }
  UrlFetchApp.fetch("https://api.github.com/repos/" + REPO + "/dispatches", {
    method: "post",
    contentType: "application/json",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: "Bearer " + token,
    },
    payload: JSON.stringify({ event_type: eventType }),
    muteHttpExceptions: false,
  });
}

function onFormSubmitTrigger() {
  dispatch("form-submitted");
}

function onEditTrigger(e) {
  if (handleReviewAction(e)) return;
  const r = e && e.range;
  if (!r) return;
  const sheet = r.getSheet();
  if (!sheet || sheet.getName() !== "inventario") return;
  // Colonna ai_stato risolta per nome header (non per lettera fissa).
  let isStato = false;
  try {
    const hr = findHeaderRow(sheet);
    if (r.getRow() <= hr) return;
    isStato = normHeader(sheet.getRange(hr, r.getColumn()).getValue()) === "ai_stato";
  } catch (err) {
    isStato = (r.getColumn() === 14); // fallback N
  }
  if (!isStato) return;
  const v = String(r.getValue()).trim().toLowerCase();
  if (v === "confermato" || v === "scartato" || v === "retry") {
    dispatch("sheet-confirmed");
    try { syncAcceptBoxes(); } catch (err) { console.log("syncAccept " + err.message); }
  }
}
