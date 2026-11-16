"""Teste: limites da busca pública do Seats.aero e 2 buscas de exemplo.
Salva em data/_descoberta.json para conferência."""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from mcp import MCP

saida = {}
m = MCP()
try:
    m.iniciar()
    saida["capabilities"] = m.usar("get_capabilities", {})
    hoje = date.today()
    base = {"start_date": (hoje + timedelta(days=1)).isoformat(),
            "end_date": (hoje + timedelta(days=59)).isoformat(), "max_results": 50, "max_stops": 2}
    saida["ida"] = m.usar("get_flights", {**base, "origins": ["GRU", "VCP"],
                                          "destinations": ["LIS", "MIA", "SCL", "MAD", "EZE", "CDG"]})
    saida["nacional"] = m.usar("get_flights", {**base, "origins": ["SJP"], "destinations": ["GRU", "VCP", "CGH"]})
except Exception as e:
    saida["erro"] = repr(e)

os.makedirs("data", exist_ok=True)
with open("data/_descoberta.json", "w", encoding="utf-8") as f:
    json.dump(saida, f, ensure_ascii=False, indent=1)
print(json.dumps(saida, ensure_ascii=False)[:3000])
