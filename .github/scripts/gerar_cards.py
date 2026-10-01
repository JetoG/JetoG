"""Gera os cards SVG do README (resumo, linguagens e sequência) incluindo repositórios privados.

Roda na GitHub Action com o token salvo no secret CARDS_TOKEN.
Só usa a biblioteca padrão do Python, então não precisa de pip install.
"""
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

TOKEN = os.environ["GH_TOKEN"]
SAIDA = Path("assets")

# Linguagens que não entram no card (templates/estilo/scripts auxiliares inflam o total)
IGNORAR = {"HTML", "CSS", "Batchfile", "Shell", "PowerShell"}
MAX_LINGUAGENS = 6
# Nomes encurtados para caber no card
APELIDOS = {"Game Maker Language": "GameMaker (GML)"}

TEMAS = {
    "dark": {"fundo": "#0d1117", "borda": "#476AE1", "titulo": "#e6edf3", "texto": "#9198a1",
             "valor": "#e6edf3", "destaque": "#476AE1", "trilha": "#21262d"},
    "light": {"fundo": "#ffffff", "borda": "#476AE1", "titulo": "#1f2328", "texto": "#59636e",
              "valor": "#1f2328", "destaque": "#476AE1", "trilha": "#eaeef2"},
}
FONTE = "'Segoe UI', Ubuntu, 'Helvetica Neue', Arial, sans-serif"


def graphql(query, variables=None):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        dados = json.load(resp)
    if "errors" in dados:
        raise RuntimeError(dados["errors"])
    return dados["data"]


def buscar_dados():
    viewer = graphql("{ viewer { id login createdAt } }")["viewer"]

    repos, cursor = [], None
    while True:
        pagina = graphql("""
            query($id: ID!, $cursor: String) {
              viewer {
                repositories(ownerAffiliations: OWNER, isFork: false, first: 50, after: $cursor) {
                  pageInfo { hasNextPage endCursor }
                  nodes {
                    stargazerCount
                    defaultBranchRef { target { ... on Commit { history(author: {id: $id}) { totalCount } } } }
                    languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
                      edges { size node { name color } }
                    }
                  }
                }
              }
            }""", {"id": viewer["id"], "cursor": cursor})["viewer"]["repositories"]
        repos += pagina["nodes"]
        if not pagina["pageInfo"]["hasNextPage"]:
            break
        cursor = pagina["pageInfo"]["endCursor"]

    # Calendário de contribuições, um ano por vez (limite da API), desde a criação da conta
    hoje = dt.datetime.now(dt.timezone.utc).date()
    inicio = dt.date.fromisoformat(viewer["createdAt"][:10])
    blocos = [
        f'y{ano}: contributionsCollection(from: "{ano}-01-01T00:00:00Z", to: "{ano}-12-31T23:59:59Z") '
        "{ contributionCalendar { weeks { contributionDays { date contributionCount } } } }"
        for ano in range(inicio.year, hoje.year + 1)
    ]
    anos = graphql("{ viewer { " + " ".join(blocos) + " } }")["viewer"]
    dias = {}
    for ano in anos.values():
        for semana in ano["contributionCalendar"]["weeks"]:
            for dia in semana["contributionDays"]:
                data = dt.date.fromisoformat(dia["date"])
                if data <= hoje:
                    dias[data] = dia["contributionCount"]

    return viewer, repos, dias, hoje


def calcular(repos, dias, hoje):
    commits = sum(
        (r["defaultBranchRef"] or {}).get("target", {}).get("history", {}).get("totalCount", 0)
        for r in repos
    )

    linguagens = {}
    for r in repos:
        for e in r["languages"]["edges"]:
            nome = e["node"]["name"]
            if nome in IGNORAR:
                continue
            nome = APELIDOS.get(nome, nome)
            atual = linguagens.setdefault(nome, {"tamanho": 0, "cor": e["node"]["color"] or "#8b949e"})
            atual["tamanho"] += e["size"]
    total = sum(l["tamanho"] for l in linguagens.values()) or 1
    top = sorted(linguagens.items(), key=lambda kv: kv[1]["tamanho"], reverse=True)[:MAX_LINGUAGENS]
    top = [(nome, info["cor"], info["tamanho"] / total * 100) for nome, info in top]

    # Sequência atual: se hoje ainda não teve contribuição, conta a partir de ontem
    atual, dia = 0, hoje if dias.get(hoje) else hoje - dt.timedelta(days=1)
    while dias.get(dia):
        atual += 1
        dia -= dt.timedelta(days=1)

    maior, corrida, fim_maior = 0, 0, None
    for data in sorted(dias):
        corrida = corrida + 1 if dias[data] else 0
        if corrida > maior:
            maior, fim_maior = corrida, data
    inicio_maior = fim_maior - dt.timedelta(days=maior - 1) if fim_maior else None

    return {
        "commits": commits,
        "repos": len(repos),
        "estrelas": sum(r["stargazerCount"] for r in repos),
        "contrib_total": sum(dias.values()),
        "contrib_ano": sum(v for d, v in dias.items() if d.year == hoje.year),
        "ano": hoje.year,
        "linguagens": top,
        "seq_atual": atual,
        "seq_maior": maior,
        "seq_maior_periodo": (inicio_maior, fim_maior),
        "atualizado": hoje,
    }


def numero_br(valor, casas=0):
    """1234.5 -> '1.234,5' (formato brasileiro)."""
    return f"{valor:,.{casas}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def moldura(largura, altura, titulo, corpo, t):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largura}" height="{altura}" '
        f'viewBox="0 0 {largura} {altura}" font-family="{FONTE}">'
        f'<rect x="0.5" y="0.5" width="{largura - 1}" height="{altura - 1}" rx="6" '
        f'fill="{t["fundo"]}" stroke="{t["borda"]}"/>'
        f'<text x="24" y="36" font-size="17" font-weight="600" fill="{t["titulo"]}">{escape(titulo)}</text>'
        f"{corpo}</svg>"
    )


def card_resumo(s, t):
    itens = [
        ("Commits", s["commits"]),
        (f"Contribuições em {s['ano']}", s["contrib_ano"]),
        ("Contribuições no total", s["contrib_total"]),
        ("Repositórios (públicos + privados)", s["repos"]),
    ]
    corpo = "".join(
        f'<text x="24" y="{70 + i * 26}" font-size="14" fill="{t["texto"]}">{escape(nome)}</text>'
        f'<text x="376" y="{70 + i * 26}" font-size="14" font-weight="600" fill="{t["valor"]}" '
        f'text-anchor="end">{numero_br(valor)}</text>'
        for i, (nome, valor) in enumerate(itens)
    )
    return moldura(400, 180, "Resumo no GitHub", corpo, t)


def card_linguagens(s, t):
    largura_barra, x, barra = 352, 24, ""
    for nome, cor, pct in s["linguagens"]:
        w = largura_barra * pct / 100
        barra += f'<rect x="{x:.2f}" y="54" width="{w:.2f}" height="8" fill="{cor}"/>'
        x += w
    barra = (
        f'<clipPath id="c"><rect x="24" y="54" width="{largura_barra}" height="8" rx="4"/></clipPath>'
        f'<rect x="24" y="54" width="{largura_barra}" height="8" rx="4" fill="{t["trilha"]}"/>'
        f'<g clip-path="url(#c)">{barra}</g>'
    )
    legenda = ""
    for i, (nome, cor, pct) in enumerate(s["linguagens"]):
        cx, cy = 24 + (i % 2) * 180, 90 + (i // 2) * 26
        legenda += (
            f'<circle cx="{cx + 5}" cy="{cy - 5}" r="5" fill="{cor}"/>'
            f'<text x="{cx + 16}" y="{cy}" font-size="13" fill="{t["texto"]}">{escape(nome)} '
            f'<tspan fill="{t["valor"]}" font-weight="600">{numero_br(pct, 1)}%</tspan></text>'
        )
    return moldura(400, 180, "Linguagens mais usadas", barra + legenda, t)


def card_sequencia(s, t):
    def data_br(d):
        return d.strftime("%d/%m/%Y") if d else "-"

    ini, fim = s["seq_maior_periodo"]
    colunas = [
        (numero_br(s["contrib_total"]), "Contribuições", "desde a criação da conta"),
        (str(s["seq_atual"]), "Sequência atual", "dias seguidos"),
        (str(s["seq_maior"]), "Maior sequência", f"{data_br(ini)} – {data_br(fim)}"),
    ]
    corpo = ""
    for i, (valor, rotulo, sub) in enumerate(colunas):
        cx = 136 + i * 264
        cor = t["destaque"] if i == 1 else t["valor"]
        corpo += (
            f'<text x="{cx}" y="84" font-size="30" font-weight="700" fill="{cor}" text-anchor="middle">{valor}</text>'
            f'<text x="{cx}" y="112" font-size="14" font-weight="600" fill="{t["titulo"]}" text-anchor="middle">{rotulo}</text>'
            f'<text x="{cx}" y="132" font-size="12" fill="{t["texto"]}" text-anchor="middle">{escape(sub)}</text>'
        )
        if i:
            corpo += f'<line x1="{cx - 132}" y1="58" x2="{cx - 132}" y2="136" stroke="{t["trilha"]}" stroke-width="2"/>'
    corpo += (
        f'<text x="{816 - 24}" y="36" font-size="11" fill="{t["texto"]}" text-anchor="end">'
        f'atualizado em {data_br(s["atualizado"])}</text>'
    )
    return moldura(816, 156, "Sequência de contribuições", corpo, t)


def main():
    _, repos, dias, hoje = buscar_dados()
    s = calcular(repos, dias, hoje)
    SAIDA.mkdir(exist_ok=True)
    for nome_tema, tema in TEMAS.items():
        for nome, gerar in (("resumo", card_resumo), ("linguagens", card_linguagens), ("sequencia", card_sequencia)):
            (SAIDA / f"{nome}-{nome_tema}.svg").write_text(gerar(s, tema), encoding="utf-8")
    print(json.dumps({k: v for k, v in s.items() if k not in ("seq_maior_periodo", "atualizado")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
