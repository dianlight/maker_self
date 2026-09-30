"""MCP stdio locale read-only sopra snapshot/inventory.json."""
import json
import os
from mcp.server.fastmcp import FastMCP

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(BASE, "snapshot", "inventory.json")

mcp = FastMCP("maker-self")


def load():
    try:
        with open(SNAP) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


@mcp.tool()
def search_parts(q: str = "", categoria: str = "") -> str:
    """Cerca parti per testo libero e/o categoria. Ritorna JSON lista (max 20)."""
    ql, cl = q.lower(), categoria.lower()
    out = [p for p in load()
           if (not ql or ql in json.dumps(p, ensure_ascii=False).lower())
           and (not cl or cl in (p.get("categoria") or "").lower())][:20]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def get_part(id: str) -> str:
    """Dettaglio parte per id."""
    for p in load():
        if p.get("id") == id:
            return json.dumps(p, ensure_ascii=False, indent=2)
    return json.dumps({"error": "not found"})


@mcp.tool()
def stock_check(id: str) -> str:
    """Quantita + posizione, sola lettura."""
    for p in load():
        if p.get("id") == id:
            return json.dumps({k: p.get(k) for k in
                               ("id", "codice", "quantita", "posizione", "categoria")},
                              ensure_ascii=False)
    return json.dumps({"error": "not found"})


if __name__ == "__main__":
    mcp.run()
