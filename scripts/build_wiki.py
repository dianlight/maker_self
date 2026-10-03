"""Genera le pagine del wiki GitHub da snapshot/inventory.json.

Uso:
    python scripts/build_wiki.py                    # genera in wiki_build/
    python scripts/build_wiki.py --out DIR
    python scripts/build_wiki.py --no-download      # solo foto gia' in snapshot/thumbs

Output: Home.md, _Sidebar.md, una pagina per categoria, immagini in images/.
Le foto vengono scaricate da Drive (foto_drive_id) o da foto_url, ridimensionate
e cachate in snapshot/thumbs/, poi copiate in images/ del wiki.
"""
import argparse
import html
import io
import json
import os
import re
import shutil
import sys
from datetime import date

import requests

THUMBS_DIR = os.path.join("snapshot", "thumbs")
MAX_IMG = 1280           # lato massimo del thumbnail
JPEG_QUALITY = 85
CELL_DS_LIMIT = 160      # datasheet non-URL: troncato per leggibilita'
DRIVE_ID_PATTERNS = (r"/file/d/([-\w]{20,})", r"[?&]id=([-\w]{20,})")


def slug(s):
    """Stessa funzione di sync_sheet_to_git.py (filename categorie)."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "na"


def is_url(s):
    return s.startswith(("http://", "https://"))


def hcell(v, limit=0):
    """Cella tabella HTML (escape + newline -> <br>).

    Le foto stanno in tabelle HTML grezze (non markdown): il CSS di GitHub
    (`img { max-width: 100% }`) collassa la colonna foto delle tabelle
    markdown e l'attributo width viene ignorato. Con <td width="...">
    la colonna resta larga e la foto rende alla dimensione voluta.
    """
    t = str(v or "").replace("\r\n", "\n").strip()
    t = html.escape(t).replace("\n", "<br>")
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t or "—"


def sniff_ext(data):
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".jpg"


def shrink(data, path):
    """Salva ridimensionato via PIL; fallback: salva cosi' com'e'."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.thumbnail((MAX_IMG, MAX_IMG))
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(path, "JPEG", quality=JPEG_QUALITY)
        return True
    except Exception:
        pass
    try:
        with open(path, "wb") as f:
            f.write(data)
        return True
    except OSError:
        return False


def drive_id_of(part):
    fid = (part.get("foto_drive_id") or "").strip()
    if fid:
        return fid
    url = (part.get("foto_url") or "").strip()
    for pat in DRIVE_ID_PATTERNS:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return ""


class PhotoResolver:
    """Risolve la foto di un componente: cache locale -> Drive -> foto_url."""

    def __init__(self, thumbs_dir, download=True):
        self.thumbs_dir = thumbs_dir
        self.download = download
        self._drv = None
        self._drv_failed = False
        os.makedirs(thumbs_dir, exist_ok=True)

    def _cached(self, pid):
        for ext in (".jpg", ".png", ".webp", ".gif"):
            p = os.path.join(self.thumbs_dir, pid + ext)
            if os.path.isfile(p):
                return p
        return None

    def _drive(self):
        if self._drv is None and not self._drv_failed:
            sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
            if not sa:
                self._drv_failed = True
                return None
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
                creds = service_account.Credentials.from_service_account_info(
                    json.loads(sa),
                    scopes=["https://www.googleapis.com/auth/drive.readonly"])
                self._drv = build("drive", "v3", credentials=creds,
                                  cache_discovery=False)
            except Exception as e:
                print(f"drive service: {e}", file=sys.stderr)
                self._drv_failed = True
        return self._drv

    def resolve(self, part):
        """Ritorna il percorso immagine oppure None."""
        pid = part["id"]
        cached = self._cached(pid)
        if cached:
            return cached
        if not self.download:
            return None

        data = None
        fid = drive_id_of(part)
        if fid:
            drv = self._drive()
            if drv is not None:
                try:
                    data = drv.files().get_media(fileId=fid).execute()
                except Exception as e:
                    print(f"drive {pid}: {e}", file=sys.stderr)
        if data is None and is_url(part.get("foto_url", "")):
            try:
                r = requests.get(part["foto_url"], timeout=30)
                if r.ok and r.content:
                    data = r.content
            except requests.RequestException as e:
                print(f"url {pid}: {e}", file=sys.stderr)
        if not data:
            return None

        path = os.path.join(self.thumbs_dir, pid + sniff_ext(data))
        return path if shrink(data, path) else None


def photo_md(path, out_images, label):
    """Copia l'immagine nel wiki e ne ritorna il markdown HTML cliccabile."""
    fname = os.path.basename(path)
    shutil.copy2(path, os.path.join(out_images, fname))
    safe = (label or "").replace('"', "'").replace("<", "").replace(">", "")
    img = f'<img src="images/{fname}" width="270" alt="{safe}">'
    return f'<a href="images/{fname}">{img}</a>'


def ds_html(raw):
    v = (raw or "").strip()
    if not v:
        return "—"
    if is_url(v):
        safe = html.escape(v, quote=True)
        return f'<a href="{safe}">Datasheet</a>'
    return hcell(v, CELL_DS_LIMIT)  # testo incollato nella cella, non un link


def sort_key(p):
    return ((p.get("codice") or "").lower(),
            (p.get("descrizione") or "").lower(),
            p["id"])


def plural(n, sing, plur):
    return f"{n} {sing if n == 1 else plur}"


def display_name(cat):
    return "Varie" if not (cat or "").strip() else cat.strip()


def page_name(cat):
    return "varie" if not (cat or "").strip() else slug(cat)


def build_category_page(cat, items, resolver, out_images):
    """Una sezione per componente: heading, foto grande cliccabile, box scheda."""
    n_photo = n_ds = 0
    total_qty = sum(p.get("quantita", 0) for p in items)
    sections = []
    for p in sorted(items, key=sort_key):
        bits = []
        path = resolver.resolve(p)
        if path:
            bits.append(photo_md(path, out_images, p.get("codice") or p["id"]))
            bits.append("")
            n_photo += 1

        qty = p.get("quantita", 0)
        qty_s = f"**{qty}**" if qty <= 0 else str(qty)
        ds = ds_html(p.get("datasheet_url"))
        if ds != "—":
            n_ds += 1

        # box scheda compatta (griglia label/valore, stile definition list)
        rows = [("Qty", qty_s),
                ("Posizione", hcell(p.get("posizione"))),
                ("Interfaccia", hcell(p.get("interfaccia"))),
                ("Datasheet", ds),
                ("Note", hcell(p.get("note"))),
                ("Aggiornato", hcell(p.get("updated_at")))]
        rows = [(k, v) for k, v in rows if v != "—"]
        if rows:
            bits.append("<table>")
            for k, v in rows:
                bits.append(f"<tr><th>{k}</th><td>{v}</td></tr>")
            bits.append("</table>")

        heading = hcell(p.get("codice")) or hcell(p.get("descrizione")) or p["id"]
        descr = hcell(p.get("descrizione"))
        title = f"{heading} — {descr}" if (descr and descr != heading) else heading
        parts = [f"### {title}"] + bits
        sections.append("\n".join(parts))

    name = display_name(cat)
    out = [
        f"# {name}",
        "",
        f"{plural(len(items), 'componente', 'componenti')} · "
        f"{plural(total_qty, 'pezzo', 'pezzi')} · "
        f"{n_ds} datasheet · {plural(n_photo, 'foto', 'foto')} "
        f"— [Home](Home)",
        "",
    ]
    out.extend(sections)
    out.append("")
    return "\n".join(out)


def build_home(groups, resolver, out_images, generated_at):
    """groups: lista (cat, items) gia' ordinata per nome pagina."""
    total_parts = sum(len(items) for _, items in groups)
    total_qty = sum(p.get("quantita", 0) for _, items in groups for p in items)
    n_ds = sum(1 for _, items in groups for p in items
               if (p.get("datasheet_url") or "").strip())
    n_photo = 0
    cat_rows = []
    for cat, items in groups:
        qty = sum(p.get("quantita", 0) for p in items)
        ds = sum(1 for p in items if (p.get("datasheet_url") or "").strip())
        ph = sum(1 for p in items if resolver.resolve(p))
        n_photo += ph
        cat_rows.append(
            f"| [{display_name(cat)}]({page_name(cat)}) "
            f"| {len(items)} | {qty} | {ds} | {ph} |")

    pct_ds = round(100 * n_ds / total_parts) if total_parts else 0
    pct_ph = round(100 * n_photo / total_parts) if total_parts else 0
    out = [
        "# Inventario laboratorio — maker_self",
        "",
        f"> Generato il {generated_at} da `scripts/build_wiki.py` "
        f"— fonte: `snapshot/inventory.json`.",
        "",
        "| | |",
        "|---|---:|",
        f"| Componenti | {total_parts} |",
        f"| Pezzi totali | {total_qty} |",
        f"| Categorie | {len(groups)} |",
        f"| Con datasheet | {n_ds} ({pct_ds}%) |",
        f"| Con foto | {n_photo} ({pct_ph}%) |",
        "",
        "## Categorie",
        "",
        "| Categoria | Componenti | Pezzi | Datasheet | Foto |",
        "|---|---:|---:|---:|---:|",
    ]
    out.extend(cat_rows)
    out.append("")
    return "\n".join(out)


def build_sidebar(groups):
    out = ["**Inventario**", "", "- [Home](Home)"]
    out.extend(f"- [{display_name(cat)}]({page_name(cat)})"
               for cat, _ in groups)
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="wiki_build", help="directory di output")
    ap.add_argument("--inventory",
                    default="snapshot/inventory.json", help="snapshot in ingresso")
    ap.add_argument("--no-download", action="store_true",
                    help="non scaricare foto: usa solo snapshot/thumbs")
    args = ap.parse_args()

    if not os.path.isfile(args.inventory):
        print(f"snapshot mancante: {args.inventory}", file=sys.stderr)
        return 1
    with open(args.inventory, encoding="utf-8") as f:
        parts = json.load(f)
    if not parts:
        print("snapshot vuoto, niente da generare.", file=sys.stderr)
        return 1

    by_cat = {}
    for p in parts:
        by_cat.setdefault(p.get("categoria") or "", []).append(p)
    # gruppi ordinati per nome pagina (determinismo dei diff)
    groups = sorted(by_cat.items(),
                    key=lambda kv: (page_name(kv[0]), display_name(kv[0])))

    out_dir = args.out
    out_images = os.path.join(out_dir, "images")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_images)

    resolver = PhotoResolver(THUMBS_DIR, download=not args.no_download)
    generated = date.today().isoformat()

    files = {"Home.md": build_home(groups, resolver, out_images, generated),
             "_Sidebar.md": build_sidebar(groups)}
    for cat, items in groups:
        files[page_name(cat) + ".md"] = build_category_page(
            cat, items, resolver, out_images)

    for name, text in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(text)
    if not os.listdir(out_images):
        os.rmdir(out_images)

    n_img = len(os.listdir(out_images)) if os.path.isdir(out_images) else 0
    print(f"wiki: {len(files)} pagine, {n_img} immagini -> {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
