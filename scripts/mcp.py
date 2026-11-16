"""Cliente mínimo para a busca pública (MCP) do Seats.aero — grátis, sem conta,
voos nos próximos ~60 dias, com limite diário por IP. Só biblioteca padrão."""
import json
import urllib.request

URL = "https://seats.aero/mcp"


class MCP:
    def __init__(self, url=URL):
        self.url, self.sessao, self.n = url, None, 0

    def _post(self, corpo):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "User-Agent": "buscador-milhas-pessoal", "MCP-Protocol-Version": "2025-03-26"}
        if self.sessao:
            h["Mcp-Session-Id"] = self.sessao
        req = urllib.request.Request(self.url, data=json.dumps(corpo).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=90) as r:
            self.sessao = r.headers.get("Mcp-Session-Id") or self.sessao
            texto = r.read().decode("utf-8", "replace")
            tipo = r.headers.get("Content-Type", "")
        if not texto.strip():
            return None
        if "event-stream" in tipo:  # resposta em SSE: pega o último "data:" com JSON
            msgs = [l[5:].strip() for l in texto.splitlines() if l.startswith("data:")]
            for m in reversed(msgs):
                try:
                    return json.loads(m)
                except ValueError:
                    pass
            return None
        return json.loads(texto)

    def chamar(self, metodo, params=None):
        self.n += 1
        resp = self._post({"jsonrpc": "2.0", "id": self.n, "method": metodo, "params": params or {}})
        if resp and resp.get("error"):
            raise RuntimeError(f"MCP {metodo}: {resp['error']}")
        return (resp or {}).get("result")

    def iniciar(self):
        r = self.chamar("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "buscador-milhas", "version": "1.0"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def ferramentas(self):
        return (self.chamar("tools/list") or {}).get("tools", [])

    def usar(self, nome, args):
        """Chama uma ferramenta e devolve o conteúdo já como JSON quando possível."""
        r = self.chamar("tools/call", {"name": nome, "arguments": args}) or {}
        if r.get("structuredContent"):
            return r["structuredContent"]
        textos = [c.get("text", "") for c in r.get("content", []) if c.get("type") == "text"]
        junto = "\n".join(textos)
        try:
            return json.loads(junto)
        except ValueError:
            return {"_texto": junto, "_erro": r.get("isError")}
