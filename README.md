# ✈️ Buscador de Milhas (uso pessoal)

Site que mostra passagens internacionais com milhas, ida e volta, saindo de Rio Preto (SJP) com conexão em Guarulhos (GRU) ou Viracopos (VCP).

- `index.html`: o site (abas Buscar, Aprenda e Alertas)
- `scripts/coletar.py`: robô que busca os dados no Seats.aero
- `.github/workflows/coletar.yml`: roda o robô 2x por dia (06:17 e 18:17)
- `config/config.json`: aeroportos, quantos dias à frente, critério de "imperdível" (30% abaixo da média)
- `config/alertas.json`: alertas do Telegram (opcional)
- `data/`: resultados gerados pelo robô (não editar)

## Modo grátis (padrão)

Sem nenhuma configuração, o robô usa a **busca pública e gratuita do Seats.aero**: voos dos próximos **60 dias**, até 1.000 consultas por dia. Cada rodada faz ~300 consultas (`max_consultas_gratis`).
O botão "ir para a passagem" abre a página do Seats.aero daquele voo com as instruções de emissão.

## Modo Pro (opcional, pago)

Se um dia quiser ver o ano inteiro, assine o Seats.aero Pro e siga os passos abaixo; o robô troca de modo sozinho quando encontra a chave.

### Configuração do Pro

1. **Seats.aero Pro**: assine em seats.aero e gere a chave em *Settings → API*.
2. **Chave no GitHub**: no repositório, *Settings → Secrets and variables → Actions → New repository secret*.
   Nome: `SEATS_AERO_API_KEY`, valor: sua chave.
3. **Ligar o site**: *Settings → Pages → Branch: main / root → Save*. O endereço aparece ali em 1–2 minutos.
4. **Primeira coleta**: aba *Actions → Atualizar passagens → Run workflow*. Depois disso roda sozinho.

Enquanto o robô não rodou, o site mostra dados de exemplo (com um aviso amarelo no topo).

## Alertas pelo Telegram (opcional)

Veja a aba **Alertas** do site: ela gera o bloco para colar em `config/alertas.json` e explica como criar o bot.
Crie os secrets `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` e mude `"ativo": true`.

## Limites

- A API Pro permite 1.000 chamadas por dia; o robô usa no máximo 450 por rodada (`max_chamadas_por_rodada`), 2 rodadas por dia.
- **Link direto para a passagem**: vem do campo `booking_links` do Seats.aero (1 chamada por passagem). O robô busca para as imperdíveis e as 3 mais baratas de cada rota/classe, até 150 novas por rodada (`links_diretos_por_rodada`), e guarda em `data/links.json`. As outras passagens mostram o link do Seats.aero e do site do programa.
- O Seats.aero não cobre o LATAM Pass. Smiles e Azul estão incluídos, além de ~23 programas estrangeiros.
- O trecho Rio Preto ↔ São Paulo só aparece se o Seats.aero acompanhar essa rota; senão o site mostra links para conferir na Azul/Smiles.
- Dados de disponibilidade: seats.aero.
