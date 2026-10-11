"""Sincronizza le dropdown categoria/posizione del Google Form 'Nuovo componente'.

Uso:
  python scripts/update_form_choices.py --list [--form-id ID]
  python scripts/update_form_choices.py --add-posizione "Diod Box" [--add-categoria componenti] [--form-id ID]

Env: GOOGLE_SERVICE_ACCOUNT_JSON (o service-account.json), FORM_ID.
Il Form deve essere condiviso con il service account come Editor,
oppure usa --token-file con OAuth utente (scope forms.body).

Se FORM_ID manca o l'API fallisce, stampa i passi manuali e esce 2
(skill inventory-bulk: riporta il TODO all'utente, non skippare in silenzio).
"""
import argparse
import json
import os
import sys

FORM_ID = os.environ.get("FORM_ID", "")
SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sa_info():
    if SA_JSON:
        return json.loads(SA_JSON)
    p = os.path.join(BASE, "service-account.json")
    with open(p) as f:
        return json.load(f)


def forms_svc(token_file=""):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    if token_file:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(
            token_file, scopes=["https://www.googleapis.com/auth/forms.body"])
    else:
        creds = service_account.Credentials.from_service_account_info(
            _sa_info(), scopes=["https://www.googleapis.com/auth/forms.body"])
    return build("forms", "v1", credentials=creds, cache_discovery=False)


def _choice_qs(form):
    out = []
    for item in form.get("items", []):
        q = item.get("questionItem", {}).get("question", {})
        cq = q.get("choiceQuestion", {})
        if not cq:
            continue
        out.append({"itemId": item.get("itemId"), "title": item.get("title", ""),
                    "options": [o.get("value", "") for o in cq.get("options", [])]})
    return out


def pick(qs, *names):
    ln = [(q, (q["title"] or "").lower()) for q in qs]
    for n in names:
        for q, t in ln:
            if n in t:
                return q
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--form-id", default=FORM_ID)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add-posizione", action="append", default=[])
    ap.add_argument("--add-categoria", action="append", default=[])
    ap.add_argument("--token-file", default="")
    a = ap.parse_args()
    if not a.form_id:
        print("FORM_ID mancante: set env FORM_ID o passa --form-id.\n"
              "Manuale: apri il Form 'Nuovo componente' > domanda posizione/categoria > "
              "+ Aggiungi opzione > salva.", file=sys.stderr)
        return 2
    try:
        svc = forms_svc(a.token_file)
        form = svc.forms().get(formId=a.form_id).execute()
    except Exception as e:
        print(f"Forms API non raggiungibile ({e}).\n"
              "Manuale: apri il Form > domanda posizione/categoria > aggiungi opzione > salva.",
              file=sys.stderr)
        return 2
    qs = _choice_qs(form)
    if a.list or (not a.add_posizione and not a.add_categoria):
        print(json.dumps(qs, ensure_ascii=False, indent=2))
        return 0
    reqs = []
    report = []
    targets = [("posizione", "position", a.add_posizione),
               ("categoria", "category", a.add_categoria)]
    for it_name, en_name, adds in targets:
        if not adds:
            continue
        q = pick(qs, it_name, en_name)
        if not q:
            print(f"Domanda '{it_name}' non trovata nel Form "
                  f"(titoli: {[x['title'] for x in qs]}). Aggiorna a mano.", file=sys.stderr)
            return 2
        have = set(q["options"])
        missing = [v for v in adds if v and v not in have]
        if not missing:
            report.append(f"{it_name}: gia' presenti")
            continue
        new_opts = [{"value": v} for v in q["options"] + missing]
        reqs.append({"updateItem": {
            "item": {"itemId": q["itemId"],
                     "questionItem": {"question": {"choiceQuestion": {"options": new_opts}}}},
            "updateMask": "questionItem.question.choiceQuestion.options"}})
        report.append(f"{it_name}: +{missing}")
    if reqs:
        svc.forms().batchUpdate(formId=a.form_id,
                                body={"requests": reqs}).execute()
    print("\n".join(report) or "niente da fare")
    return 0


if __name__ == "__main__":
    sys.exit(main())
