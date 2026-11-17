#!/usr/bin/env python3
"""
Caçador de promoções de TRANSFERÊNCIA BONIFICADA da Livelo para programas de milhas.

Lê os feeds (RSS) de blogs brasileiros de milhas, separa as notícias de
"transfira pontos Livelo para X com até N% de bônus" e grava em data/promos_livelo.json.
Opcional: avisa no Telegram quando aparece promoção nova com bônus alto.
Só biblioteca padrão.
"""
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAIDA = os.path.join(RAIZ, "data", "promos_livelo.json")
CONFIG = os.path.join(RAIZ, "config", "config.json")

FEEDS = [
    ("Pontos pra Voar", "https://pontospravoar.com/feed/"),
    ("Passageiro de Primeira", "https://passageirodeprimeira.com/feed/"),
    ("Melhores Destinos", "https://www.melhoresdestinos.com.br/feed"),
    ("Cartões e Viagens", "https://cartoeseviagens.com.br/feed"),
]

# nome que aparece no texto -> código do programa (mesmos códigos do site)
PROGRAMAS = [
    (r"\bsmiles\b", "smiles"),
    (r"\b(azul fidelidade|tudoazul|azul)\b", "azul"),
    (r"\b(latam pass|latam)\b", "latam"),
    (r"\b(tap|miles&go|miles & go)\b", "tap"),
    (r"\b(iberia|iberia plus|avios)\b", "iberia"),
    (r"\b(connectmiles|copa)\b", "copa"),
    (r"\b(all accor|accor)\b", "accor"),
    (r"\b(aeroplan)\b", "aeroplan"),
    (r"\b(flying blue)\b", "flyingblue"),
]
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}
MESES = {m: i for i, m in enumerate(["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                                     "agosto", "setembro", "outubro", "novembro", "dezembro"], 1)}


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (buscador-milhas pessoal)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read()


def limpar(txt):
    txt = re.sub(r"<[^>]+>", " ", txt or "")
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()


def programa_de(texto):
    t = texto.lower()
    achados = [cod for padrao, cod in PROGRAMAS if re.search(padrao, t)]
    return achados[0] if achados else None


def bonus_de(texto):
    """Maior percentual de bônus citado ('até 130% de bônus', '100% de bônus')."""
    nums = [int(n) for n in re.findall(r"(\d{2,3})\s?%\s*(?:de\s+)?b[ôo]nus", texto.lower())]
    nums += [int(n) for n in re.findall(r"b[ôo]nus\s+de\s+(?:at[ée]\s+)?(\d{2,3})\s?%", texto.lower())]
    nums = [n for n in nums if 10 <= n <= 300]
    return max(nums) if nums else None


def validade_de(texto, ano_ref):
    """Procura 'até o dia 6 de novembro', 'válida até 17/11', 'somente hoje, 27/08/2026'."""
    t = texto.lower()
    m = re.search(r"at[ée]\s+(?:o\s+dia\s+)?(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")", t)
    if m:
        return f"{ano_ref}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"(?:at[ée]|hoje,?)\s+(?:o\s+dia\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t)
    if m:
        ano = int(m.group(3)) if m.group(3) else ano_ref
        ano = ano + 2000 if ano < 100 else ano
        return f"{ano}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def linhas_radar(texto_html, publicado):
    """Tabelas do 'Radar PPV': | Programa | Parceiro | Bonificação | Validade |."""
    promos = []
    for linha in re.findall(r"<tr[^>]*>(.*?)</tr>", texto_html or "", flags=re.S | re.I):
        cels = [limpar(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", linha, flags=re.S | re.I)]
        if len(cels) < 4 or "livelo" not in cels[1].lower():
            continue
        prog = programa_de(cels[0])
        pct = re.search(r"(\d{2,3})\s?%", cels[2])
        if not prog or not pct:
            continue
        val = None
        m = re.search(r"(\d{1,2})-(" + "|".join(MESES) + r")", cels[3].lower())
        if m:
            val = f"{publicado.year}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"
        promos.append({"prog": prog, "bonus": int(pct.group(1)), "validade": val})
    return promos


def coletar():
    novas = []
    for fonte, url in FEEDS:
        try:
            raiz = ET.fromstring(baixar(url))
        except Exception as e:
            print(f"  {fonte}: erro ao ler o feed ({e})")
            continue
        n = 0
        for item in raiz.iter("item"):
            titulo = limpar(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            corpo_html = item.findtext("content:encoded", namespaces=NS) or item.findtext("description") or ""
            corpo = limpar(corpo_html)
            try:
                pub = parsedate_to_datetime(item.findtext("pubDate"))
            except Exception:
                pub = datetime.now(timezone.utc)
            texto = f"{titulo}. {corpo[:1500]}"
            # 1) Radar com tabela de transferências
            for p in linhas_radar(corpo_html, pub):
                novas.append({**p, "titulo": titulo, "link": link, "fonte": fonte,
                              "publicado": pub.astimezone(timezone.utc).isoformat(), "de": "tabela"})
                n += 1
            # 2) Notícia de transferência Livelo -> programa
            tl = titulo.lower()
            if "livelo" not in tl or not re.search(r"transf|b[ôo]nus", tl):
                continue
            prog = programa_de(re.sub(r"livelo", "", tl)) or programa_de(corpo.lower().replace("livelo", ""))
            bonus = bonus_de(titulo) or bonus_de(corpo[:1500])
            if not prog or not bonus:
                continue
            novas.append({"prog": prog, "bonus": bonus, "validade": validade_de(texto, pub.year),
                          "titulo": titulo, "link": link, "fonte": fonte,
                          "publicado": pub.astimezone(timezone.utc).isoformat(), "de": "notícia"})
            n += 1
        print(f"  {fonte}: {n} menções de transferência Livelo")
    return novas


def ler(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def avisar(novas, minimo):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    boas = [p for p in novas if p["bonus"] >= minimo]
    if not boas or not token or not chat:
        return
    txt = "💳 Transferência bonificada Livelo:\n\n" + "\n".join(
        f"• {p['prog'].upper()}: até {p['bonus']}% de bônus" + (f" (até {p['validade'][8:]}/{p['validade'][5:7]})" if p.get("validade") else "")
        + f"\n  {p['link']}" for p in boas[:10])
    dados = urllib.parse.urlencode({"chat_id": chat, "text": txt, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=dados, timeout=30)
    except Exception as e:
        print("  telegram: erro", e)


def main():
    cfg = ler(CONFIG, {})
    agora = datetime.now(timezone.utc)
    base = ler(SAIDA, {"promos": []})
    conhecidas = {(p["prog"], p["bonus"], p["link"]) for p in base["promos"]}
    todas = base["promos"]
    novas = []
    for p in coletar():
        k = (p["prog"], p["bonus"], p["link"])
        if k not in conhecidas:
            conhecidas.add(k)
            p["visto_em"] = agora.isoformat()
            todas.append(p)
            novas.append(p)
    # guarda 1 ano de histórico
    limite = (agora - timedelta(days=365)).isoformat()
    todas = sorted([p for p in todas if p["publicado"] >= limite], key=lambda p: p["publicado"], reverse=True)
    os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as f:
        json.dump({"atualizado": agora.isoformat(), "promos": todas}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Pronto: {len(novas)} novas, {len(todas)} no histórico")
    alertas = ler(os.path.join(RAIZ, "config", "alertas.json"), {})
    if alertas.get("ativo") and base["promos"]:   # não avisa na primeira carga
        avisar(novas, int(cfg.get("alerta_transferencia_bonus_minimo", 80)))


if __name__ == "__main__":
    main()
