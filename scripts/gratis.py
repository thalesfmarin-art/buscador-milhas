"""Coleta GRATUITA pela busca pública do Seats.aero (sem conta, próximos 60 dias,
1.000 consultas por dia por IP, até 50 resultados por consulta)."""
import re
import time
from datetime import date, timedelta

from mcp import MCP

# destinos internacionais acompanhados (código -> região, nomes de região do Seats.aero)
DESTINOS = {
    **{c: "Europe" for c in "LIS OPO MAD BCN CDG FCO MXP LHR FRA MUC AMS ZRH IST BRU VIE ATH DUB CPH HEL".split()},
    **{c: "North America" for c in "MIA MCO FLL JFK EWR ATL IAH ORD DFW LAX IAD YYZ YUL MEX CUN".split()},
    **{c: "South America" for c in "SCL EZE AEP MVD ASU LIM BOG UIO".split()},
    **{c: "Central America" for c in "PTY PUJ".split()},
    **{c: "Middle East" for c in "DXB DOH AUH".split()},
    **{c: "Africa" for c in "ADD JNB LAD CMN".split()},
    **{c: "Asia" for c in "NRT HND SIN".split()},
}
CAB = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}


def taxa_em_reais(txt, cambios):
    """'$56.40 USD' / 'R$37.13 BRL' -> valor em reais."""
    if not txt:
        return None
    m = re.search(r"([\d.,]+)\s*([A-Z]{3})", txt)
    if not m:
        return None
    valor = float(m.group(1).replace(",", ""))
    return round(valor * cambios.get(m.group(2), cambios.get("USD", 5.4)))


def agrupar(voos, cambios, regiao_de):
    """Lista de voos -> registros por programa+rota+data, com o mais barato de cada cabine."""
    regs = {}
    for v in voos:
        cab = CAB.get((v.get("cabin") or "").lower())
        if not cab or not v.get("miles_price"):
            continue
        dt = (v.get("departs_at") or "")[:10]
        k = (v["mileage_program"], v["origin"], v["destination"], dt)
        r = regs.setdefault(k, {
            "id": v.get("flight_ref"), "p": v["mileage_program"], "o": v["origin"], "d": v["destination"],
            "ro": regiao_de(v["origin"]), "rd": regiao_de(v["destination"]), "dt": dt, "u": None, "c": {}, "f": {}})
        atual = r["c"].get(cab)
        if atual and atual[0] <= v["miles_price"]:
            continue
        avisos = v.get("warnings") or []
        r["c"][cab] = [
            v["miles_price"],
            None if "unknown_taxes" in avisos else taxa_em_reais(v.get("taxes"), cambios),
            v.get("remaining_seats") or 0,
            1 if v.get("stops") == 0 else 0,
            ",".join(v.get("operating_carriers") or []),
            v.get("duration_minutes"),
        ]
        # detalhes do voo: ref, horário de saída e chegada, números dos voos, paradas
        r["f"][cab] = [v.get("flight_ref"), (v.get("departs_at") or "")[11:16],
                       (v.get("arrives_at") or "")[11:16], v.get("flights"), v.get("stops")]
        minutos = v.get("minutes_old")
        if minutos is not None:
            visto = (time.time() - minutos * 60)
            r["u"] = max(r["u"] or 0, visto)
    saida = []
    for r in regs.values():
        if r["u"]:
            r["u"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["u"]))
        r["id"] = next(iter(r["f"].values()))[0]
        saida.append(r)
    return saida


def coletar(cfg, cambios, brasil, limite):
    m = MCP()
    m.iniciar()
    hoje = date.today()
    ini, fim = (hoje + timedelta(days=1)).isoformat(), (hoje + timedelta(days=59)).isoformat()
    portas = cfg["aeroportos_internacionais"]
    cabines = cfg.get("cabines_gratis", ["economy", "business"])
    regiao_de = lambda a: "South America" if a in brasil else DESTINOS.get(a, "")
    chamadas = 0
    erros = 0

    def buscar(origens, destinos, cabine=None):
        nonlocal chamadas, erros
        if chamadas >= limite:
            return []
        chamadas += 1
        args = {"origins": origens, "destinations": destinos, "start_date": ini, "end_date": fim,
                "max_results": 50, "max_stops": 2}
        if cabine:
            args["cabin"] = cabine
        try:
            r = m.usar("get_flights", args)
        except Exception as e:
            erros += 1
            print("  erro:", origens, destinos, cabine, e)
            return []
        return r.get("flights", []) if isinstance(r, dict) else []

    ida, volta = [], []
    for dest in DESTINOS:
        for cab in cabines:
            ida += buscar(portas, [dest], cab)
            volta += buscar([dest], portas, cab)
        if erros > 15:
            print("  muitos erros, parando a coleta")
            break
    # origem do interior (ex.: Rio Preto): tenta itinerários diretos com conexão no mesmo bilhete
    extra = cfg.get("origem_interior")
    if extra:
        todos = list(DESTINOS)
        for cab in cabines:
            ida += buscar([extra], todos, cab)
            volta += buscar(todos, [extra], cab)
    print(f"  internacional: {len(ida)} voos de ida, {len(volta)} de volta ({chamadas} consultas)")

    nac = []
    sp, origem = cfg["aeroportos_conexao_sp"], cfg["cidade_origem"]
    for d0 in (range(0, 59, 15) if cfg.get("coletar_nacional", True) else []):   # janelas de 15 dias para pegar mais datas
        a, b = (hoje + timedelta(days=1 + d0)).isoformat(), (hoje + timedelta(days=min(59, d0 + 15))).isoformat()
        for o, d in [x for aero in sp for x in (([origem], [aero]), ([aero], [origem]))]:   # um aeroporto por vez
            if chamadas >= limite:
                break
            chamadas += 1
            try:
                r = m.usar("get_flights", {"origins": o, "destinations": d, "start_date": a, "end_date": b,
                                           "max_results": 50, "max_stops": 1, "cabin": "economy"})
                nac += r.get("flights", []) if isinstance(r, dict) else []
            except Exception as e:
                print("  erro nacional:", e)
    print(f"  nacional: {len(nac)} voos")

    saidas = set(portas) | ({extra} if extra else set())
    regs_ida = [r for r in agrupar(ida, cambios, regiao_de) if r["o"] in saidas and r["d"] not in brasil]
    regs_volta = [r for r in agrupar(volta, cambios, regiao_de) if r["d"] in saidas and r["o"] not in brasil]
    regs_nac = agrupar(nac, cambios, regiao_de)
    return regs_ida, regs_volta, regs_nac, m, chamadas


def links_emissao(m, regs, imperdiveis, cache, limite):
    """Para as passagens mais interessantes, pega o link do Seats.aero com as
    instruções de emissão daquele voo (get_flight_details)."""
    ativos = {}
    for r in regs:
        for cab, f in r.get("f", {}).items():
            ativos[f[0]] = (r, cab)
    cache = {k: v for k, v in cache.items() if k in ativos}
    fila = []
    fila += [a["fr"] for a in imperdiveis if a.get("fr")]
    grupos = {}
    for ref, (r, cab) in ativos.items():
        grupos.setdefault((r["o"], r["d"], cab), []).append((r["c"][cab][0], ref))
    for lista in grupos.values():
        fila += [ref for _, ref in sorted(lista)[:2]]
    feitos = 0
    for ref in dict.fromkeys(fila):
        if feitos >= limite:
            break
        if ref in cache:
            continue
        feitos += 1
        try:
            r = m.usar("get_flight_details", {"flight_ref": ref})
            cache[ref] = (r or {}).get("url") or ""
        except Exception as e:
            print("  erro detalhes:", e)
            cache[ref] = ""
    print(f"  links de emissão: {feitos} novos")
    return cache, feitos
