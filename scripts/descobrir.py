"""Teste: lista as ferramentas da busca pública do Seats.aero e faz 1 busca de exemplo.
Salva tudo em data/_descoberta.json para conferência."""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from mcp import MCP

saida = {}
m = MCP()
try:
    saida["init"] = m.iniciar()
    tools = m.ferramentas()
    saida["tools"] = tools
    nomes = [t["name"] for t in tools]
    alvo = next((n for n in nomes if "search" in n.lower()), None)
    if alvo:
        hoje = date.today()
        args = {"origin_airport": "GRU", "destination_airport": "LIS",
                "start_date": (hoje + timedelta(days=7)).isoformat(),
                "end_date": (hoje + timedelta(days=50)).isoformat()}
        schema = next(t for t in tools if t["name"] == alvo).get("inputSchema", {})
        props = schema.get("properties", {})
        args = {k: v for k, v in args.items() if k in props} or args
        r = m.usar(alvo, args)
        saida["exemplo_args"] = {"tool": alvo, "args": args}
        saida["exemplo"] = json.dumps(r, ensure_ascii=False)[:20000]
except Exception as e:
    saida["erro"] = repr(e)

os.makedirs("data", exist_ok=True)
with open("data/_descoberta.json", "w", encoding="utf-8") as f:
    json.dump(saida, f, ensure_ascii=False, indent=1)
print(json.dumps(saida, ensure_ascii=False)[:3000])
