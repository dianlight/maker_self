# Uso da OpenCode
MCP locale read-only sopra `snapshot/inventory.json`. Nessun server.

`opencode.json`:
```json
{
  "mcp": {
    "maker-self": {
      "type": "stdio",
      "command": ["python", "mcp_server/server.py"],
      "cwd": "/Users/ltarantino/Documents/Sources/maker_self"
    }
  }
}
```

Esempi:
- `search_parts("BMP280")`
- `search_parts("ESP32", "mcu board")`
- `get_part("sensori-bmp280-01")`
- `stock_check("mcu-board-esp32-s-01")` legge `quantita` + `posizione`
