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
  const r = e && e.range;
  if (!r) return;
  if (r.getSheet().getName() !== "inventario" || r.getColumn() !== 13) return; // M
  const v = String(r.getValue());
  if (v === "confermato" || v === "scartato") {
    dispatch("sheet-confirmed");
  }
}
