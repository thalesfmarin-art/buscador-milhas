#!/usr/bin/env python3
"""
Robô de coleta do Buscador de Milhas.

Roda no GitHub Actions algumas vezes por dia:
  1. Busca na API do Seats.aero as passagens com milhas saindo de GRU/VCP para o
     exterior (ida) e voltando do exterior para GRU/VCP (volta).
  2. Busca o trecho nacional Rio Preto <-> São Paulo/Campinas.
  3. Converte as taxas para reais, atualiza o histórico de preços e marca as
     "imperdíveis".
  4. Envia alertas pelo Telegram (opcional).

Só usa a biblioteca padrão do Python (nada para instalar).
Variáveis de ambiente:
  SEATS_AERO_API_KEY   (obrigatória)  chave da API do Seats.aero Pro
  TELEGRAM_TOKEN       (opcional)     token do bot do Telegram
  TELEGRAM_CHAT_ID     (opcional)     seu chat id no Telegram
"""
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(RAIZ, "data")
CONFIG = os.path.join(RAIZ, "config")
API = "https://seats.aero/partnerapi"

PROGRAMAS = [
    "smiles", "azul", "aeroplan", "alaska", "american", "aeromexico", "lifemiles",
    "copa", "delta", "emirates", "ethiopian", "etihad", "finnair", "flyingblue",
    "jetblue", "lufthansa", "qantas", "qatar", "eurobonus", "saudia", "singapore",
    "turkish", "united", "virginatlantic", "velocity",
]

# Aeroportos brasileiros (para separar voo nacional de internacional)
BRASIL = set("""
GRU CGH VCP GIG SDU BSB CNF PLU SSA REC FOR POA CWB FLN BEL MAO NAT MCZ AJU JPA THE SLZ
CGB CGR GYN VIX IGU NVT JOI LDB MGF SJP RAO UDI BPS PMW PVH RBR BVB MCP IOS IMP STM
PNZ JDO XAP CXJ PFB CAC UBA MOC JTC PPB AQA BAU GVR IPN VDC CPV FEN SJK ARU BYO
""".split())

CABINES = {"Y": "economy", "W": "premium", "J": "business", "F": "first"}

chamadas = 0
max_chamadas = 400


def ler_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def salvar_json(caminho, dados):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))


def api_get(url):
    global chamadas
    if chamadas >= max_chamadas:
        raise RuntimeError("limite de chamadas desta rodada atingido")
    chamadas += 1
    req = urllib.request.Request(url, headers={
        "Partner-Authorization": os.environ["SEATS_AERO_API_KEY"],
        "Accept": "application/json",
        "User-Agent": "buscador-milhas-pessoal",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def paginar(url):
    """Segue o moreURL da API até acabar, sem repetir IDs."""
    vistos, itens = set(), []
    while url:
        try:
            resp = api_get(url)
        except RuntimeError as e:
            print("  aviso:", e)
            break
        except Exception as e:  # erro de rede/HTTP: segue com o que já tem
            print("  erro na chamada:", e)
            break
        for item in resp.get("data") or []:
            if item.get("ID") not in vistos:
                vistos.add(item.get("ID"))
                itens.append(item)
        url = ("https://seats.aero" + resp["moreURL"]) if resp.get("hasMore") and resp.get("moreURL") else None
    return itens


def cambio():
    """Quanto vale 1 unidade de cada moeda em reais."""
    padrao = {"BRL": 1.0, "USD": 5.4, "EUR": 6.2, "CAD": 3.9, "GBP": 7.2}
    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/BRL", timeout=30) as r:
            rates = json.load(r)["rates"]
        return {moeda: round(1 / v, 4) for moeda, v in rates.items() if v}
    except Exception as e:
        print("  câmbio: usando valores padrão", e)
        return padrao


def converter_registro(item, cambios):
    """Transforma o objeto da API num registro compacto para o site."""
    rota = item.get("Route") or {}
    taxas = {}
    for t in item.get("AvailabilityTrips") or []:
        cab = {"economy": "Y", "premium": "W", "business": "J", "first": "F"}.get((t.get("Cabin") or "").lower())
        if not cab:
            continue
        custo = int(t.get("MileageCost") or 0)
        # TotalTaxes vem em centavos da moeda TaxesCurrency
        valor = (t.get("TotalTaxes") or 0) / 100 * cambios.get(t.get("TaxesCurrency") or "USD", cambios.get("USD", 5.4))
        atual = taxas.get(cab)
        if custo and (atual is None or custo < atual[0]):
            taxas[cab] = (custo, round(valor), t.get("Stops"), t.get("TotalDuration"))
    c = {}
    for cab in CABINES:
        if not item.get(cab + "Available"):
            continue
        try:
            mi = int(item.get(cab + "MileageCost") or 0)
        except ValueError:
            continue
        if not mi:
            continue
        tx = taxas.get(cab, (0, None, None, None))
        c[cab] = [
            mi,
            tx[1],                                   # taxas em R$ (None = desconhecida)
            item.get(cab + "RemainingSeats") or 0,
            1 if item.get(cab + "Direct") else 0,
            item.get(cab + "Airlines") or "",
            tx[3],                                   # duração em minutos
        ]
    if not c:
        return None
    return {
        "id": item.get("ID"),
        "p": item.get("Source") or rota.get("Source"),
        "o": rota.get("OriginAirport"),
        "d": rota.get("DestinationAirport"),
        "ro": rota.get("OriginRegion"),
        "rd": rota.get("DestinationRegion"),
        "dt": (item.get("Date") or "")[:10],
        "u": item.get("UpdatedAt"),
        "c": c,
    }


def coletar_internacional(cfg, cambios):
    hoje = date.today()
    fim = hoje + timedelta(days=cfg["dias_a_frente"])
    portas = set(cfg["aeroportos_internacionais"])
    ida, volta = [], []
    for prog in PROGRAMAS:
        base = {"source": prog, "start_date": hoje.isoformat(), "end_date": fim.isoformat(),
                "include_trips": "true", "minify_trips": "true", "take": 1000}
        for direcao, campo in (("ida", "origin_region"), ("volta", "destination_region")):
            url = f"{API}/availability?" + urllib.parse.urlencode({**base, campo: "South America"})
            itens = paginar(url)
            for it in itens:
                reg = converter_registro(it, cambios)
                if not reg:
                    continue
                if direcao == "ida" and reg["o"] in portas and reg["d"] not in BRASIL:
                    ida.append(reg)
                elif direcao == "volta" and reg["d"] in portas and reg["o"] not in BRASIL:
                    volta.append(reg)
        print(f"  {prog}: total ida={len(ida)} volta={len(volta)} chamadas={chamadas}")
    return ida, volta


def coletar_nacional(cfg, cambios):
    hoje = date.today()
    fim = hoje + timedelta(days=cfg["dias_a_frente"])
    sp = ",".join(cfg["aeroportos_conexao_sp"])
    origem = cfg["cidade_origem"]
    regs = []
    for o, d in ((origem, sp), (sp, origem)):
        url = f"{API}/search?" + urllib.parse.urlencode({
            "origin_airport": o, "destination_airport": d, "start_date": hoje.isoformat(),
            "end_date": fim.isoformat(), "take": 1000, "include_trips": "true", "minify_trips": "true"})
        for it in paginar(url):
            reg = converter_registro(it, cambios)
            if reg:
                regs.append(reg)
    print(f"  nacional: {len(regs)} registros")
    return regs


def atualizar_historico(historico, registros, hoje_str):
    """Média e mínimo por rota+cabine+programa, contando no máximo 1 vez por dia."""
    menores = {}
    for r in registros:
        for cab, v in r["c"].items():
            k = f'{r["o"]}-{r["d"]}-{cab}-{r["p"]}'
            menores[k] = min(menores.get(k, 10**9), v[0])
    for k, mi in menores.items():
        h = historico.get(k, {"n": 0, "media": 0, "min": mi, "dia": ""})
        if h["dia"] != hoje_str:
            h["media"] = round((h["media"] * h["n"] + mi) / (h["n"] + 1))
            h["n"] += 1
            h["dia"] = hoje_str
        h["min"] = min(h["min"], mi)
        historico[k] = h
    return historico


def marcar_imperdiveis(registros, historico, pct):
    """Retorna as imperdíveis atuais e marca 'saver' nos registros."""
    fator = 1 - pct / 100
    # mediana atual por região+cabine+programa (usada enquanto não há histórico)
    grupos = {}
    for r in registros:
        reg = r["rd"] if r["d"] not in BRASIL else r["ro"]
        for cab, v in r["c"].items():
            grupos.setdefault((reg, cab, r["p"]), []).append(v[0])
    medianas = {k: statistics.median(v) for k, v in grupos.items() if len(v) >= 5}

    achados = []
    for r in registros:
        r["s"] = []
        reg = r["rd"] if r["d"] not in BRASIL else r["ro"]
        for cab, v in r["c"].items():
            h = historico.get(f'{r["o"]}-{r["d"]}-{cab}-{r["p"]}')
            if h and v[0] <= h["min"] * 1.1:
                r["s"].append(cab)
            if h and h["n"] >= 5:
                ref, base = h["media"], "média da rota"
            elif (reg, cab, r["p"]) in medianas:
                ref, base = medianas[(reg, cab, r["p"])], "mediana atual"
            else:
                continue
            if v[0] <= ref * fator:
                achados.append({
                    "k": f'{r["id"]}-{cab}', "id": r["id"], "fr": (r.get("f", {}).get(cab) or [None])[0], "p": r["p"], "o": r["o"], "d": r["d"],
                    "dt": r["dt"], "cab": cab, "mi": v[0], "tx": v[1], "ref": round(ref),
                    "desc": round((1 - v[0] / ref) * 100), "base": base,
                })
        if not r["s"]:
            del r["s"]
    achados.sort(key=lambda a: (-a["desc"], a["mi"]))
    # no máximo 1 por rota+classe, para a lista não repetir o mesmo voo em várias datas
    vistos, unicos = set(), []
    for a in achados:
        if (a["o"], a["d"], a["cab"]) not in vistos:
            vistos.add((a["o"], a["d"], a["cab"]))
            unicos.append(a)
    return unicos


def buscar_links(regs, imperdiveis, limite):
    """Pega o link direto de emissão (booking_links do /trips) para as passagens
    mais interessantes. Cada ID custa 1 chamada, então guarda em cache."""
    caminho = os.path.join(DATA, "links.json")
    cache = ler_json(caminho, {})
    ativos = {r["id"] for r in regs}
    cache = {k: v for k, v in cache.items() if k in ativos}   # limpa IDs que sumiram

    # prioridade: imperdíveis, depois as 3 mais baratas por destino+cabine
    fila = [a["id"] for a in imperdiveis]
    grupos = {}
    for r in regs:
        for cab, v in r["c"].items():
            grupos.setdefault((r["o"], r["d"], cab), []).append((v[0], r["id"]))
    for lista in grupos.values():
        fila += [i for _, i in sorted(lista)[:3]]

    feitos = 0
    for i in dict.fromkeys(fila):
        if feitos >= limite or chamadas >= max_chamadas:
            break
        if i in cache:
            continue
        try:
            resp = api_get(f"{API}/trips/{urllib.parse.quote(str(i))}")
        except Exception as e:
            print("  links: parando,", e)
            break
        feitos += 1
        links = resp.get("booking_links") or []
        escolhido = next((l for l in links if l.get("primary")), links[0] if links else None)
        cache[i] = escolhido.get("link") if escolhido else ""
    salvar_json(caminho, cache)
    print(f"  links diretos: {feitos} novos, {sum(1 for v in cache.values() if v)} no total")


def enviar_telegram(texto):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("  telegram: sem token/chat id, alerta não enviado")
        return False
    dados = urllib.parse.urlencode({"chat_id": chat, "text": texto, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=dados, timeout=30)
        return True
    except Exception as e:
        print("  telegram: erro", e)
        return False


def link_busca(r, cab):
    q = urllib.parse.urlencode({"date": r["dt"], "origins": r["o"], "destinations": r["d"], "sources": r["p"],
                                "applicable_cabin": {"Y": "economy", "W": "premium", "J": "business", "F": "first"}[cab],
                                "min_seats": 1, "stops": 2})
    return "https://seats.aero/search?" + q


NOMES_CAB = {"Y": "econômica", "W": "premium", "J": "executiva", "F": "primeira"}


def processar_alertas(ida):
    cfg = ler_json(os.path.join(CONFIG, "alertas.json"), {})
    if not cfg.get("ativo"):
        return
    enviados_path = os.path.join(DATA, "alertas_enviados.json")
    enviados = set(ler_json(enviados_path, []))
    novos = []
    for a in cfg.get("alertas", []):
        alvo = (a.get("destino") or "*").strip()
        for r in ida:
            if alvo not in ("*", "", r["d"], r["rd"]):
                continue
            if a.get("ida_de") and r["dt"] < a["ida_de"]:
                continue
            if a.get("ida_ate") and r["dt"] > a["ida_ate"]:
                continue
            v = r["c"].get(a.get("cabine") or "Y")
            if not v or v[0] > int(a.get("max_milhas") or 10**9):
                continue
            chave = f'{r["id"]}-{a.get("cabine")}-{v[0]}'
            if chave in enviados:
                continue
            enviados.add(chave)
            tx = f" + R$ {v[1]} taxas" if v[1] is not None else ""
            novos.append(f'• {r["o"]}→{r["d"]} {r["dt"]} {NOMES_CAB.get(a.get("cabine"), "")}: '
                         f'{v[0]:,} milhas{tx} ({r["p"]})'.replace(",", ".") + "\n  " + link_busca(r, a.get("cabine") or "Y"))
    if novos:
        cab = f"✈️ {len(novos)} passagem(ns) nos seus alertas:\n\n"
        lote = novos[:25] if cfg.get("modo") == "resumo" else novos
        if enviar_telegram(cab + "\n".join(lote)):
            salvar_json(enviados_path, sorted(enviados)[-5000:])


def main():
    global max_chamadas
    cfg = ler_json(os.path.join(CONFIG, "config.json"), {})
    modo = "pro" if os.environ.get("SEATS_AERO_API_KEY") else "gratis"
    total = int(cfg.get("max_chamadas_por_rodada", 450))
    limite_links = int(cfg.get("links_diretos_por_rodada", 150))
    max_chamadas = max(50, total - limite_links)   # reserva chamadas para os links diretos
    os.makedirs(DATA, exist_ok=True)
    agora = datetime.now(timezone.utc)
    hoje_str = agora.date().isoformat()
    print(f"Modo: {modo}")

    print("Câmbio...")
    cambios = cambio()
    sessao = None
    if modo == "gratis":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import gratis
        ida, volta, nacional, sessao, usadas = gratis.coletar(
            cfg, cambios, BRASIL, int(cfg.get("max_consultas_gratis", 300)))
    else:
        print("Internacional...")
        ida, volta = coletar_internacional(cfg, cambios)
        print("Nacional...")
        nacional = coletar_nacional(cfg, cambios)

    historico = ler_json(os.path.join(DATA, "historico.json"), {})
    historico = atualizar_historico(historico, ida + volta, hoje_str)
    achados = marcar_imperdiveis(ida + volta, historico, cfg.get("imperdivel_percentual_abaixo_da_media", 30))

    # imperdíveis: as atuais e as que sumiram (viram "passadas")
    imp = ler_json(os.path.join(DATA, "imperdiveis.json"), {"agora": [], "passadas": []})
    antes = {a["k"]: a for a in imp.get("agora", [])}
    atuais = {}
    for a in achados[:40]:
        a["visto_desde"] = antes.get(a["k"], {}).get("visto_desde", agora.isoformat())
        atuais[a["k"]] = a
    passadas = imp.get("passadas", [])
    for k, a in antes.items():
        if k not in atuais:
            a["sumiu_em"] = agora.isoformat()
            passadas.insert(0, a)
    imp = {"agora": list(atuais.values()), "passadas": passadas[:100]}

    salvar_json(os.path.join(DATA, "voos.json"), {
        "atualizado": agora.isoformat(), "cambio": {k: cambios.get(k) for k in ("USD", "EUR", "CAD", "GBP")},
        "ida": ida, "volta": volta, "nacional": nacional, "demo": False, "modo": modo,
    })
    salvar_json(os.path.join(DATA, "historico.json"), historico)
    salvar_json(os.path.join(DATA, "imperdiveis.json"), imp)
    print("Links...")
    if modo == "gratis":
        cache = ler_json(os.path.join(DATA, "links.json"), {})
        cache, _ = gratis.links_emissao(sessao, ida + volta, imp["agora"], cache,
                                         int(cfg.get("links_gratis_por_rodada", 60)))
        salvar_json(os.path.join(DATA, "links.json"), cache)
    else:
        max_chamadas = total
        buscar_links(ida + volta + nacional, imp["agora"], limite_links)
    processar_alertas(ida)
    print(f"Pronto: {len(ida)} idas, {len(volta)} voltas, {len(nacional)} nacionais, "
          f"{len(imp['agora'])} imperdíveis.")


if __name__ == "__main__":
    main()
