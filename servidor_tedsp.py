# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=1.4.0,<2", "httpx>=0.27", "truststore>=0.9"]
# (mcp<2 de propósito: o 2.x renomeou FastMCP → MCPServer e o registro das tools falha em silêncio)
# ///
"""MCP — Ementário do Tribunal de Ética e Disciplina da OAB/SP (índice local).

Expõe:
  buscar_ementas_ted_sp                 1ª Turma de Ética Profissional (deontológica, consultas) — busca no índice local
  obter_parecer_ted_sp                  ementa + relatório + parecer integrais de uma consulta, com link estável
  verificar_citacao_ted_sp              confere trecho literal (reabre na fonte) antes de ir entre aspas
  buscar_ementas_disciplinares_ted_sp   Turmas Disciplinares (acórdãos 2010–2014, base antiga) — índice local
  obter_acordao_disciplinar_ted_sp      acórdão disciplinar integral pelo idEmenta
  pesquisar_tema_no_portal_ted_sp       busca DIRIGIDA: pesquisa o termo no portal (título), baixa e indexa só o que casar
  sincronizar_base_ted_sp               atualização incremental, com orçamento de requisições por chamada
  buscar_jurisprudencia_cfoab           Conselho Federal da OAB — busca AO VIVO na API do portal (jurisprudencia.oab.org.br)
  obter_ementa_cfoab                    ementa + acórdão integrais por número de processo
  verificar_citacao_cfoab               confere trecho literal reabrindo o portal
  buscar_normas_cfoab                   súmulas, provimentos, resoluções, INs e portarias do Conselho Federal
  diagnostico_base_ted_sp               cobertura, última sincronização, estado do disjuntor — sem rede

Backend (mapeado pelo navegador em 19/09/2026 — sem WAF, sem captcha, sem login):
  · Deontológica: POST www.oabsp.org.br/_ajax/ementario.php  {card, ano, action: "ano"|"load-more"|"search-keyword", start, keyword}
    → JSON {err, msg (HTML da lista, 12 por página), btnMore}. Anos 1994–hoje. A busca por palavra do site casa
    só com o título da ementa — por isso a busca deste servidor roda em índice local (SQLite FTS5) do texto integral.
    Inteiro teor: POST _ajax/load-modal.php {bracket: 5, slug, guid} → JSON {title, html}.
  · Disciplinar: POST www2.oabsp.org.br/asp/tribunal_etica/ementasTribunais.asp {proc: 1, anoreq: "AAAA/M"|0, texto1, texto2}
    (sem proc=1 a página volta vazia) → HTML com data + ementa + idEmenta; texto em textoEmentas.asp?idEmenta=N
    (acórdão nº, turma, relator, pena). Cobertura do portal: só 2010–2014.
  · Conselho Federal: GET jurisprudencia.oab.org.br/api/jurisprudencia/v1/portal/pesquisa/contexto/{EMENTA|DECISAO_SUMULA|…}
    ?pesquisaLivre=&modoPesquisaLivre={TODOS_OS_TERMOS|UM_DOS_TERMOS|FRASE_EXATA}&numeroProcesso=&page=0&size=20&sort={score|dataOrdenacao},desc
    + filtros idsOrganizacao, orgaoJulgador, relatoresIdMembro, tipoEmentas (ids vêm das facetas da própria resposta). API REST
    do SPA do portal, sem WAF; busca textual no servidor → aqui NÃO há índice local. Limitador e disjuntor separados por host.
Natureza: o TED não é tribunal judicial. Ementa deontológica é orientação em tese (art. 71, II, do CED); acórdão
disciplinar é divulgado "em caráter didático" (nota da Corregedoria). Nenhum dos dois é precedente vinculante.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import gzip
import hashlib
import platform
import threading
import urllib.parse
from typing import Any

try:
    import fcntl  # POSIX
except Exception:  # pragma: no cover
    fcntl = None

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

try:
    import httpx
except Exception:  # permite --selftest offline sem a lib
    httpx = None

import logging

logging.getLogger("httpx").setLevel(logging.WARNING)

VERSAO = "1.4.0"
REPO_GITHUB = "robertogecia/mcp-oab-jurisprudencia"
SITE = "https://www.oabsp.org.br"
URL_LISTA = SITE + "/_ajax/ementario.php"
URL_MODAL = SITE + "/_ajax/load-modal.php"
PAGINA_TED = SITE + "/tribunal-de-etica-e-disciplina"
SITE2 = "https://www2.oabsp.org.br"
URL_DISC = SITE2 + "/asp/tribunal_etica/ementasTribunais.asp"
URL_DISC_TEXTO = SITE2 + "/asp/tribunal_etica/textoEmentas.asp?idEmenta="
CFOAB = "https://jurisprudencia.oab.org.br"
CFOAB_API = CFOAB + "/api/jurisprudencia/v1/portal/pesquisa/contexto/"
CARD = "Turma de Ética Profissional"
POR_PAGINA_SITE = 12
ANO_INICIAL = 1994

# Dados (índice, recibos, disjuntor). Prefira um lugar FORA de pasta sincronizada em nuvem (OneDrive, iCloud, Dropbox):
# SQLite em WAL e sincronizador de nuvem brigam pelo mesmo arquivo. TEDSP_DADOS é o nome antigo e continua valendo.
PASTA = os.path.dirname(os.path.abspath(__file__))
PASTA_DADOS = os.environ.get("TEDSP_DIR_DADOS") or os.environ.get("TEDSP_DADOS") or os.path.join(PASTA, "dados")
ARQUIVO_DB = os.path.join(PASTA_DADOS, "tedsp.db")
PASTA_RECIBOS = os.environ.get("TEDSP_DIR_RECIBOS") or os.path.join(PASTA_DADOS, "recibos")
_ARQUIVO_ESTADO = os.path.join(PASTA_DADOS, ".disjuntor_estado_tedsp.json")

# Ritmo por portal. Os dois portais são de terceiros e não dizem o próprio limite. O que foi MEDIDO no CMS da OAB/SP
# (19/09/2026): 503 avulso depois de ~35 a ~270 requisições a 2 s cada (≈ 300 em 10 min) — os tetos abaixo ficam muito
# aquém disso de propósito. São tetos de proteção, não limites medidos: quem os sobe assume o risco de derrubar um
# servidor que é de todos. A API do Conselho Federal nunca recusou nesta medição, mas também não é dele o direito de
# ser martelada. Escada: cada recusa do portal aperta o ritmo um degrau; SUCESSOS_PARA_RELAXAR consultas limpas descem um.
ESCADA = {"oabsp": [(2.0, 30), (6.0, 12), (20.0, 6), (60.0, 3)],
          "cfoab": [(1.5, 60), (4.0, 20), (15.0, 8), (60.0, 3)]}
JANELA_S = 600
DIA_MAX = {"oabsp": 300, "cfoab": 600}
SUCESSOS_PARA_RELAXAR = 100
_COOLDOWN_INICIAL_S = {"oabsp": 2 * 3600.0, "cfoab": 3600.0}
_COOLDOWN_MAX_S = 24 * 3600.0
PAUSA_ILEGIVEL_S = 3600.0
_PAUSA_503_S = 45.0
_RE_BLOQUEIO = re.compile(r"(?i)access denied|acesso negado|too many requests|captcha|cloudflare|request blocked|forbidden")
# A carga pelo terminal (`--sincronizar`, feita UMA vez para montar o pacote) respeita espaçamento, escada e pausa, mas
# não os tetos por janela/dia — senão uma carga inicial levaria semanas. Nenhuma ferramenta MCP liga este modo.
_MODO_CARGA = False

# User-Agent HONESTO por padrão: o cliente se identifica como o que é. Medido em 23/09/2026: os três endereços usados
# (API do Conselho Federal, CMS e ASP da OAB/SP) respondem normalmente a ele. Quem precisar de outro (rede corporativa
# que só deixa passar navegador) define TEDSP_USER_AGENT — decisão e responsabilidade de quem define.
USER_AGENT_PADRAO = "mcp-oab-jurisprudencia/{versao} (pesquisa juridica; cliente MCP; ritmo limitado)"
HEADERS = {
    "User-Agent": (os.environ.get("TEDSP_USER_AGENT") or USER_AGENT_PADRAO).replace("{versao}", VERSAO),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
# Modo híbrido: bases que o usuário assina e onde as saídas devem mandar procurar o que este servidor não cobre.
COMPLEMENTOS = [c.strip() for c in os.environ.get("TEDSP_COMPLEMENTOS", "").split(";") if c.strip()]


def _garantir_pasta(caminho: str) -> None:
    """Pasta de dados com permissão 0700: o índice é público, mas recibos e o histórico de temas dizem o que você pesquisou."""
    os.makedirs(caminho, mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(caminho, 0o700)  # makedirs não corrige pasta que já existia com 0755


def onde_mais_procurar() -> str:
    """O parágrafo híbrido: o que fazer com o que este servidor não cobre, com ou sem outras ferramentas."""
    if COMPLEMENTOS:
        return f"Para o que falta, consulte também: {', '.join(COMPLEMENTOS)} (declarado em TEDSP_COMPLEMENTOS)."
    return ("Para o que falta: o portal oficial de cada órgão (jurisprudencia.oab.org.br para o Conselho Federal; o ementário em "
            "oabsp.org.br/tribunal-de-etica-e-disciplina para o TED-SP) ou outra base que você assine.")

AVISO_NATUREZA_DEONT = ("Natureza: orientação em tese da 1ª Turma de Ética Profissional do TED-OAB/SP (consulta, art. 71, II, do CED) — "
                        "não vincula outro TED nem o Judiciário; cite como orientação deontológica, não como precedente.")
AVISO_NATUREZA_CFOAB = ("Natureza: decisão de órgão do Conselho Federal da OAB (última instância administrativa do sistema OAB) ou, quando indicado, "
                        "de Câmara Recursal de Seccional. Orienta TEDs e Seccionais; não é jurisprudência judicial e não vincula o Judiciário. "
                        "Ementa de órgão fracionário não é súmula: súmulas do Conselho Pleno/Órgão Especial se consultam em `buscar_normas_cfoab`.")
AVISO_NATUREZA_DISC = ("Natureza: acórdão disciplinar divulgado pela Corregedoria do TED-OAB/SP em caráter didático (processo sigiloso, "
                       "art. 72, §2º, do EAOAB). Base do portal cobre só 2010–2014. Não é precedente vinculante.")


# ───────────────────────── texto ─────────────────────────

def _fold(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").lower()


def _html_para_texto(h: str) -> str:
    """HTML do CMS → texto com parágrafos preservados; descarta <script> (o modal traz um JSON-LD no fim)."""
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", h or "")
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    t = html.unescape(h).replace("\xa0", " ")
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _uma_linha(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def _so_digitos(t: str) -> str:
    return re.sub(r"\D", "", t or "")


_RE_ELIPSE = re.compile(r"\s*(?:\[\s*(?:\.{3}|…|\.\s*\.\s*\.)\s*\]|\(\s*(?:\.{3}|…)\s*\)|…)\s*")


def _fragmentos(trecho: str) -> list[str]:
    """'A [...] B' → ['A', 'B']. Omissão marcada pelo advogado não pode reprovar a conferência,
    mas a ORDEM importa: fragmento fora de ordem é paráfrase remontada, não citação literal."""
    return [f for f in (x.strip() for x in _RE_ELIPSE.split(trecho or "")) if len(f) >= 3]


def _contem_fragmentos(texto_norm: str, trecho: str) -> bool:
    """True se todos os fragmentos do trecho aparecem, na ordem, dentro de UM mesmo campo já normalizado."""
    pos = 0
    for f in _fragmentos(trecho):
        alvo = _normalizar_para_comparar(f)
        i = texto_norm.find(alvo, pos)
        if i < 0:
            return False
        pos = i + len(alvo)
    return True


def _normalizar_para_comparar(t: str) -> str:
    t = _fold(t)
    t = re.sub(r"[“”\"'‘’`´]", "", t)
    t = re.sub(r"[–—−-]", "-", t)
    return re.sub(r"\s+", " ", re.sub(r"\s*([.,;:()\-/])\s*", r"\1", t)).strip()


_RE_FECHO = re.compile(r"(?is)\bProc(?:esso)?s?\.?\s*(?:n[ºo°.]*\s*)?(?P<proc>(?:E\s*-\s*)?\d[\d.\-/]*\d)\s*[-–—,]?\s*(?P<resto>(?:(?!\bProc)[\s\S]){0,600}?\bPresidente\b[^\n]{0,160})")
_RE_VOTACAO = re.compile(r"(?i)\b(v\.\s*u\.|v\.\s*m\.|vota[çc][ãa]o un[âa]nime|por unanimidade|por maioria)")
_RE_DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_RE_REL = re.compile(r"(?i)\bRel(?:ator|atora|\.)?a?\.?\s*(?:designad[oa]\s*|origin[áa]ri[oa]\s*)?(?:Dr[aª]?\.?\s*)?(?P<n>[A-ZÀ-Ü][^,;\-–—\n]{3,80}?)\s*(?=[,;\-–—]|\s+Rev|\s+Pres|$)")
_RE_REV = re.compile(r"(?i)\bRev(?:isor|isora|\.)?a?\.?\s*(?:Dr[aª]?\.?\s*)?(?P<n>[A-ZÀ-Ü][^,;\-–—\n]{3,80}?)\s*(?=[,;\-–—]|\s+Pres|$)")
_RE_PRES = re.compile(r"(?i)\bPresidente\s*(?:em exerc[íi]cio\s*)?(?:Dr[aª]?\.?\s*)?(?P<n>[A-ZÀ-Ü][^,;\n]{3,80}?)\s*(?=[.,;\n]|$)")


def _parse_parecer(texto: str) -> dict:
    """Separa a ementa (até o fecho 'Proc. … Presidente …') do relatório/parecer e extrai os dados do fecho.
    Extração é por regex sobre texto livre de 30 anos de formatos: o que não casar fica vazio, nunca inventado."""
    d = {"ementa": "", "inteiro": "", "fecho": "", "processo": "", "votacao": "", "data_julgamento": "", "relator": "", "revisor": "", "presidente": ""}
    m = _RE_FECHO.search(texto)
    if not m:
        d["ementa"] = texto
        return d
    fim = m.end()
    ponto = texto.find(".", fim - 1, fim + 3)
    if ponto >= 0:
        fim = ponto + 1
    d["ementa"] = texto[:fim].strip()
    d["inteiro"] = texto[fim:].strip()
    fecho = _uma_linha(texto[m.start():fim])
    d["fecho"] = fecho
    d["processo"] = _uma_linha(m.group("proc"))
    v = _RE_VOTACAO.search(fecho)
    if v:
        bruto = _fold(v.group(1)).replace(" ", "")
        d["votacao"] = "v.u." if bruto in ("v.u.", "votacaounanime", "porunanimidade") else "v.m."
    dt = _RE_DATA.search(fecho)
    if dt:
        d["data_julgamento"] = f"{int(dt.group(3)):04d}-{int(dt.group(2)):02d}-{int(dt.group(1)):02d}"
    for chave, rx in (("relator", _RE_REL), ("revisor", _RE_REV), ("presidente", _RE_PRES)):
        mm = rx.search(fecho)
        if mm:
            d[chave] = _uma_linha(mm.group("n")).rstrip(".")
    return d


_RE_ITEM_LISTA = re.compile(r'(?is)<p class="font-serif[^"]*"[^>]*>\s*<a[^>]*data-guid="(?P<guid>\d+)"[^>]*data-slug="(?P<slug>[^"]*)"[^>]*>(?P<numero>.*?)</a>\s*</p>\s*'
                            r'<p[^>]*>\s*<a[^>]*>(?P<titulo>.*?)</a>')


def _parse_lista(msg: str) -> list[dict]:
    return [{"guid": m.group("guid"), "slug": m.group("slug"), "numero": _uma_linha(_html_para_texto(m.group("numero"))),
             "titulo": _uma_linha(_html_para_texto(m.group("titulo")))} for m in _RE_ITEM_LISTA.finditer(msg or "")]


_RE_ITEM_DISC = re.compile(r"(?is)<b>\s*(?P<data>\d{2}/\d{2}/\d{4})\s*-\s*</b>\s*<a[^>]*textoEmentas\.asp\?idEmenta=(?P<id>\d+)[^>]*>(?P<ementa>.*?)</a>")


def _parse_lista_disc(pagina: str) -> list[dict]:
    out = []
    for m in _RE_ITEM_DISC.finditer(pagina or ""):
        d, mes, a = m.group("data").split("/")
        out.append({"id": int(m.group("id")), "data": f"{a}-{mes}-{d}", "ementa": _uma_linha(_html_para_texto(m.group("ementa")))})
    return out


_RE_DISC_ACORDAO = re.compile(r"(?i)Ac[óo]rd[ãa]o\s*N\s*o?\s*[º°]?\s*:?\s*(\d+)")
_RE_DISC_PROC = re.compile(r"(?i)processo disciplinar n\s*o?[º°.]?\s*([0-9A-Za-z./\-]+)")
_RE_DISC_TURMA = re.compile(r"(?i)membros d[ao]\s+(.{3,80}?Turma[^,]{0,40}?),")
_RE_DISC_REL = re.compile(r"(?i)\bRel\.?:?\s*(?:Dr[aª]?\.?\s*)?([^\-–—\n]{3,80}?)\s*[-–—]\s*Presidente")


def _parse_texto_disc(pagina: str) -> dict:
    t = _html_para_texto(pagina)
    t = re.sub(r"(?is)^.*?(?=Ac[óo]rd[ãa]o\s*N)", "", t, count=1) if re.search(r"(?i)Ac[óo]rd[ãa]o\s*N", t) else t
    t = re.sub(r"\bn\s+o\b", "nº", re.sub(r"\bN\s+o\s*:", "Nº:", t))
    plano = _uma_linha(t)
    g = lambda rx: (lambda m: _uma_linha(m.group(1)) if m else "")(rx.search(plano))
    return {"texto": t, "acordao": g(_RE_DISC_ACORDAO), "processo": g(_RE_DISC_PROC).rstrip(".,"), "turma": g(_RE_DISC_TURMA), "relator": g(_RE_DISC_REL)}


# ───────────────────────── consulta FTS ─────────────────────────

def _traduzir_consulta(consulta: str) -> str:
    """Sintaxe de advogado → FTS5: "frase exata", E/OU/NÃO (ou AND/OR/NOT), prefixo* e parênteses. Padrão entre termos: E."""
    out: list[str] = []
    for m in re.finditer(r'"([^"]+)"|(\(|\))|([^\s()"]+)', consulta or ""):
        frase, par, tok = m.groups()
        if frase:
            out.append('"' + re.sub(r"[^\w\s]", " ", frase, flags=re.UNICODE).strip() + '"')
        elif par:
            out.append(par)
        else:
            f = _fold(tok)
            if f in ("e", "and"):
                out.append("AND")
            elif f in ("ou", "or"):
                out.append("OR")
            elif f in ("nao", "not"):
                out.append("NOT")
            else:
                pref = tok.endswith("*")
                limpo = re.sub(r"[^\w]", " ", tok, flags=re.UNICODE).strip()
                for parte in limpo.split():
                    out.append(f'"{parte}"' + ("*" if pref else ""))
    while out and out[-1] in ("AND", "OR", "NOT"):
        out.pop()
    while out and out[0] in ("AND", "OR"):
        out.pop(0)
    return " ".join(out)


# ───────────────────────── citações (grafo) ─────────────────────────
# Medido sobre 74 pareceres reais (23/09/2026): 262 precedentes distintos citados e 126 dispositivos. O Estatuto aparece
# como EAOAB, EOAB, "Estatuto" e "Estatuto da Advocacia"; o Código de Ética como CED e por extenso; há listas ("artigos
# 7º, II e XIX, 34, VII, e 36 do Estatuto") e a variante "E. 6.093/2023". O extrator normaliza tudo para uma chave.
PARSER_VERSAO = 2  # suba quando parser ou extrator mudar: o índice se refaz do texto guardado, SEM rede

_RE_PREC_E = re.compile(r"\bE\s*[-–.]?\s*(\d)\.?(\d{3})\s*/\s*(\d{2,4})\b")
_RE_PREC_NOVO = re.compile(r"\b(\d{2}\.\d{4}\.\d{4}\.\d{5,6}-\d)\b")
_RE_DISP = re.compile(r"(?i)\bart(?:igo)?s?\.?\s*(\d{1,3}[^.;]{0,90}?)\b(EAOAB|EOAB|Estatuto(?:\s+da\s+Advocacia)?|CED|"
                      r"C[óo]digo\s+de\s+[ÉE]tica(?:\s+e\s+Disciplina)?|Regulamento\s+Geral|CPC|C[óo]digo\s+de\s+Processo\s+Civil|"
                      r"Constitui[çc][ãa]o(?:\s+Federal)?|CF)\b")
_RE_ART_NUM = re.compile(r"(?<![§\d])(?<!§ )\b(\d{1,3})(?:º|°|o)?\b(?!\s*[/.]\d)")


def _diploma(nome: str) -> str:
    f = _fold(nome)
    if f.startswith(("eaoab", "eoab", "estatuto")):
        return "EAOAB"
    if f.startswith(("ced", "codigo de etica")):
        return "CED"
    if f.startswith("regulamento"):
        return "RG"
    if f.startswith(("cpc", "codigo de processo")):
        return "CPC"
    return "CF"


def _canon_prec_e(d1: str, d3: str, ano: str) -> str:
    a = int(ano)
    if len(ano) == 2:
        a = 2000 + a if a <= 40 else 1900 + a
    return f"E-{d1}.{d3}/{a}"


def _canon_ref(texto: str) -> str | None:
    """Uma referência isolada (a que o usuário digita em `cita`) → a chave do grafo. None se não reconhecer."""
    t = _uma_linha(texto)
    m = _RE_PREC_E.search(t)
    if m:
        return _canon_prec_e(*m.groups())
    m = _RE_PREC_NOVO.search(t)
    if m:
        return m.group(1)
    m = _RE_DISP.search(t)
    if m:
        n = _RE_ART_NUM.search(m.group(1))
        return f"art. {int(n.group(1))} {_diploma(m.group(2))}" if n else None
    m = re.match(r"(?i)^\s*art(?:igo)?\.?\s*(\d{1,3})\D*$", t)  # "art. 10" sem diploma: o Estatuto, que é o mais citado
    return f"art. {int(m.group(1))} EAOAB" if m else None


def _extrair_citacoes(texto: str, proprio: str = "") -> set[tuple[str, str]]:
    """{(tipo, ref)} citados no parecer — 'ted' (precedente do TED-SP) e 'dispositivo'. Exclui o próprio processo."""
    t = _uma_linha(texto)
    proprio_c = _canon_ref(proprio) if proprio else None
    refs: set[tuple[str, str]] = set()
    for m in _RE_PREC_E.finditer(t):
        refs.add(("ted", _canon_prec_e(*m.groups())))
    for m in _RE_PREC_NOVO.finditer(t):
        refs.add(("ted", m.group(1)))
    for m in _RE_DISP.finditer(t):
        dip = _diploma(m.group(2))
        for n in _RE_ART_NUM.finditer(m.group(1)):
            if 0 < int(n.group(1)) < 400:
                refs.add(("dispositivo", f"art. {int(n.group(1))} {dip}"))
    if proprio_c:
        refs.discard(("ted", proprio_c))
    return refs


# ───────────────────────── montagem da consulta ─────────────────────────
# Portado do servidor do TJSE (harness de 21/09/2026): pergunta em português corrente funciona porque palavras soltas
# combinam por OU e o ranking (bm25) ordena; o que está "entre aspas" é obrigatório; `grupos` exige um termo de cada.
# Operador explícito em MAIÚSCULAS (E, OU, NÃO, AND, OR, NOT) ou parênteses liga o modo avançado, que obedece à letra.

_SEM_VARIANTE = {"pais", "leis", "seis", "dois", "reis", "jamais", "demais", "quais", "tais", "onus", "lapis", "tres", "caos",
                 "simples", "pires", "atras", "alias", "apenas", "antes", "depois", "menos", "mais", "entao", "senao", "orgao",
                 "virus", "bonus", "campus", "status", "habeas", "corpus", "versus", "reus", "deus", "juros", "custas", "ferias",
                 "anais", "oculos", "honorarios", "lucros"}
# Gramática, não assunto. "nao", "sem" e "menor" NÃO entram: mudam o sentido jurídico (lição do harness do TJSE).
_VAZIAS = set("""a o as os um uma uns umas de do da dos das em no na nos nas por para pelo pela pelos pelas com
sob sobre entre ate apos ante e ou mas que se qual quais quando onde como porque pois ja sim ha ser sao foi
era eram tem tinha teve havia deve devem pode podem existe existem qualquer algum alguma
seguinte seguintes mesmo mesma outro outra seu sua seus suas este esta isso aquilo lhe lhes ao aos""".split())


def variantes_numero(w: str) -> list[str]:
    """Singular/plural de uma palavra já sem acento e minúscula. FTS5 não tem stemmer de português e prefixo não
    resolve plural irregular (acao→acoes, moral→morais). Variante inexistente é inofensiva: só não casa."""
    v = [w]
    if len(w) < 4 or not w.isalpha() or w in _SEM_VARIANTE:
        return v
    for suf, trocas in (("oes", ["ao"]), ("aes", ["ao"]), ("aos", ["ao"]), ("ais", ["al"]), ("eis", ["el"]), ("ois", ["ol"]),
                        ("ns", ["m"]), ("res", ["r"]), ("zes", ["z"]), ("ses", ["s"]), ("ao", ["oes", "aos", "aes"]),
                        ("al", ["ais"]), ("el", ["eis"]), ("ol", ["ois"]), ("il", ["is"]), ("m", ["ns"]), ("r", ["res"]),
                        ("z", ["zes"])):
        if w.endswith(suf):
            v += [w[: -len(suf)] + t for t in trocas]
            break
    else:
        v.append(w[:-1] if w.endswith("s") else w + "s")
    return v[:4]


def _palavras(t: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", _fold(t), flags=re.U).split()


def _frase_fts(termo: str, exato: bool = False) -> str:
    termo = termo.strip()
    radical = re.search(r"[^\W\d_]{3,}[$*]$", termo) is not None  # "R$" não é radical
    palavras = _palavras(termo)
    if not palavras:
        return ""
    if radical:
        return '"' + " ".join(palavras) + '"*'
    if exato:
        return '"' + " ".join(palavras) + '"'
    combos: list[list[str]] = [[]]
    for w in palavras:
        vs = variantes_numero(w)
        if len(combos) * len(vs) > 36:
            vs = vs[:1]
        combos = [c + [x] for c in combos for x in vs]
    fr = ['"' + " ".join(c) + '"' for c in combos]
    return fr[0] if len(fr) == 1 else "(" + " OR ".join(fr) + ")"


def _tem_operador(consulta: str) -> bool:
    return bool(re.search(r"(?:^|\s)(?:E|OU|N[ÃA]O|AND|OR|NOT)(?:\s|$)|[()]", consulta or ""))


def _montar_match(consulta: str, grupos: list[list[str]] | None = None, exato: bool = False) -> tuple[str, list[tuple[str, str]]]:
    """(expressão FTS5, partes obrigatórias como (rótulo, expressão)) — as partes servem para dizer QUAL zera a busca."""
    partes: list[tuple[str, str]] = []
    consulta = consulta or ""
    if _tem_operador(consulta):
        expr = _traduzir_consulta(consulta)
        if expr:
            partes.append((f"consulta avançada «{_uma_linha(consulta)}»", expr))
    else:
        frases = re.findall(r'"([^"]+)"', consulta)
        for fr in frases:
            e = _frase_fts(fr, exato=True)
            if e:
                partes.append((f'"{fr}"', e))
        resto = re.sub(r'"[^"]*"', " ", consulta)
        soltas = []
        for tok in resto.split():
            if re.search(r"[^\W\d_]{3,}[$*]$", tok):
                soltas.append(_frase_fts(tok))
                continue
            for w in _palavras(tok):
                if w not in _VAZIAS and len(w) >= 2:
                    soltas.append(_frase_fts(w, exato=exato))
        soltas = [x for x in dict.fromkeys(soltas) if x]
        if soltas:
            partes.append(("palavras soltas (ao menos uma)", soltas[0] if len(soltas) == 1 else "(" + " OR ".join(soltas) + ")"))
    for g in grupos or []:
        termos = [_frase_fts(t, exato=exato) for t in g if t and t.strip()]
        termos = [t for t in termos if t]
        if termos:
            partes.append((f"grupo {[t for t in g if t]}", termos[0] if len(termos) == 1 else "(" + " OR ".join(termos) + ")"))
    expr = " AND ".join(p[1] if len(partes) == 1 else f"({p[1]})" for p in partes)
    return expr, partes


# ───────────────────────── base local ─────────────────────────

class BaseAusente(RuntimeError):
    pass


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS ementas(
  guid TEXT PRIMARY KEY, slug TEXT, numero TEXT, numero_dig TEXT, titulo TEXT, ano_lista INTEGER,
  texto TEXT, ementa TEXT, inteiro TEXT, fecho TEXT, processo TEXT, processo_dig TEXT, votacao TEXT,
  data_julgamento TEXT, relator TEXT, revisor TEXT, presidente TEXT, baixado_em TEXT);
CREATE INDEX IF NOT EXISTS ix_ementas_num ON ementas(numero_dig);
CREATE INDEX IF NOT EXISTS ix_ementas_proc ON ementas(processo_dig);
CREATE VIRTUAL TABLE IF NOT EXISTS ementas_fts USING fts5(guid UNINDEXED, titulo, ementa, inteiro, tokenize="unicode61 remove_diacritics 2");
CREATE TABLE IF NOT EXISTS disciplinares(
  id INTEGER PRIMARY KEY, data TEXT, ementa TEXT, texto TEXT, acordao TEXT, processo TEXT, turma TEXT, relator TEXT, baixado_em TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS disciplinares_fts USING fts5(id UNINDEXED, ementa, texto, tokenize="unicode61 remove_diacritics 2");
CREATE TABLE IF NOT EXISTS meta(chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS citacoes(guid TEXT, tipo TEXT, ref TEXT, PRIMARY KEY(guid, tipo, ref));
CREATE INDEX IF NOT EXISTS ix_citacoes_ref ON citacoes(ref);
"""


def _db(caminho: str | None = None, criar: bool = False) -> sqlite3.Connection:
    caminho = caminho or ARQUIVO_DB
    if not criar and not os.path.exists(caminho):
        raise BaseAusente("[PESQUISA NÃO REALIZADA] índice local do TED-OAB/SP ainda não existe — rode `sincronizar_base_ted_sp` "
                          f"(ou, no terminal: {PASTA}/.venv/bin/python {os.path.abspath(__file__)} --sincronizar). Isto NÃO é 'não localizado'.")
    _garantir_pasta(os.path.dirname(caminho))
    novo = not os.path.exists(caminho)
    con = sqlite3.connect(caminho, timeout=60)
    if novo:
        with contextlib.suppress(OSError):
            os.chmod(caminho, 0o600)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.create_function("fold", 1, _fold)  # LIKE nativo só ignora caixa em ASCII; títulos são acentuados e em caixa alta
    con.executescript(_ESQUEMA)
    if "html" not in {r[1] for r in con.execute("PRAGMA table_info(ementas)")}:
        con.execute("ALTER TABLE ementas ADD COLUMN html TEXT")
    if int(_meta_get(con, "parser_versao", "0") or 0) < PARSER_VERSAO:
        _reindexar(con)
    return con


def _reindexar(con) -> int:
    """Refaz fecho, FTS e grafo de TODOS os pareceres a partir do que está guardado (HTML bruto quando há, senão o texto).
    Zero rede. Roda sozinho quando PARSER_VERSAO sobe."""
    n = 0
    for r in con.execute("SELECT guid, html, texto FROM ementas WHERE texto IS NOT NULL").fetchall():
        _gravar_parecer(con, r["guid"], r["html"] if r["html"] else None, None if r["html"] else r["texto"])
        n += 1
    _meta_set(con, "parser_versao", str(PARSER_VERSAO))
    con.commit()
    return n


def _meta_get(con, chave: str, padrao: str = "") -> str:
    r = con.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
    return r[0] if r else padrao


def _meta_set(con, chave: str, valor: str) -> None:
    con.execute("INSERT INTO meta(chave,valor) VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, valor))


def _gravar_item_lista(con, it: dict, ano: int) -> bool:
    """True se o guid é novo."""
    if con.execute("SELECT 1 FROM ementas WHERE guid=?", (it["guid"],)).fetchone():
        return False
    con.execute("INSERT INTO ementas(guid,slug,numero,numero_dig,titulo,ano_lista) VALUES(?,?,?,?,?,?)",
                (it["guid"], it["slug"], it["numero"], _so_digitos(it["numero"]), it["titulo"], ano))
    return True


def _gravar_parecer(con, guid: str, html_modal: str | None, texto: str | None = None) -> None:
    """Grava o parecer a partir do HTML bruto (guardado, para reparse sem rede) ou, em reindexação, do texto já extraído."""
    if html_modal is not None:
        con.execute("UPDATE ementas SET html=? WHERE guid=?", (html_modal, guid))
        texto = _html_para_texto(html_modal)
    texto = texto or ""
    p = _parse_parecer(texto)
    con.execute("UPDATE ementas SET texto=?, ementa=?, inteiro=?, fecho=?, processo=?, processo_dig=?, votacao=?, data_julgamento=?, relator=?, "
                "revisor=?, presidente=?, baixado_em=? WHERE guid=?",
                (texto, p["ementa"], p["inteiro"], p["fecho"], p["processo"], _so_digitos(p["processo"]), p["votacao"], p["data_julgamento"],
                 p["relator"], p["revisor"], p["presidente"], time.strftime("%Y-%m-%d %H:%M:%S"), guid))
    titulo = con.execute("SELECT titulo FROM ementas WHERE guid=?", (guid,)).fetchone()[0]
    con.execute("DELETE FROM ementas_fts WHERE guid=?", (guid,))
    con.execute("INSERT INTO ementas_fts(guid,titulo,ementa,inteiro) VALUES(?,?,?,?)", (guid, titulo, p["ementa"], p["inteiro"]))
    con.execute("DELETE FROM citacoes WHERE guid=?", (guid,))
    con.executemany("INSERT OR IGNORE INTO citacoes(guid, tipo, ref) VALUES(?,?,?)",
                    [(guid, tipo, ref) for tipo, ref in _extrair_citacoes(texto, p["processo"])])


def _gravar_disc_lista(con, it: dict) -> bool:
    if con.execute("SELECT 1 FROM disciplinares WHERE id=?", (it["id"],)).fetchone():
        return False
    con.execute("INSERT INTO disciplinares(id,data,ementa) VALUES(?,?,?)", (it["id"], it["data"], it["ementa"]))
    return True


def _gravar_disc_texto(con, id_: int, pagina: str) -> None:
    p = _parse_texto_disc(pagina)
    con.execute("UPDATE disciplinares SET texto=?, acordao=?, processo=?, turma=?, relator=?, baixado_em=? WHERE id=?",
                (p["texto"], p["acordao"], p["processo"], p["turma"], p["relator"], time.strftime("%Y-%m-%d %H:%M:%S"), id_))
    em = con.execute("SELECT ementa FROM disciplinares WHERE id=?", (id_,)).fetchone()[0]
    con.execute("DELETE FROM disciplinares_fts WHERE id=?", (id_,))
    con.execute("INSERT INTO disciplinares_fts(id,ementa,texto) VALUES(?,?,?)", (id_, em, p["texto"]))


# ───────────────────────── disjuntor + rede ─────────────────────────

class PortalRecusou(RuntimeError):
    pass


_PAUSA_MEMORIA: dict[str, float] = {}  # vale mesmo se o disco falhar


@contextlib.contextmanager
def _trava_estado():
    """Trava entre processos (flock). Sem trava, `_reservar` recusa requisitar — dois processos sem trava perdem registro."""
    f = None
    try:
        _garantir_pasta(PASTA_DADOS)
        f = open(_ARQUIVO_ESTADO + ".lock", "a+")
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
    except OSError:
        f = None
    try:
        yield f is not None
    finally:
        if f is not None:
            with contextlib.suppress(Exception):
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_UN)
                f.close()


def _host_vazio() -> dict:
    return {"requisicoes": [], "pausa_ate": 0.0, "motivo": "", "incidentes": [], "nivel": 0, "sucessos": 0, "bloqueios_seguidos": 0}


def _sanear_host(e: Any, host: str, agora: float) -> dict:
    """Tipos saneados: JSON válido com tipo errado também é ilegível (levanta, e quem chama trata como fail-closed)."""
    e = e if isinstance(e, dict) else {}
    return {"requisicoes": [float(t) for t in (e.get("requisicoes") or []) if 0 <= agora - float(t) < 86400],
            "pausa_ate": float(e.get("pausa_ate") or e.get("cooldown_ate") or 0),  # cooldown_ate: nome da v1.3
            "motivo": str(e.get("motivo") or ""),
            "incidentes": [i for i in (e.get("incidentes") or []) if isinstance(i, dict)][-20:],
            "nivel": min(max(int(e.get("nivel") or 0), 0), len(ESCADA[host]) - 1),
            "sucessos": max(int(e.get("sucessos") or 0), 0),
            "bloqueios_seguidos": max(int(e.get("bloqueios_seguidos") or 0), 0)}


def _ler_estado() -> dict:
    """{'oabsp': {...}, 'cfoab': {...}}. Estado ILEGÍVEL não libera requisição (fail-closed): pausa de 1 h em ritmo mínimo."""
    agora = time.time()
    if not os.path.exists(_ARQUIVO_ESTADO):
        return {h: _host_vazio() for h in ESCADA}
    try:
        with open(_ARQUIVO_ESTADO, encoding="utf-8") as f:
            bruto = json.load(f)
        if not isinstance(bruto, dict):
            raise ValueError("estado não é objeto")
        antigo = {k: v for k, v in bruto.items() if k not in ("hosts", *ESCADA)}  # formato da v1.3: oabsp na raiz
        return {h: _sanear_host(bruto.get(h) or (bruto.get("hosts") or {}).get(h) or (antigo if h == "oabsp" else {}), h, agora)
                for h in ESCADA}
    except Exception:
        return {h: {**_host_vazio(), "pausa_ate": agora + PAUSA_ILEGIVEL_S, "nivel": len(ESCADA[h]) - 1,
                    "motivo": "estado do disjuntor ilegível (fail-closed)"} for h in ESCADA}


def _gravar_estado(e: dict) -> None:
    _garantir_pasta(PASTA_DADOS)
    tmp = f"{_ARQUIVO_ESTADO}.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False)
    os.replace(tmp, _ARQUIVO_ESTADO)


def _nome_portal(host: str) -> str:
    return "do Conselho Federal da OAB" if host == "cfoab" else "da OAB/SP"


def _reservar(agora: float | None = None, host: str = "oabsp") -> float:
    """Reserva uma requisição e devolve quanto esperar. Levanta PortalRecusou em pausa, teto ou sem trava (fail-closed)."""
    agora = agora if agora is not None else time.time()
    with _trava_estado() as travado:
        if not travado and fcntl is not None:
            raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] não consegui a trava do disjuntor ({_ARQUIVO_ESTADO}.lock); "
                                "sem trava não há requisição (fail-closed). Isto NÃO é 'não localizado'.")
        tudo = _ler_estado()
        e = tudo[host]
        ate = max(e["pausa_ate"], _PAUSA_MEMORIA.get(host, 0.0))
        if agora < ate:
            raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] portal {_nome_portal(host)} em pausa por mais "
                                f"~{int((ate - agora) // 60) + 1} min ({e['motivo'] or 'pausa em memória'}). Não contornar por "
                                "navegador, proxy ou outro cliente. Isto NÃO é 'não localizado'.")
        espacamento, janela_max = ESCADA[host][e["nivel"]]
        reqs = e["requisicoes"]
        if not _MODO_CARGA:
            if len(reqs) >= DIA_MAX[host]:
                raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] teto diário de {DIA_MAX[host]} requisições ao portal "
                                    f"{_nome_portal(host)} atingido. Isto NÃO é 'não localizado'.")
            if len([t for t in reqs if agora - t < JANELA_S]) >= janela_max:
                raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] teto de {janela_max} requisições em {JANELA_S // 60} min ao portal "
                                    f"{_nome_portal(host)}" + (f" (ritmo apertado, degrau {e['nivel']} da escada, por recusa "
                                                               "anterior)" if e["nivel"] else "")
                                    + "; tente de novo em alguns minutos. Isto NÃO é 'não localizado'.")
        espera = max(0.0, (max(reqs) + espacamento - agora) if reqs else 0.0)
        e["requisicoes"] = reqs + [agora + espera]
        try:
            _gravar_estado(tudo)
        except OSError as ex:
            raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] não consegui registrar a requisição no disjuntor ({type(ex).__name__}); "
                                "sem registro não há requisição (fail-closed).")
        return espera


def _registrar_bloqueio(motivo: str, retry_after: float = 0.0, agora: float | None = None, host: str = "oabsp") -> float:
    """Recusa do portal: pausa que dobra a cada recusa seguida (2 h → 24 h na OAB/SP) e o ritmo sobe um degrau na escada."""
    agora = agora if agora is not None else time.time()
    passo = _COOLDOWN_INICIAL_S[host]
    with _trava_estado(), contextlib.suppress(OSError):
        tudo = _ler_estado()
        e = tudo[host]
        passo = min(_COOLDOWN_MAX_S, _COOLDOWN_INICIAL_S[host] * (2 ** e["bloqueios_seguidos"]))
        passo = max(passo, retry_after)
        e["bloqueios_seguidos"] += 1
        e["pausa_ate"] = max(e["pausa_ate"], agora + passo)  # pausa só cresce
        e["motivo"] = motivo[:200]
        e["nivel"] = min(e["nivel"] + 1, len(ESCADA[host]) - 1)
        e["sucessos"] = 0
        e["incidentes"] = (e["incidentes"] + [{"quando": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(agora)),
                                               "motivo": motivo[:200]}])[-20:]
        _gravar_estado(tudo)
    _PAUSA_MEMORIA[host] = max(_PAUSA_MEMORIA.get(host, 0.0), agora + passo)
    return passo


def _registrar_sucesso(host: str = "oabsp") -> None:
    """Consulta limpa: zera a sequência de recusas e, depois de SUCESSOS_PARA_RELAXAR seguidas, desce um degrau."""
    with _trava_estado(), contextlib.suppress(OSError):
        tudo = _ler_estado()
        e = tudo[host]
        if not e["bloqueios_seguidos"] and not e["nivel"]:
            return
        e["bloqueios_seguidos"] = 0
        if e["nivel"]:
            e["sucessos"] += 1
            if e["sucessos"] >= SUCESSOS_PARA_RELAXAR:
                e["nivel"] -= 1
                e["sucessos"] = 0
        _gravar_estado(tudo)


async def _requisitar(url: str, operacao: str, dados: dict | None = None, params: dict | None = None) -> str:
    """Uma requisição, no ritmo do limitador. Recusa do portal arma o disjuntor e NÃO é repetida."""
    if httpx is None:
        raise PortalRecusou("[PESQUISA NÃO REALIZADA] httpx não instalado na venv do servidor")
    if not (url.startswith(SITE + "/_ajax/") or url.startswith(SITE2 + "/asp/tribunal_etica/") or url.startswith(CFOAB_API)):
        raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] URL fora do portal permitido recusada: {url[:120]}")
    host = "cfoab" if url.startswith(CFOAB) else "oabsp"
    nome_portal = _nome_portal(host)
    espera = _reservar(host=host)
    if espera:
        await asyncio.sleep(espera)
    cab = dict(HEADERS)
    if host == "cfoab":
        cab.update({"Accept": "application/json", "Referer": CFOAB + "/"})
    elif url.startswith(SITE + "/"):
        cab.update({"X-Requested-With": "XMLHttpRequest", "Referer": PAGINA_TED, "Origin": SITE})
    else:
        cab["Referer"] = URL_DISC
    try:
        async with httpx.AsyncClient(timeout=90.0, headers=cab, follow_redirects=True) as cli:
            r = await (cli.post(url, data=dados) if dados is not None else cli.get(url, params=params))
            if r.status_code == 503 and not (r.headers.get("retry-after") or "").strip():
                # Medido em 19/09/2026: o CMS da OAB/SP solta 503 avulso (~1 a cada 35–270 requisições) e o MESMO item baixa normalmente
                # depois — indisponibilidade passageira, não recusa. UMA nova tentativa após pausa longa; se repetir, aí sim arma o disjuntor.
                # 403/429 e 503 com Retry-After continuam sendo recusa na primeira ocorrência, sem nova tentativa.
                await asyncio.sleep(_PAUSA_503_S)
                r = await (cli.post(url, data=dados) if dados is not None else cli.get(url, params=params))
    except Exception as ex:  # timeout e queda de conexão NÃO armam o disjuntor: não são recusa do portal
        raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] falha de rede em {operacao}: {type(ex).__name__}: {ex}")
    corpo = r.text
    motivo = ""
    if r.status_code in (403, 429, 503):
        motivo = f"HTTP {r.status_code}"
    elif "challenge" in (r.headers.get("cf-mitigated") or ""):
        motivo = "desafio Cloudflare"
    elif r.status_code == 200 and len(corpo) < 3000 and _RE_BLOQUEIO.search(corpo) and not corpo.lstrip().startswith("{"):
        motivo = "página de bloqueio"
    if motivo:
        ra = r.headers.get("retry-after") or ""
        passo = _registrar_bloqueio(f"{operacao}: {motivo}", float(ra) if ra.isdigit() else 0.0, host=host)
        raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] o portal {nome_portal} recusou ({motivo}) em {operacao}; pausa de "
                            f"{int(passo // 60)} min e ritmo apertado. Isto NÃO é 'não localizado'.")
    if r.status_code != 200:
        detalhe = ""
        if host == "cfoab":
            with contextlib.suppress(Exception):
                detalhe = " (" + str(json.loads(corpo).get("message") or "")[:160] + ")"
        raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] HTTP {r.status_code} em {operacao}{detalhe} — isto NÃO é 'não localizado'.")
    _registrar_sucesso(host)
    return corpo


async def _json_ajax(url: str, operacao: str, dados: dict) -> dict:
    corpo = await _requisitar(url, operacao, dados)
    try:
        j = json.loads(corpo)
        if not isinstance(j, dict):
            raise ValueError("não é objeto")
        return j
    except Exception:
        raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] resposta inesperada (não-JSON) em {operacao} — o portal pode ter mudado. "
                            "Isto NÃO é 'não localizado'.")


async def _baixar_modal(slug: str, guid: str) -> str:
    j = await _json_ajax(URL_MODAL, f"inteiro teor {guid}", {"bracket": "5", "slug": slug, "guid": guid})
    h = j.get("html") or ""
    if not h.strip():
        raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] o portal devolveu modal vazio para {guid} — isto NÃO é 'não localizado'.")
    return h


# ───────────────────────── sincronização ─────────────────────────

async def _sincronizar(anos: list[int] | None = None, max_requisicoes: int = 0, incluir_disciplinar: bool = True,
                       progresso=None) -> dict:
    """Incremental e retomável. Ordem: listas dos anos (do mais novo ao mais antigo) → pareceres pendentes →
    lista disciplinar → acórdãos disciplinares pendentes. `max_requisicoes` (0 = sem teto) limita a chamada."""
    diga = progresso or (lambda *_: None)
    con = _db(criar=True)
    feitas = 0
    rel = {"listas": 0, "novas": 0, "pareceres": 0, "disc_novas": 0, "disc_textos": 0, "parou_por_orcamento": False, "avisos": []}

    def _cabe() -> bool:
        return not max_requisicoes or feitas < max_requisicoes

    try:
        ano_atual = time.localtime().tm_year
        for ano in (sorted(set(anos), reverse=True) if anos else range(ano_atual, ANO_INICIAL - 1, -1)):
            if not _cabe():
                break
            completo = _meta_get(con, f"ano_completo_{ano}") == "1"
            if completo and ano < ano_atual - 1:
                continue  # ano antigo já varrido até o fim: o ementário dele não muda
            start = 0
            while _cabe():
                dados = {"card": CARD, "ano": str(ano), "action": "ano"} if start == 0 else \
                        {"card": CARD, "ano": str(ano), "keyword": "", "start": str(start), "action": "load-more"}
                j = await _json_ajax(URL_LISTA, f"lista {ano} (a partir de {start})", dados)
                feitas += 1
                rel["listas"] += 1
                itens = _parse_lista(j.get("msg") or "")
                novos = sum(_gravar_item_lista(con, it, ano) for it in itens)
                rel["novas"] += novos
                con.commit()
                diga(f"  {ano} +{start}: {len(itens)} itens, {novos} novos")
                fim = str(j.get("btnMore")) != "1" or not itens
                if fim:
                    _meta_set(con, f"ano_completo_{ano}", "1")
                    con.commit()
                    break
                if completo and novos == 0:
                    break  # ano recente já varrido antes e esta página não trouxe nada novo: o resto é conhecido
                start += POR_PAGINA_SITE
        pend = con.execute("SELECT guid, slug, numero FROM ementas WHERE texto IS NULL ORDER BY CAST(guid AS INTEGER) DESC").fetchall()
        for r in pend:
            if not _cabe():
                break
            h = await _baixar_modal(r["slug"], r["guid"])
            feitas += 1
            _gravar_parecer(con, r["guid"], h)
            rel["pareceres"] += 1
            if rel["pareceres"] % 10 == 0:
                con.commit()
                diga(f"  pareceres: {rel['pareceres']}/{len(pend)} (último: {r['numero']})")
        con.commit()
        if incluir_disciplinar and _cabe():
            if _meta_get(con, "disc_lista_completa") != "1":
                pagina = await _requisitar(URL_DISC, "lista disciplinar (todos)", {"proc": "1", "anoreq": "0", "texto1": "", "texto2": ""})
                feitas += 1
                itens = _parse_lista_disc(pagina)
                rel["disc_novas"] = sum(_gravar_disc_lista(con, it) for it in itens)
                if itens:
                    _meta_set(con, "disc_lista_completa", "1")
                else:
                    rel["avisos"].append("lista disciplinar voltou sem itens — o formulário antigo pode ter mudado")
                con.commit()
                diga(f"  disciplinar: {len(itens)} ementas na lista, {rel['disc_novas']} novas")
            pend = con.execute("SELECT id FROM disciplinares WHERE texto IS NULL ORDER BY id DESC").fetchall()
            for r in pend:
                if not _cabe():
                    break
                pagina = await _requisitar(URL_DISC_TEXTO + str(r["id"]), f"acórdão disciplinar {r['id']}")
                feitas += 1
                _gravar_disc_texto(con, r["id"], pagina)
                rel["disc_textos"] += 1
                if rel["disc_textos"] % 10 == 0:
                    con.commit()
                    diga(f"  acórdãos disciplinares: {rel['disc_textos']}/{len(pend)}")
        rel["parou_por_orcamento"] = not _cabe()
        _meta_set(con, "ultima_sincronizacao", time.strftime("%Y-%m-%d %H:%M:%S"))
        con.commit()
    finally:
        con.commit()
        con.close()
    rel["requisicoes"] = feitas
    return rel


async def _sincronizar_tema(palavra: str, ano: int | None = None, max_requisicoes: int = 150, progresso=None) -> dict:
    """Busca dirigida: usa a busca por palavra do PORTAL (que só casa o TÍTULO da ementa), cataloga o que voltar e baixa
    só esses pareceres. Serve para pesquisar um tema sem a carga completa; o que não tiver o termo no título fica de fora."""
    diga = progresso or (lambda *_: None)
    palavra = _uma_linha(palavra)
    if len(palavra) < 3:
        raise ValueError("palavra de busca curta demais")
    con = _db(criar=True)
    rel = {"palavra": palavra, "encontradas": 0, "novas": 0, "pareceres": 0, "guids": [], "requisicoes": 0, "parou_por_orcamento": False}
    try:
        start = 0
        while rel["requisicoes"] < max_requisicoes:
            dados = {"card": CARD, "ano": str(ano or 9999), "keyword": palavra, "action": "search-keyword"} if start == 0 else \
                    {"card": CARD, "ano": str(ano or 9999), "keyword": palavra, "start": str(start), "action": "load-more"}
            j = await _json_ajax(URL_LISTA, f"busca no portal por '{palavra}' (a partir de {start})", dados)
            rel["requisicoes"] += 1
            itens = _parse_lista(j.get("msg") or "")
            for it in itens:
                if it["guid"] not in rel["guids"]:
                    rel["guids"].append(it["guid"])
                    rel["novas"] += _gravar_item_lista(con, it, 0)
            con.commit()
            diga(f"  '{palavra}' +{start}: {len(itens)} itens")
            if str(j.get("btnMore")) != "1" or not itens:
                break
            start += POR_PAGINA_SITE
        rel["encontradas"] = len(rel["guids"])
        _meta_set(con, "tema:" + _fold(palavra) + (f":{ano}" if ano else ""), time.strftime("%Y-%m-%d %H:%M:%S") + f" · {rel['encontradas']} ementas (download em andamento ou interrompido)")
        con.commit()
        for g in rel["guids"]:
            r = con.execute("SELECT slug, texto FROM ementas WHERE guid=?", (g,)).fetchone()
            if r["texto"]:
                continue
            if rel["requisicoes"] >= max_requisicoes:
                rel["parou_por_orcamento"] = True
                break
            _gravar_parecer(con, g, await _baixar_modal(r["slug"], g))
            rel["requisicoes"] += 1
            rel["pareceres"] += 1
            con.commit()
        _meta_set(con, "tema:" + _fold(palavra) + (f":{ano}" if ano else ""), time.strftime("%Y-%m-%d %H:%M:%S") + f" · {rel['encontradas']} ementas")
        con.commit()
    finally:
        con.commit()
        con.close()
    return rel


# ───────────────────────── pacote pronto (importar sem tocar o portal) ─────────────────────────
# Portado do TJSE. Montar o ementário inteiro custa ~6 mil requisições a um CMS que solta 503: quem monta é o autor,
# UMA vez, e publica no release; os demais importam em segundos, sem requisição nenhuma à OAB/SP. O pacote leva só o
# ementário DEONTOLÓGICO (consultas em tese) — NUNCA a tabela `meta` (histórico de temas pesquisados), recibos ou a base
# disciplinar (acórdãos de processo sigiloso, que a ferramenta lê do portal quando pedido).
PACOTE_NOME = "base-tedsp.jsonl.gz"
PACOTE_URL = f"https://github.com/{REPO_GITHUB}/releases/latest/download/{PACOTE_NOME}"
PACOTE_SUMS_URL = f"https://github.com/{REPO_GITHUB}/releases/latest/download/SHA256SUMS.txt"
PACOTE_MAX_BYTES = 400 * 1024 * 1024
_CAMPOS_PACOTE = ("guid", "slug", "numero", "titulo", "ano_lista", "html", "texto")


def _exportar_pacote(destino: str) -> dict:
    """Grava `<destino>/base-tedsp.jsonl.gz` e `SHA256SUMS.txt`. Só dado público do ementário."""
    con = _db()
    try:
        os.makedirs(destino, exist_ok=True)
        caminho = os.path.join(destino, PACOTE_NOME)
        n_txt = n_tit = 0
        with gzip.open(caminho, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(json.dumps({"_pacote": "mcp-oab-jurisprudencia", "versao_servidor": VERSAO, "parser_versao": PARSER_VERSAO,
                                "gerado_em": time.strftime("%Y-%m-%d"), "fonte": PAGINA_TED}, ensure_ascii=False) + "\n")
            for r in con.execute(f"SELECT {', '.join(_CAMPOS_PACOTE)} FROM ementas ORDER BY CAST(guid AS INTEGER)"):
                f.write(json.dumps({k: r[k] for k in _CAMPOS_PACOTE}, ensure_ascii=False) + "\n")
                n_txt += 1 if r["texto"] else 0
                n_tit += 0 if r["texto"] else 1
        h = hashlib.sha256(open(caminho, "rb").read()).hexdigest()
        with open(os.path.join(destino, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
            f.write(f"{h}  {PACOTE_NOME}\n")
        return {"arquivo": caminho, "sha256": h, "com_texto": n_txt, "so_titulo": n_tit, "bytes": os.path.getsize(caminho)}
    finally:
        con.close()


def _importar_linhas(linhas) -> dict:
    """Importa linhas do pacote. Nunca rebaixa: parecer que já tem texto aqui não é sobrescrito."""
    con = _db(criar=True)
    novos = textos = 0
    try:
        for linha in linhas:
            if not linha.strip():
                continue
            r = json.loads(linha)
            if "_pacote" in r:
                continue
            guid = re.sub(r"\D", "", str(r.get("guid") or ""))
            if not guid:
                continue
            existe = con.execute("SELECT texto FROM ementas WHERE guid=?", (guid,)).fetchone()
            if not existe:
                con.execute("INSERT INTO ementas(guid,slug,numero,numero_dig,titulo,ano_lista) VALUES(?,?,?,?,?,?)",
                            (guid, str(r.get("slug") or ""), str(r.get("numero") or ""), _so_digitos(str(r.get("numero") or "")),
                             str(r.get("titulo") or ""), int(r.get("ano_lista") or 0)))
                novos += 1
            if r.get("texto") and not (existe and existe["texto"]):
                _gravar_parecer(con, guid, r.get("html") or None, None if r.get("html") else r["texto"])
                textos += 1
        _meta_set(con, "pacote_importado_em", time.strftime("%Y-%m-%d %H:%M:%S"))
        con.commit()
        return {"novos": novos, "textos": textos}
    finally:
        con.close()


async def _importar_pacote() -> str:
    if httpx is None:
        raise PortalRecusou("[PESQUISA NÃO REALIZADA] httpx não instalado na venv do servidor")
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True, headers={"User-Agent": HEADERS["User-Agent"]}) as cli:
        sums = await cli.get(PACOTE_SUMS_URL)
        if sums.status_code == 404:
            return ("Este release ainda NÃO traz o pacote pronto do ementário. Enquanto isso, a busca dirigida funciona e pesa "
                    "pouco no portal: `pesquisar_tema_no_portal_ted_sp` com um termo do tema (ex.: 'publicidade', 'honorários'), e "
                    "depois `buscar_ementas_ted_sp`. O Conselho Federal não precisa de pacote: é consultado ao vivo.")
        if sums.status_code != 200:
            raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] GitHub respondeu HTTP {sums.status_code} ao buscar a lista de hashes do pacote.")
        esperado = next((ln.split()[0] for ln in sums.text.splitlines() if ln.strip().endswith(PACOTE_NOME)), "")
        if not re.fullmatch(r"[0-9a-f]{64}", esperado):
            raise PortalRecusou("[PESQUISA NÃO REALIZADA] SHA256SUMS.txt do release não traz hash válido do pacote — importação recusada.")
        r = await cli.get(PACOTE_URL)
        if r.status_code != 200:
            raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] GitHub respondeu HTTP {r.status_code} ao baixar o pacote.")
        if len(r.content) > PACOTE_MAX_BYTES:
            raise PortalRecusou("[PESQUISA NÃO REALIZADA] pacote maior que o limite de segurança — importação recusada.")
        if hashlib.sha256(r.content).hexdigest() != esperado:
            raise PortalRecusou("[PESQUISA NÃO REALIZADA] o hash do pacote NÃO confere com SHA256SUMS.txt (download corrompido ou "
                                "adulterado) — nada foi importado.")
    rel = _importar_linhas(gzip.decompress(r.content).decode("utf-8").splitlines())
    return (f"Pacote importado (hash conferido): {rel['novos']} ementa(s) novas no catálogo, {rel['textos']} parecer(es) com texto "
            "integral. Nenhuma requisição foi feita ao portal da OAB/SP.\n" + _diagnostico())


# ───────────────────────── apresentação ─────────────────────────

def _data_br(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if iso and len(iso) >= 10 else ""


def _link(d) -> str:
    """Link estável do próprio portal (#modal=<slug>[!]5). Montado de slug real vindo da lista — nunca fabricado."""
    return f"{PAGINA_TED}#modal={d['slug']}%5b%21%5d5" if d["slug"] else ""


def _citacao(d) -> str:
    partes = ["TED-OAB/SP, 1ª Turma de Ética Profissional"]
    partes.append(f"Proc. {d['processo']}" if d["processo"] else f"ementa {d['numero']}")
    if d["relator"] and "vencid" not in _fold(d["fecho"] or ""):
        partes.append(f"Rel. {d['relator']}")
    elif d["relator"]:  # há voto vencido: quem assina o parecer vencedor se lê no fecho literal, não se deduz por regex
        partes.append("relatoria: ver fecho (há voto vencido)")
    if d["votacao"]:
        partes.append(d["votacao"])
    if d["data_julgamento"]:
        partes.append(f"j. {_data_br(d['data_julgamento'])}")
    return ", ".join(partes)


_RE_AUTUACAO = re.compile(r"(?i)\b(?:autuad[ao]|consulta\s+(?:formulada|protocolada|autuada)|protocolad[ao])\b[^.\n]{0,60}?(\d{1,2}/\d{1,2}/\d{4})")


def _alertas_data(d) -> list[str]:
    """Inconsistências de data DENTRO da fonte — o servidor não as resolve, só as expõe."""
    av = []
    fecho_iso = d["data_julgamento"] or ""
    m = _RE_AUTUACAO.search(d["texto"] or "")
    if m and fecho_iso:
        dd, mm, aa = m.group(1).split("/")
        aut = f"{int(aa):04d}-{int(mm):02d}-{int(dd):02d}"
        if aut > fecho_iso:
            av.append(f"⚠️ data inconsistente NA PRÓPRIA FONTE: o texto diz que a consulta foi autuada/protocolada em {_data_br(aut)}, "
                      f"posterior ao julgamento do fecho ({_data_br(fecho_iso)}). Cite pela data do fecho e não use a outra para contar prazo.")
    ano_l = d["ano_lista"] if "ano_lista" in d.keys() else 0
    if ano_l and fecho_iso and str(ano_l) != fecho_iso[:4]:
        av.append(f"⚠️ o portal lista esta ementa no ementário de {ano_l}, mas o fecho registra julgamento em {fecho_iso[:4]} "
                  "(a publicação no ementário é posterior ao julgamento) — a data de julgamento é a do fecho.")
    return av


def _cobertura(con) -> str:
    tot, com = con.execute("SELECT COUNT(*), COUNT(texto) FROM ementas").fetchone()
    dt, dc = con.execute("SELECT COUNT(*), COUNT(texto) FROM disciplinares").fetchone()
    anos = [r[0] for r in con.execute("SELECT DISTINCT CAST(COALESCE(NULLIF(substr(data_julgamento,1,4),''), NULLIF(ano_lista,0)) AS INTEGER) "
                                      "FROM ementas WHERE texto IS NOT NULL ORDER BY 1") if r[0]]
    faixa = f"{anos[0]}–{anos[-1]}" if anos else "nenhum ano"
    parcial = "" if tot and com == tot and _meta_get(con, f"ano_completo_{ANO_INICIAL}") == "1" else \
        " ⚠️ BASE PARCIAL: ausência de resultado aqui NÃO é 'não localizado' — sincronize antes de concluir."
    temas = [f"'{r[0][5:]}' ({r[1]})" for r in con.execute("SELECT chave, valor FROM meta WHERE chave LIKE 'tema:%' ORDER BY chave")]
    if temas:
        parcial += "\n[Temas já baixados por busca dirigida no portal (casam o termo no TÍTULO da ementa): " + "; ".join(temas) + "]"
    return (f"[Base local TED-OAB/SP v{VERSAO} · deontológica: {com}/{tot} pareceres indexados ({faixa}) · disciplinar: {dc}/{dt} · "
            f"última sincronização: {_meta_get(con, 'ultima_sincronizacao', 'nunca')}]{parcial}")


def _trecho(texto: str, termos: list[str], limite: int = 420) -> str:
    plano = _uma_linha(texto)
    f = _fold(plano)
    pos = min([p for p in (f.find(_fold(t)) for t in termos if t) if p >= 0] or [0])
    ini = max(0, pos - limite // 3)
    corte = plano[ini:ini + limite]
    return ("…" if ini else "") + corte + ("…" if ini + limite < len(plano) else "")


def _termos(match: str) -> list[str]:
    return [t for t in re.findall(r'"([^"]+)"', match)]


def _localizar(con, numero: str | None, guid: str | None):
    if guid:
        return con.execute("SELECT * FROM ementas WHERE guid=?", (_so_digitos(guid),)).fetchall()
    dig = _so_digitos(numero or "")
    if len(dig) < 3:
        raise ValueError("informe o número da ementa/processo (ex.: '4989-0/2026', 'E-5.123/2019', '25.0886.2025.014989-0') ou o guid")
    linhas = con.execute("SELECT * FROM ementas WHERE numero_dig=? OR processo_dig=?", (dig, dig)).fetchall()
    if not linhas and len(dig) >= 5:
        linhas = con.execute("SELECT * FROM ementas WHERE processo_dig LIKE ? OR numero_dig LIKE ?", (f"%{dig}", f"%{dig}")).fetchall()
    return linhas


def _panorama(con, onde: str, args: list) -> list[str]:
    """Sobre TODAS as ementas que casam (a base é local): período, dispositivos e precedentes mais citados, relatores.
    É amostragem para decidir o que ler, não conclusão sobre o entendimento do TED."""
    guids = [r[0] for r in con.execute(f"SELECT e.guid FROM ementas_fts f JOIN ementas e ON e.guid=f.guid WHERE {onde} LIMIT 3000", args)]
    if len(guids) < 5:
        return []
    marca = ",".join("?" * len(guids))
    datas = con.execute(f"SELECT MIN(data_julgamento), MAX(data_julgamento) FROM ementas WHERE data_julgamento<>'' AND guid IN ({marca})", guids).fetchone()
    top = lambda tipo: con.execute(f"SELECT ref, COUNT(DISTINCT guid) n FROM citacoes WHERE tipo=? AND guid IN ({marca}) "
                                   "GROUP BY ref ORDER BY n DESC, ref LIMIT 8", [tipo, *guids]).fetchall()
    rel = con.execute(f"SELECT relator, COUNT(*) n FROM ementas WHERE relator<>'' AND guid IN ({marca}) "
                      "GROUP BY relator ORDER BY n DESC LIMIT 5", guids).fetchall()
    out = [f"── Panorama das {len(guids)} ementa(s) que casam (não só desta página) ──"]
    if datas and datas[0]:
        out.append(f"Período dos julgamentos: {_data_br(datas[0])} a {_data_br(datas[1])}")
    disp, prec = top("dispositivo"), top("ted")
    if disp:
        out.append("Dispositivos mais citados: " + "; ".join(f"{r['ref']} ({r['n']})" for r in disp))
    if prec:
        out.append("Precedentes do TED mais citados: " + "; ".join(f"{r['ref']} ({r['n']})" for r in prec)
                   + " — use `cita=` para ver quem aplica cada um")
    if rel:
        out.append("Relatores: " + "; ".join(f"{r['relator']} ({r['n']})" for r in rel))
    return out + [""]


def _buscar(consulta: str, ano: int | None, relator: str | None, data_inicio: str | None, data_fim: str | None, campo: str,
            pagina: int, por_pagina: int, grupos: list[list[str]] | None = None, exato: bool = False, cita: str | None = None,
            triagem: bool = False) -> str:
    con = _db()
    try:
        match, partes = _montar_match(consulta, grupos, exato)
        ref_cita = None
        if cita:
            ref_cita = _canon_ref(cita)
            if not ref_cita:
                return (f"Não reconheci «{cita}» como referência. Use um precedente do TED (ex.: 'E-4.607/2016' ou "
                        "'25.0886.2024.010230-7') ou um dispositivo (ex.: 'art. 10 do EAOAB', 'art. 71 do CED'). "
                        "`mapa_de_citacoes_ted_sp` lista as referências que o índice conhece.")
        if not match and not ref_cita:
            return ("Informe a consulta — em português corrente funciona (ex.: advogado que atua em outra seccional sem inscrição) "
                    "— ou `cita` (ex.: 'E-4.607/2016', 'art. 34 do EAOAB').")
        col = {"titulo": "titulo", "ementa": "{titulo ementa}", "tudo": ""}.get(campo, "")
        cond, args = [], []
        if match:
            cond.append("ementas_fts MATCH ?")
            args.append(f"{col} : ({match})" if col else match)
        if ref_cita:
            cond.append("e.guid IN (SELECT guid FROM citacoes WHERE ref=?)")
            args.append(ref_cita)
        if ano:
            cond.append("(substr(e.data_julgamento,1,4)=? OR (e.data_julgamento='' AND e.ano_lista=?))")
            args += [str(ano), ano]
        if relator:
            cond.append("fold(e.relator) LIKE ?")
            args.append(f"%{_fold(relator)}%")
        if data_inicio:
            cond.append("e.data_julgamento>=?")
            args.append(data_inicio)
        if data_fim:
            cond.append("e.data_julgamento<>'' AND e.data_julgamento<=?")
            args.append(data_fim)
        if data_inicio and data_fim and data_inicio > data_fim:
            return f"data_inicio ({data_inicio}) é posterior a data_fim ({data_fim}) — inverta."
        por_pagina = 30 if triagem else max(1, min(25, por_pagina))
        onde = " AND ".join(cond)
        base_from = "ementas_fts f JOIN ementas e ON e.guid=f.guid" if match else "ementas e"
        rank = "bm25(ementas_fts, 0, 8.0, 4.0, 1.0)" if match else "e.data_julgamento"
        ordem = "rk" if match else "rk DESC"  # bm25: menor é melhor; só com `cita`, mais recente primeiro
        try:
            total = con.execute(f"SELECT COUNT(*) FROM {base_from} WHERE {onde}", args).fetchone()[0]
            linhas = con.execute(f"SELECT e.*, {rank} AS rk FROM {base_from} WHERE {onde} ORDER BY {ordem} LIMIT ? OFFSET ?",
                                 args + [por_pagina, (max(1, pagina) - 1) * por_pagina]).fetchall()
        except sqlite3.OperationalError as ex:
            return f"Consulta não entendida pelo índice ({ex}). Simplifique: palavras soltas, \"frase\", `grupos`, radical$."
        filtros = [f"cita {ref_cita}"] if ref_cita else []
        out = [_cobertura(con), AVISO_NATUREZA_DEONT,
               f"Consulta: {match or '—'}{' · ' + ', '.join(filtros) if filtros else ''} · {total} resultado(s) · página {max(1, pagina)}", ""]
        if not total:
            out.append("Nenhuma ementa deontológica do TED-OAB/SP com isso no índice local.")
            if len(partes) > 1:  # diz QUAL parte zera, e o que voltaria sem ela
                for rot, ex in partes:
                    so = con.execute("SELECT COUNT(*) FROM ementas_fts WHERE ementas_fts MATCH ?", [ex]).fetchone()[0]
                    outras = [e2 for r2, e2 in partes if r2 != rot]
                    sem = con.execute("SELECT COUNT(*) FROM ementas_fts WHERE ementas_fts MATCH ?",
                                      [" AND ".join(f"({x})" for x in outras)]).fetchone()[0] if outras else 0
                    out.append(f"  · {rot}: sozinho casa {so}; sem ele, a busca devolveria {sem}")
            out.append("A busca é literal (com singular/plural automático), não semântica: tente sinônimos ou radical$ (ex.: honorar$).")
        termos = _termos(match)
        if triagem and linhas:
            out += ["TRIAGEM — até 30 candidatos em lista curta, para VOCÊ ler, descartar o que não trata do assunto e reordenar. "
                    "Medido no servidor do TJSE: reordenar assim subiu a precisão nos 10 primeiros de 40,5 % para 59,5 %. "
                    "A ementa é só o começo do parecer: leia o inteiro teor do que for usar.", ""]
            for i, d in enumerate(linhas, 1 + (max(1, pagina) - 1) * por_pagina):
                em = _uma_linha(d["ementa"] or d["titulo"] or "")
                out.append(f"{i}. [{d['numero']} · guid {d['guid']} · {_data_br(d['data_julgamento']) or 's/ data'}] "
                           f"{em[:700]}{'…' if len(em) > 700 else ''}")
            return "\n".join(out)
        if total and not triagem and pagina <= 1:
            out += _panorama(con, onde, args) if match else []
        for i, d in enumerate(linhas, 1 + (max(1, pagina) - 1) * por_pagina):
            out += [f"{i}. {_citacao(d)}", f"   Ementa nº {d['numero']} · guid {d['guid']}", f"   {d['titulo']}",
                    f"   Trecho: {_trecho(d['texto'] or '', termos)}", f"   Link: {_link(d)}", ""]
        # A busca roda no FTS, que só tem parecer BAIXADO. Ementa catalogada só por título é invisível lá — e título de
        # ementário não é ementa (o E-4.239/2013 diz "CINCO OU MAIS" no título e "mais de cinco" no corpo).
        if match and not ref_cita:
            obrig = [_palavras(fr) for fr in re.findall(r'"([^"]+)"', consulta or "")]
            soltas = [w for w in _palavras(re.sub(r'"[^"]*"', " ", consulta or "")) if w not in _VAZIAS and len(w) >= 3]
            if obrig or soltas:
                cand = []
                for r in con.execute("SELECT numero, titulo FROM ementas WHERE texto IS NULL"):
                    tf = " " + " ".join(_palavras(r["titulo"] or "")) + " "
                    if all(" " + " ".join(o) + " " in tf for o in obrig):
                        acertos = sum(1 for w in soltas if any(" " + v + " " in tf for v in variantes_numero(w)))
                        if acertos or not soltas:
                            cand.append((acertos, r))
                cand.sort(key=lambda x: -x[0])
                if cand:
                    out += ["", f"──── {len(cand)} ementa(s) com esses termos NO TÍTULO, ainda SEM texto baixado (não entram na busca acima) ────",
                            "Título de ementário NÃO é ementa: é lista de descritores, e já houve caso de o título dizer o oposto do parecer "
                            "(E-4.239/2013: título 'CINCO OU MAIS CAUSAS', corpo 'mais de cinco'). NÃO cite nenhuma destas como entendimento do TED "
                            "sem baixar o texto — use `pesquisar_tema_no_portal_ted_sp` com um termo do tema.", ""]
                    out += [f"· [SÓ TÍTULO] {r['numero']} — {r['titulo'][:180]}" for _, r in cand[:12]]
                    if len(cand) > 12:
                        out.append(f"· … e mais {len(cand) - 12}.")
                    out.append("")
        if total:
            out.append("Antes de citar: `obter_parecer_ted_sp` para ler o parecer inteiro e `verificar_citacao_ted_sp` para o que for entre aspas. "
                       "Relator/data vêm de extração automática do fecho — confira no texto. Todo resultado da lista principal tem TEXTO INTEGRAL indexado.")
        else:
            out.append(onde_mais_procurar())
        return "\n".join(out)
    finally:
        con.close()


def _mapa_citacoes(ref: str | None, tipo: str | None, limite: int) -> str:
    con = _db()
    try:
        n_ar = con.execute("SELECT COUNT(*) FROM citacoes").fetchone()[0]
        cab = [_cobertura(con), f"Grafo de citações: {n_ar} arestas extraídas do texto dos pareceres baixados (só eles entram)."]
        limite = max(1, min(50, limite))
        if ref:
            chave = _canon_ref(ref)
            if not chave:
                return "\n".join(cab + [f"Não reconheci «{ref}» como precedente do TED ou dispositivo."])
            quem = con.execute("""SELECT e.* FROM citacoes c JOIN ementas e USING(guid) WHERE c.ref=?
                                  ORDER BY e.data_julgamento DESC LIMIT ?""", (chave, limite)).fetchall()
            tot = con.execute("SELECT COUNT(DISTINCT guid) FROM citacoes WHERE ref=?", (chave,)).fetchone()[0]
            out = cab + [f"{tot} parecer(es) do índice citam {chave}" + (f" (mostrando {len(quem)})" if tot > len(quem) else "") + ":", ""]
            out += [f"· {_citacao(d)} — {d['titulo'][:140]}" for d in quem]
            alvo = con.execute("SELECT * FROM ementas WHERE processo_dig=? AND texto IS NOT NULL", (_so_digitos(chave),)).fetchone() \
                if chave.startswith(("E-", "2")) else None
            if chave.startswith(("E-", "2")) and not alvo:
                out += ["", f"O próprio {chave} NÃO está no índice com texto: o grafo o enxerga pelas citações, mas para ler é preciso "
                            "baixá-lo (`pesquisar_tema_no_portal_ted_sp` com um termo do título dele)."]
            return "\n".join(out)
        filtro = "WHERE tipo=?" if tipo in ("ted", "dispositivo") else ""
        ps = [tipo] if filtro else []
        top = con.execute(f"SELECT tipo, ref, COUNT(DISTINCT guid) n FROM citacoes {filtro} GROUP BY tipo, ref ORDER BY n DESC, ref LIMIT ?",
                          ps + [limite]).fetchall()
        out = cab + ["Referências mais citadas pelos pareceres do índice:", ""]
        out += [f"· {r['ref']} — {r['n']} parecer(es)" + (" [precedente do TED]" if r["tipo"] == "ted" else "") for r in top]
        return "\n".join(out + ["", "Muitos precedentes citados são ANTERIORES ao que foi baixado: o grafo os enxerga, o índice não tem o texto."])
    finally:
        con.close()


async def _obter(numero: str | None, guid: str | None, reabrir_na_fonte: bool) -> str:
    con = _db()
    try:
        linhas = _localizar(con, numero, guid)
        if not linhas:
            return (f"{_cobertura(con)}\nNenhuma ementa com o número '{numero or guid}' no índice local. Confira o formato "
                    "(nº da ementa no portal, ex. '4989-0/2026', ou nº do processo do fecho).")
        if len(linhas) > 1:
            return "Mais de uma ementa sob esse número — escolha pelo guid:\n" + "\n".join(
                f"- guid {d['guid']}: {d['numero']} — {d['titulo'][:140]}" for d in linhas)
        d = linhas[0]
        origem = "índice local"
        if reabrir_na_fonte or not d["texto"]:
            h = await _baixar_modal(d["slug"], d["guid"])
            _gravar_parecer(con, d["guid"], h)
            con.commit()
            d = con.execute("SELECT * FROM ementas WHERE guid=?", (d["guid"],)).fetchone()
            origem = "reaberto no portal agora"
        recibo = _recibo_ted(d)
        return "\n".join([AVISO_NATUREZA_DEONT] + _alertas_data(d) + [recibo, f"Citação: {_citacao(d)}", f"Ementa nº {d['numero']} · guid {d['guid']} · fonte: {origem} "
                          f"(baixado em {d['baixado_em']})", f"Link: {_link(d)}", f"Fecho (literal): {d['fecho'] or '[não identificado — leia o texto]'}",
                          "", "──────── TEXTO INTEGRAL (ementa + relatório + parecer) ────────", d["texto"] or ""])
    finally:
        con.close()


# ───────────────────────── de quem é a frase ─────────────────────────
# Portado do servidor do TJSE, mas redesenhado sobre a estrutura MEDIDA do parecer do TED (E-5.936/2022, 34 parágrafos):
# o parecer transcreve ementas inteiras de outros pareceres, com aspas ou SEM ELAS, cada uma terminando no próprio fecho
# ("Proc. X - v.u., … Presidente …"). Por isso o critério principal é o FECHO do parágrafo, não o primeiro número que
# aparece nele: o primeiro "Proc." de uma ementa transcrita costuma ser um precedente que ELA cita. Também cita voto de
# outro julgador e doutrina, entre aspas. Um "✅ CONFERE" sem esta leitura atribuiria ao parecer a frase de outro.
_RE_DOUTRINA = re.compile(r"(?i)\b(leciona|preleciona|ensina|doutrina|nas palavras d|coment[áa]rios ao|segundo o autor|"
                          r"na li[çc][ãa]o d|obra|autor[a]? )")
_RE_VOTO_OUTRO = re.compile(r"(?i)\bvoto\s+(?:convergente|divergente|vencido)|declara[çc][ãa]o de voto")
_RE_ROTULO_CITACAO = re.compile(r"(?i)(in verbis|sen[ãa]o vejamos|transcrev|colacionad|confira-se|a seguinte ementa|"
                                r"seguintes? (?:ementas?|decis[õo]es)|precedentes?:|verbis)[^\n]{0,40}$|:\s*$")
_RE_NEGACAO = re.compile(r"(?:^|\s)(?:nao|nunca|jamais|nem|inexiste|descabe|vedad[oa])\s+\S*\s*\S*\s*$")


def _alertas_atribuicao(texto: str, trecho: str, processo_proprio: str, com_fecho: bool = True) -> list[str]:
    """Alertas sobre DE QUEM é o trecho, lidos no parágrafo que o contém. Heurísticos: pegam o padrão comum, não tudo
    (doutrina transcrita sem aspas nem atribuição passa). Dizem 'confira quem fala', não substituem ler o texto."""
    pars = [x for x in (texto or "").split("\n") if x.strip()]
    idx = next((i for i, par in enumerate(pars) if _contem_fragmentos(_normalizar_para_comparar(par), trecho)), None)
    if idx is None:
        return ["ℹ️ Alertas de atribuição não calculados: o trecho atravessa mais de um parágrafo do original."]
    par = pars[idx]
    ant = pars[idx - 1] if idx else ""
    av: list[str] = []
    proprio = _canon_ref(processo_proprio) if processo_proprio else None
    if com_fecho:
        f = _RE_FECHO.search(par)
        if f:
            outro = _canon_ref(f.group("proc"))
            if outro and outro != proprio:
                av.append(f"⚠️ TRANSCRIÇÃO: o trecho está na ementa TRANSCRITA do Proc. {outro} (outro parecer), que o parecer "
                          f"apenas reproduz. Cite o {outro} como fonte — ou diga 'citando o {outro}' —, nunca como palavra deste parecer.")
    norm = _normalizar_para_comparar(par)
    if par.lstrip()[:1] in ("“", '"', "«"):
        av.append("⚠️ ENTRE ASPAS: o parágrafo inteiro é citação (abre com aspas) — de outro parecer, de voto, de doutrina ou de lei. "
                  "Confira quem fala antes de atribuir.")
    else:
        for sp in re.findall(r"[“\"«]([^”\"»]{15,})[”\"»]", par):
            if _contem_fragmentos(_normalizar_para_comparar(sp), trecho):
                av.append("⚠️ ENTRE ASPAS: o trecho está entre aspas no original — é citação de terceiro, não voz do relator.")
                break
    contexto = (ant[-300:] + " " + par[:400])
    if _RE_VOTO_OUTRO.search(contexto):
        av.append("⚠️ VOTO DE OUTRO JULGADOR: o trecho vem de voto convergente, divergente ou vencido citado no parecer — "
                  "não é o entendimento da Turma.")
    if _RE_DOUTRINA.search(contexto) and any(a.startswith("⚠️ ENTRE ASPAS") for a in av):
        av.append("⚠️ DOUTRINA: o trecho é citação de autor, reproduzida no parecer — atribua ao autor.")
    if not any(a.startswith("⚠️ TRANSCRIÇÃO") for a in av) and _RE_ROTULO_CITACAO.search(ant.strip()):
        av.append("⚠️ CITAÇÃO ANUNCIADA: o parágrafo anterior introduz uma citação ('senão vejamos', 'in verbis', ':'). "
                  "Confira se o trecho é do parecer ou do que ele cita.")
    frs = _fragmentos(trecho)
    if frs:
        i = norm.find(_normalizar_para_comparar(frs[0]))
        if i > 0 and _RE_NEGACAO.search(" " + norm[max(0, i - 40):i]):
            av.append("⚠️ NEGAÇÃO LOGO ANTES: o original tem uma negação imediatamente antes do trecho — sem ela, o sentido pode inverter.")
    return av


# ───────────────────────── recibo de custódia ─────────────────────────
# Mesmo formato dos servidores do TJRO, STJ, TJSE, TCE-RO e TRF1: um arquivo por documento, com o texto que a fonte
# entregou e hash. Um verificador de ficha de citação (ex.: o lint da skill peticao-rg) confere a peça CONTRA ESTE
# ARQUIVO, não contra o que o modelo diz que leu. `trechos_transcritos` e `trecho_divergente` guardam o que NÃO é palavra
# do órgão — ementa transcrita de outro parecer, citação entre aspas, doutrina, voto de outro julgador —, em bruto,
# para o verificador avisar em vez de aprovar. Permissão 0600: o recibo pode nomear quem consultou ou foi representado.

def _blocos_alheios(texto: str, processo_proprio: str) -> tuple[list[str], list[str]]:
    """(transcritos, divergentes) — parágrafos em bruto que não são a voz do órgão."""
    pars = [x for x in (texto or "").split("\n") if x.strip()]
    proprio = _canon_ref(processo_proprio) if processo_proprio else None
    transc, diverg = [], []
    for i, par in enumerate(pars):
        f = _RE_FECHO.search(par)
        outro = _canon_ref(f.group("proc")) if f else None
        if (outro and outro != proprio) or par.lstrip()[:1] in ("“", '"', "«"):
            transc.append(par.strip())
        if _RE_VOTO_OUTRO.search((pars[i - 1][-300:] if i else "") + " " + par[:400]) and par.lstrip()[:1] in ("“", '"', "«"):
            diverg.append(par.strip())
    return transc, diverg


def _gravar_recibo(ident: str, tribunal: str, nr_processo: str, texto: str, campos: dict, processo_proprio: str = "") -> str | None:
    """Grava `<PASTA_RECIBOS>/<ident>.json`. Nunca derruba a ferramenta: falha de disco devolve None."""
    ident = re.sub(r"[^\w-]", "", str(ident or ""))
    if not ident or not (texto or "").strip():
        return None
    transc, diverg = _blocos_alheios(texto, processo_proprio) if tribunal == "TED-OAB/SP" else ([], [])
    rec = {"id_documento": ident, "nr_processo": nr_processo or "", "tribunal": tribunal, **campos, "texto": texto,
           "trechos_transcritos": transc, "trecho_divergente": "\n".join(diverg),
           "sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
           "obtido_em": time.strftime("%Y-%m-%dT%H:%M:%S"), "servidor": f"mcp-oab-jurisprudencia v{VERSAO}",
           "normalizacao": "trechos em bruto, recortados de `texto` — normalize com a sua própria regra antes de comparar"}
    try:
        _garantir_pasta(PASTA_RECIBOS)
        caminho = os.path.join(PASTA_RECIBOS, f"{ident}.json")
        tmp = f"{caminho}.{os.getpid()}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
        os.replace(tmp, caminho)
        return caminho
    except OSError:
        return None


def _recibo_ted(d) -> str:
    c = _gravar_recibo(d["guid"], "TED-OAB/SP", d["processo"] or d["numero"] or "", d["texto"] or "",
                       {"tipo": "PARECER (consulta em tese)", "numero_ementa": d["numero"], "data_julgamento": _data_br(d["data_julgamento"]),
                        "orgao": "1ª Turma de Ética Profissional", "relator": d["relator"] or "", "link": _link(d)},
                       d["processo"] or "")
    return f"Recibo gravado: {c}" if c else "Recibo NÃO gravado (falha de disco) — a conferência vale, mas não fica prova em arquivo."


async def _verificar(numero: str | None, guid: str | None, trecho: str) -> str:
    if len(_uma_linha(trecho)) < 15:
        return "Trecho curto demais para conferência (mínimo ~15 caracteres)."
    con = _db()
    try:
        linhas = _localizar(con, numero, guid)
        if len(linhas) != 1:
            return (f"Não foi possível fixar UMA ementa para '{numero or guid}' ({len(linhas)} encontradas) — localize pelo guid com "
                    "`buscar_ementas_ted_sp`. Conferência NÃO realizada.")
        d = linhas[0]
        fonte = "portal (reaberto agora)"
        try:
            h = await _baixar_modal(d["slug"], d["guid"])
            _gravar_parecer(con, d["guid"], h)
            con.commit()
            d = con.execute("SELECT * FROM ementas WHERE guid=?", (d["guid"],)).fetchone()
        except PortalRecusou as ex:
            if not d["texto"]:
                return str(ex)
            fonte = f"cópia local de {d['baixado_em']} (portal indisponível agora: teto de verificação reduzido)"
        alvo, texto = _normalizar_para_comparar(trecho), _normalizar_para_comparar(d["texto"] or "")
        partes = len(_fragmentos(trecho))
        nota = (f"\n⚠️ Conferido em {partes} fragmentos separados por [...], na ordem e no mesmo campo — o que está SOB o [...] não foi "
                "conferido: releia o original antes de afirmar que a omissão não muda o sentido." if partes > 1 else "")
        onde_ementa = _contem_fragmentos(_normalizar_para_comparar(d["ementa"] or ""), trecho)
        if _contem_fragmentos(texto, trecho):
            alertas = [] if onde_ementa else _alertas_atribuicao(d["texto"] or "", trecho, d["processo"] or "")
            nota += "\n" + _recibo_ted(d)
            veredito = "✅ CONFERE" if not [a for a in alertas if a.startswith("⚠️")] else "✅ CONFERE O TEXTO — ⚠️ MAS CONFIRA DE QUEM É A FRASE"
            return (f"{veredito} — trecho literal presente {'na EMENTA' if onde_ementa else 'no RELATÓRIO/PARECER (fora da ementa)'} de "
                    f"{_citacao(d)}.\nFonte da conferência: {fonte}\nLink: {_link(d)}{nota}"
                    + ("\n" + "\n".join(alertas) if alertas else ""))
        palavras = [p for p in alvo.split(" ") if len(p) > 3]
        presentes = sum(1 for p in palavras if p in texto)
        return (f"❌ NÃO CONFERE literalmente em {_citacao(d)} ({presentes}/{len(palavras)} palavras do trecho aparecem soltas no texto). "
                f"Não ponha entre aspas: releia com `obter_parecer_ted_sp` e copie do original.\nFonte da conferência: {fonte}")
    finally:
        con.close()


def _buscar_disc(consulta: str, ano: int | None, pagina: int, por_pagina: int) -> str:
    con = _db()
    try:
        match = _traduzir_consulta(consulta)
        if not match:
            return "Informe a consulta (ex.: \"prestação de contas\"; locupletamento; abandono E causa)."
        cond, args = ["disciplinares_fts MATCH ?"], [match]
        if ano:
            cond.append("substr(d.data,1,4)=?")
            args.append(str(ano))
        por_pagina = max(1, min(25, por_pagina))
        onde = " AND ".join(cond)
        try:
            total = con.execute(f"SELECT COUNT(*) FROM disciplinares_fts f JOIN disciplinares d ON d.id=f.id WHERE {onde}", args).fetchone()[0]
            linhas = con.execute(f"SELECT d.*, bm25(disciplinares_fts, 0, 4.0, 1.0) AS rk FROM disciplinares_fts f JOIN disciplinares d ON d.id=f.id "
                                 f"WHERE {onde} ORDER BY rk LIMIT ? OFFSET ?", args + [por_pagina, (max(1, pagina) - 1) * por_pagina]).fetchall()
        except sqlite3.OperationalError as ex:
            return f"Consulta não entendida pelo índice ({ex}). Simplifique: termos soltos, \"frase\", E / OU / NÃO, prefixo*."
        out = [_cobertura(con), AVISO_NATUREZA_DISC, f"Consulta: {match} · {total} resultado(s) · página {max(1, pagina)}", ""]
        if not total:
            out.append("Nenhum acórdão disciplinar (2010–2014) com esses termos no índice local.")
        for i, d in enumerate(linhas, 1 + (max(1, pagina) - 1) * por_pagina):
            out += [f"{i}. TED-OAB/SP, {d['turma'] or 'Turma Disciplinar'}, Acórdão nº {d['acordao'] or '?'}, PD {d['processo'] or '?'}, "
                    f"Rel. {d['relator'] or '?'}, j. {_data_br(d['data'])} · idEmenta {d['id']}", f"   {d['ementa']}",
                    f"   Link: {URL_DISC_TEXTO}{d['id']}", ""]
        return "\n".join(out)
    finally:
        con.close()


async def _obter_disc(id_ementa: int, reabrir_na_fonte: bool) -> str:
    con = _db()
    try:
        d = con.execute("SELECT * FROM disciplinares WHERE id=?", (id_ementa,)).fetchone()
        if not d:
            return f"{_cobertura(con)}\nidEmenta {id_ementa} não está no índice local."
        if reabrir_na_fonte or not d["texto"]:
            _gravar_disc_texto(con, id_ementa, await _requisitar(URL_DISC_TEXTO + str(id_ementa), f"acórdão disciplinar {id_ementa}"))
            con.commit()
            d = con.execute("SELECT * FROM disciplinares WHERE id=?", (id_ementa,)).fetchone()
        return "\n".join([AVISO_NATUREZA_DISC, f"Link: {URL_DISC_TEXTO}{id_ementa} · baixado em {d['baixado_em']}", "", d["texto"] or ""])
    finally:
        con.close()


# ───────────────────────── Conselho Federal da OAB (API ao vivo) ─────────────────────────

_MODOS = {"todos": "TODOS_OS_TERMOS", "um": "UM_DOS_TERMOS", "frase": "FRASE_EXATA"}
_CTX_NORMAS = {"sumula": "DECISAO_SUMULA", "provimento": "DECISAO_PROVIMENTO", "resolucao": "DECISAO_RESOLUCAO",
               "instrucao_normativa": "DECISAO_INSTRUCAO_NORMATIVA", "portaria": "DECISAO_PORTARIA"}
_ORGANIZACOES = {"federal": "49", "sp": "25"}  # ids observados nas facetas do portal em 19/09/2026
_TIPOS_EMENTA = {"disciplinar": "PROCESSO_DISCIPLINAR", "outro": "OUTRO"}


async def _cfoab_consultar(contexto: str, params: dict, operacao: str) -> dict:
    corpo = await _requisitar(CFOAB_API + contexto, operacao, params=params)
    try:
        j = json.loads(corpo)
    except Exception:
        j = None
    if not isinstance(j, dict) or "resultado" not in j:
        raise PortalRecusou(f"[PESQUISA NÃO REALIZADA] resposta inesperada da API do Conselho Federal em {operacao} — o portal pode ter mudado. "
                            "Isto NÃO é 'não localizado'.")
    return j


_RE_SUFIXO_ORGAO = re.compile(r"\s*/\s*[A-Za-zÀ-Üà-ü][A-Za-zÀ-Üà-ü\-]{1,12}\s*$")


def _numero_cfoab(n: str) -> tuple[str, str]:
    """'49.0000.2025.000148-3/OEP' → ('49.0000.2025.000148-3', 'OEP'). A API não aceita o sufixo do órgão."""
    n = _uma_linha(n)
    m = _RE_SUFIXO_ORGAO.search(n)
    return (n[:m.start()].strip(), m.group(0).strip(" /")) if m else (n, "")


def _cfoab_total(j: dict) -> int:
    try:
        return int((j.get("metadata") or [{}])[0].get("totalRegistros") or 0)
    except Exception:
        return 0


def _iso_data(v: Any) -> str:
    v = str(v or "")
    return v[:10] if re.match(r"\d{4}-\d{2}-\d{2}", v) else ""


def _cfoab_orgao(r: dict) -> str:
    o = r.get("orgaoJulgador") or {}
    org, setor = _uma_linha(o.get("nomeOrganizacao") or ""), _uma_linha(o.get("nomeSetor") or "")
    org = "CFOAB" if _fold(org) == "conselho federal" else org.replace("Conselho Seccional - ", "OAB/")
    return f"{org}, {setor}" if setor else org


def _cfoab_publicacao(r: dict) -> str:
    pd = r.get("publicacaoDiario") or {}
    if not pd:
        return ""
    partes = [f"DEOAB {_data_br(_iso_data(pd.get('dataPublicacao')))}"]
    if pd.get("edicao"):
        partes.append(f"ed. {pd['edicao']}")
    if pd.get("pagina"):
        partes.append(f"p. {pd['pagina']}")
    return ", ".join(partes)


def _cfoab_fecho_legado(r: dict) -> str:
    """Ementa antiga (migrada sem campos estruturados) traz a referência no parêntese final do próprio texto — devolve-o literal."""
    t = _uma_linha(r.get("descricaoEmenta") or "")
    aberturas = list(re.finditer(r"(?i)\((?=\s*(?:proc|recurso|consulta|representa|rel\.|relator|ementa)\b)", t))
    if not aberturas or not t.rstrip(" .").endswith(")"):
        return ""
    fecho = t[aberturas[-1].end():].rstrip(" .")[:-1].strip()
    return fecho if 15 <= len(fecho) <= 500 else ""


def _cfoab_citacao(r: dict) -> str:
    """Montada só de campos estruturados da API; o título do feito vem literal da publicação no Diário Eletrônico da OAB."""
    partes = [_cfoab_orgao(r)]
    titulo = _uma_linha(((r.get("publicacaoDiario") or {}).get("titulo") or "")).rstrip(".")
    if not titulo and not r.get("numeroProcesso"):
        legado = _cfoab_fecho_legado(r)
        return f"{_cfoab_orgao(r)} — referência literal do fecho da ementa: ({legado})" if legado else \
               f"{_cfoab_orgao(r)} — ementa antiga sem dados estruturados nem fecho identificável: leia o texto"
    partes.append(titulo or f"Proc. {r.get('numeroProcesso')}")
    if r.get("numeroEmenta"):
        partes.append(f"Ementa n. {r['numeroEmenta']}")
    rel = [x for x in (r.get("relatores") or []) if x.get("nome")]
    # Em ementa migrada o campo "Relator Atual" do portal pode não ser quem relatou (visto em 19/09/2026: campo dizia um nome, o texto
    # publicado dizia outro). O texto publicado no DEOAB prevalece; a divergência é dita, nunca resolvida em silêncio.
    mt = re.search(r"(?i)\bRelator(?:a)?(?:\s+para\s+o\s+ac[óo]rd[ãa]o)?\s*:\s*(?:Conselheir[oa]\s+Federal\s+)?([A-ZÀ-Ü][^().;]{5,80}?)\s*(?=\(|\.|;)", r.get("descricaoEmenta") or "")
    if mt and rel and not any(_fold(_uma_linha(mt.group(1))) in _fold(x["nome"]) or _fold(x["nome"]) in _fold(mt.group(1)) for x in rel):
        do_texto = _uma_linha(mt.group(1))
        estrut = ", ".join(_uma_linha(x["nome"]) for x in rel)
        # "Relator ad hoc" explica a divergência: o portal indexa quem assinou, o texto diz quem relatou.
        # o fecho assina "Fulano, Relator ad hoc" — o nome vem ANTES do rótulo
        ad_hoc = re.search(r"(?i)([A-ZÀ-Ü][^.;,]{5,80}),\s*Relator(?:a)?\s+ad\s*hoc\b", r.get("descricaoEmenta") or "")
        if ad_hoc and any(_fold(x["nome"].split()[0]) in _fold(ad_hoc.group(1)) for x in rel if x.get("nome")):
            partes.append(f"Rel. {do_texto}; {_uma_linha(ad_hoc.group(1))} assinou como Relator(a) ad hoc")
        else:
            partes.append(f"Rel. {do_texto} [⚠️ divergência na própria fonte: o texto publicado diz {do_texto}, o campo estruturado do portal diz "
                          f"{estrut}; prevalece o texto publicado — confira no original antes de citar]")
    elif rel:
        partes.append("; ".join(f"{'Rel.' if 'relator' in _fold(x.get('tipo') or 'relator') else x.get('tipo')} {_uma_linha(x['nome'])}"
                                + (f" ({x['tipo']})" if len(rel) > 1 and x.get("tipo") else "") for x in rel))
    j = _iso_data((r.get("reuniao") or {}).get("data"))
    if j:
        partes.append(f"j. {_data_br(j)}")
    pub = _cfoab_publicacao(r)
    if pub:
        partes.append(pub)
    return ", ".join(partes)


def _recibo_cfoab(r: dict) -> str | None:
    org = (r.get("orgaoJulgador") or {}).get("nomeOrganizacao") or ""
    texto = _uma_linha(r.get("descricaoEmenta") or "") + "\n\n" + _uma_linha(r.get("descricaoAcordao") or "")
    return _gravar_recibo(str(r.get("id") or ""), "CFOAB" if _fold(org) == "conselho federal" else "OAB/SP", r.get("numeroProcesso") or "",
                          texto.strip(), {"tipo": "EMENTA E ACÓRDÃO (o voto não é publicado)", "numero_ementa": r.get("numeroEmenta") or "",
                                          "data_julgamento": _data_br(_iso_data((r.get("reuniao") or {}).get("data"))),
                                          "orgao": _cfoab_orgao(r), "citacao": _cfoab_citacao(r)})


def _cfoab_format(r: dict, i: int, integral: bool) -> list[str]:
    em, ac = _uma_linha(r.get("descricaoEmenta") or ""), _uma_linha(r.get("descricaoAcordao") or "")
    if not integral and len(em) > 900:
        em = em[:900] + "… [cortado — repita a busca com integral=true ou use `obter_ementa_cfoab`]"
    out = [f"{i}. {_cfoab_citacao(r)}", f"   Processo {r.get('numeroProcesso') or '(ementa antiga, sem número estruturado)'} · tipo {r.get('tipoEmenta') or '?'} · id {r.get('id')}", f"   EMENTA: {em}"]
    if ac:
        out.append(f"   {ac if integral else ac[:500] + ('…' if len(ac) > 500 else '')}")
    disp = "; ".join(_uma_linha(d.get("descricao") or "") for d in (r.get("dispositivosNormativos") or []))
    if disp:
        out.append(f"   Dispositivos indexados pelo portal: {disp}")
    if r.get("documentos"):
        out.append(f"   Documentos anexos no portal: {len(r['documentos'])} (abrir em {CFOAB}/?contexto=EMENTA)")
    if integral:
        c = _recibo_cfoab(r)
        out.append(f"   Recibo: {c}" if c else "   Recibo NÃO gravado (falha de disco).")
    out.append("")
    return out


def _achar_faceta(grupo: list, texto: str) -> list[dict]:
    alvo = _fold(texto)
    return [g for g in (grupo or []) if all(p in _fold(g.get("nome") or "") for p in alvo.split())]


async def _cfoab_buscar(consulta: str, modo: str, organizacao: str | None, orgao: str | None, relator: str | None, tipo: str | None,
                        ordenacao: str, pagina: int, por_pagina: int, numero_processo: str | None = None, integral: bool = False,
                        triagem: bool = False) -> str:
    if modo not in _MODOS:
        raise ValueError(f"modo deve ser um de {list(_MODOS)}")
    if not _uma_linha(consulta) and not numero_processo:
        raise ValueError("informe a consulta ou o numero_processo")
    por_pagina = 20 if triagem else max(1, min(20, por_pagina))
    numero_processo, sufixo_orgao = _numero_cfoab(numero_processo or "")
    params = {"pesquisaLivre": _uma_linha(consulta), "modoPesquisaLivre": _MODOS[modo], "numeroProcesso": numero_processo,
              "page": str(max(1, pagina) - 1), "size": str(por_pagina), "sort": "score,desc" if ordenacao == "relevantes" else "dataOrdenacao,desc"}
    if organizacao:
        if _fold(organizacao) not in _ORGANIZACOES:
            raise ValueError(f"organizacao deve ser um de {list(_ORGANIZACOES)} (ou omita)")
        params["idsOrganizacao"] = _ORGANIZACOES[_fold(organizacao)]
    if tipo:
        if _fold(tipo) not in _TIPOS_EMENTA:
            raise ValueError(f"tipo deve ser um de {list(_TIPOS_EMENTA)} (ou omita)")
        params["tipoEmentas"] = _TIPOS_EMENTA[_fold(tipo)]
    avisos: list[str] = []
    rotulo = f"busca CFOAB '{params['pesquisaLivre'] or params['numeroProcesso']}'"
    if orgao or relator:  # ids de faceta só existem na resposta: 1ª chamada sem o filtro resolve o id, 2ª aplica
        j0 = await _cfoab_consultar("EMENTA", dict(params, size="1"), rotulo + " (facetas)")
        for texto, grupo, campo in ((orgao, "grupoOrgaoJulgador", "orgaoJulgador"), (relator, "grupoRelatores", "relatoresIdMembro")):
            if not texto:
                continue
            achados = _achar_faceta(j0.get(grupo), texto)
            if len(achados) != 1:
                nomes = "; ".join(f"{g.get('nome')} ({g.get('count')})" for g in (achados or j0.get(grupo) or [])[:15])
                return (f"Filtro '{texto}' {'é ambíguo' if achados else 'não casa com nenhuma faceta'} para esta consulta. Opções: {nomes or 'nenhuma'}. "
                        "Refine o texto do filtro. (Isto não é resultado de pesquisa.)")
            params[campo] = str(achados[0]["_id"])
            avisos.append(f"filtro aplicado: {achados[0].get('nome')}")
    j = await _cfoab_consultar("EMENTA", params, rotulo)
    total = _cfoab_total(j)
    out = [f"[Jurisprudência do Conselho Federal da OAB — busca AO VIVO no portal (jurisprudencia.oab.org.br) · modo: {modo} · "
           f"ordenação: {'relevância' if ordenacao == 'relevantes' else 'data'}]", AVISO_NATUREZA_CFOAB,
           f"{total} resultado(s) · página {max(1, pagina)} ({por_pagina} por página)" + "".join(f" · {a}" for a in avisos), ""]
    if not total and numero_processo:
        out.append(f"O portal do Conselho Federal NÃO tem ementa publicada sob o processo {numero_processo}"
                   + (f" — o sufixo do órgão '/{sufixo_orgao}' foi removido da consulta, porque a API não o aceita" if sufixo_orgao else "")
                   + ". Isto é 'sem ementa publicada': o feito pode existir, estar em tramitação ou não ter sido ementado. Confira o número "
                     "(formato NN.NNNN.AAAA.NNNNNN-D). NÃO é 'o Conselho Federal nunca decidiu'.")
    elif not total:
        out.append("Nenhuma ementa com esses termos no portal do Conselho Federal. A busca é textual: tente modo='um', sinônimos, ou menos termos.")
    if triagem and total:
        out += ["TRIAGEM — até 20 candidatos em lista curta, para VOCÊ ler, descartar o que não trata do assunto e reordenar "
                "(no TJSE, reordenar assim subiu a precisão nos 10 primeiros de 40,5 % para 59,5 %). Depois leia a ementa inteira do que usar.", ""]
        for i, r in enumerate(j.get("resultado") or [], 1 + (max(1, pagina) - 1) * por_pagina):
            em = _uma_linha(r.get("descricaoEmenta") or "")
            out.append(f"{i}. [{_cfoab_orgao(r)} · proc. {r.get('numeroProcesso') or 's/ nº'} · id {r.get('id')}] {em[:700]}{'…' if len(em) > 700 else ''}")
        return "\n".join(out)
    for i, r in enumerate(j.get("resultado") or [], 1 + (max(1, pagina) - 1) * por_pagina):
        out += _cfoab_format(r, i, integral)
    if total:
        org = "; ".join(f"{g.get('nome')} ({g.get('count')})" for g in (j.get("grupoOrgaoJulgador") or [])[:10])
        out += [f"Distribuição por órgão: {org}", "Antes de aspas: `verificar_citacao_cfoab`. O modo 'frase' do portal tolera termos separados — confira o trecho. "
                "Decisão antiga pode estar superada por súmula ou por alteração do Regulamento Geral/Provimento: cheque `buscar_normas_cfoab` e prefira a mais recente."]
    return "\n".join(out)


async def _cfoab_verificar(numero_processo: str, trecho: str) -> str:
    if len(_uma_linha(trecho)) < 15:
        return "Trecho curto demais para conferência (mínimo ~15 caracteres)."
    numero_processo = _numero_cfoab(numero_processo)[0]
    j = await _cfoab_consultar("EMENTA", {"pesquisaLivre": "", "modoPesquisaLivre": "TODOS_OS_TERMOS", "numeroProcesso": numero_processo,
                                           "page": "0", "size": "20", "sort": "dataOrdenacao,desc"}, f"conferência CFOAB {numero_processo}")
    res = j.get("resultado") or []
    if not res:
        return f"Nenhuma ementa sob o processo '{numero_processo}' no portal do Conselho Federal — conferência NÃO realizada (confira o número: formato NN.NNNN.AAAA.NNNNNN-D)."
    alvo = _normalizar_para_comparar(trecho)
    partes = len(_fragmentos(trecho))
    nota = (f"\n⚠️ Conferido em {partes} fragmentos separados por [...], na ordem e no mesmo campo — o que está SOB o [...] não foi "
            "conferido: releia o original antes de afirmar que a omissão não muda o sentido." if partes > 1 else "")
    for r in res:
        na_ementa = _contem_fragmentos(_normalizar_para_comparar(r.get("descricaoEmenta") or ""), trecho)
        if na_ementa or _contem_fragmentos(_normalizar_para_comparar(r.get("descricaoAcordao") or ""), trecho):
            campo_txt = (r.get("descricaoEmenta") if na_ementa else r.get("descricaoAcordao")) or ""
            alertas = [a for a in _alertas_atribuicao(campo_txt, trecho, "", com_fecho=False) if not a.startswith("ℹ️")]
            veredito = "✅ CONFERE" if not alertas else "✅ CONFERE O TEXTO — ⚠️ MAS CONFIRA DE QUEM É A FRASE"
            c = _recibo_cfoab(r)
            nota += f"\nRecibo: {c}" if c else "\nRecibo NÃO gravado (falha de disco)."
            return (f"{veredito} — trecho literal presente {'na EMENTA' if na_ementa else 'no ACÓRDÃO (dispositivo)'} de {_cfoab_citacao(r)}.\n"
                    "Fonte da conferência: portal do Conselho Federal, reaberto agora (ementa + acórdão; o voto não é publicado na API)." + nota
                    + ("\n" + "\n".join(alertas) if alertas else ""))
    return (f"❌ NÃO CONFERE literalmente em nenhuma das {len(res)} ementa(s) do processo {numero_processo}. Não ponha entre aspas: "
            "releia com `obter_ementa_cfoab` e copie do original.")


async def _cfoab_normas(consulta: str, tipo: str, modo: str, pagina: int, por_pagina: int, integral: bool = False) -> str:
    if _fold(tipo) not in _CTX_NORMAS:
        raise ValueError(f"tipo deve ser um de {list(_CTX_NORMAS)}")
    if modo not in _MODOS:
        raise ValueError(f"modo deve ser um de {list(_MODOS)}")
    por_pagina = max(1, min(20, por_pagina))
    j = await _cfoab_consultar(_CTX_NORMAS[_fold(tipo)], {"pesquisaLivre": _uma_linha(consulta), "modoPesquisaLivre": _MODOS[modo], "page": str(max(1, pagina) - 1),
                                                          "size": str(por_pagina), "sort": "score,desc" if _uma_linha(consulta) else "dataOrdenacao,desc"},
                               f"normas CFOAB ({tipo}) '{consulta}'")
    total = _cfoab_total(j)
    out = [f"[Conselho Federal da OAB — {tipo} · busca AO VIVO · {total} resultado(s) · página {max(1, pagina)}]", ""]
    if not total:
        out.append(f"Nenhum(a) {tipo} com esses termos. Consulta vazia lista tudo por data.")
    for i, r in enumerate(j.get("resultado") or [], 1 + (max(1, pagina) - 1) * por_pagina):
        corpo = _html_para_texto(r.get("texto") or "").replace("ㅤ", "").strip()
        desc = _uma_linha(r.get("descricao") or "")
        texto = (desc + ("\n   " + corpo.replace("\n", "\n   ") if corpo else "")).strip() if integral else desc
        teto = 3000 if integral else 400
        alvo = _fold(_uma_linha(consulta))
        so_corpo = bool(alvo) and alvo not in _fold(desc) and alvo not in _fold(str(r.get("numero") or ""))
        out += [f"{i}. {_uma_linha(((r.get('publicacaoDiario') or {}).get('titulo')) or (str(r.get('tipoDecisao') or tipo).title() + ' n. ' + str(r.get('numero') or '?')))} · "
                f"{_cfoab_orgao(r)} · {_data_br(_iso_data(r.get('data')))}" + (f" · {_cfoab_publicacao(r)}" if _cfoab_publicacao(r) else ""),
                f"   Processo {r.get('numeroProcesso') or '—'} · id {r.get('id')}"
                + ("  ⚠️ os termos não aparecem na ementa/número — casaram só no corpo, provável ruído" if so_corpo else ""),
                f"   {texto[:teto]}" + ("… [cortado]" if len(texto) > teto else ""), ""]
    if not integral and (j.get("resultado") or []):
        out.append("Acima está só a ementa/1ª linha de cada norma. Para o texto completo (necessário antes de citar), repita com integral=true "
                   "e por_pagina baixo, ou peça o item específico.")
    return "\n".join(out)


def _diagnostico() -> str:
    out = [f"MCP TED-OAB/SP v{VERSAO} · base: {ARQUIVO_DB}"]
    try:
        con = _db()
        out.append(_cobertura(con))
        pend = con.execute("SELECT COUNT(*) FROM ementas WHERE texto IS NULL").fetchone()[0]
        pend_d = con.execute("SELECT COUNT(*) FROM disciplinares WHERE texto IS NULL").fetchone()[0]
        anos_ok = [r[0][-4:] for r in con.execute("SELECT chave FROM meta WHERE chave LIKE 'ano_completo_%' ORDER BY chave")]
        diverg = con.execute("SELECT COUNT(*) FROM ementas WHERE texto IS NOT NULL AND fecho<>'' AND length(numero_dig)>=4 "
                              "AND processo_dig NOT LIKE '%'||substr(numero_dig,1,4)||'%'").fetchone()[0]
        sem_fecho = con.execute("SELECT COUNT(*) FROM ementas WHERE texto IS NOT NULL AND fecho=''").fetchone()[0]
        out += [f"Pendentes de download: {pend} pareceres, {pend_d} acórdãos disciplinares",
                f"Anos com lista varrida até o fim: {', '.join(anos_ok) or 'nenhum'}",
                f"Pareceres sem fecho identificado (relator/data vazios): {sem_fecho}",
                f"Pareceres cujo processo extraído não contém o nº da ementa (conferir fecho à mão): {diverg}"]
        con.close()
    except BaseAusente as ex:
        out.append(str(ex))
    tudo = _ler_estado()
    agora = time.time()
    for host, rotulo in (("oabsp", "OAB/SP (TED)"), ("cfoab", "Conselho Federal (API ao vivo)")):
        e = tudo[host]
        ate = max(e["pausa_ate"], _PAUSA_MEMORIA.get(host, 0.0))
        esp, jan = ESCADA[host][e["nivel"]]
        reqs = e["requisicoes"]
        out.append(f"Disjuntor {rotulo}: {'EM PAUSA até ' + time.strftime('%d/%m %H:%M', time.localtime(ate)) + ' — ' + e['motivo'] if agora < ate else 'livre'} · "
                   f"{len([t for t in reqs if agora - t < JANELA_S])}/{jan} em 10 min · {len(reqs)}/{DIA_MAX[host]} em 24 h · espaçamento {esp:.1f}s"
                   + (f" · RITMO APERTADO: degrau {e['nivel']} de {len(ESCADA[host]) - 1}, {SUCESSOS_PARA_RELAXAR - e['sucessos']} "
                      "consulta(s) limpa(s) para afrouxar" if e["nivel"] else ""))
        for inc in e["incidentes"][-5:]:
            out.append(f"  incidente {inc.get('quando')}: {inc.get('motivo')}")
    out.append(f"User-Agent: {'definido por TEDSP_USER_AGENT' if os.environ.get('TEDSP_USER_AGENT') else 'padrão (identificado)'} · "
               f"Modo: {'híbrido — complementos: ' + ', '.join(COMPLEMENTOS) if COMPLEMENTOS else 'autônomo (TEDSP_COMPLEMENTOS vazio)'} · "
               f"Dados em: {PASTA_DADOS}")
    return "\n".join(out)


# ───────────────────────── crédito, aviso de versão e relato de erro ─────────────────────────
# Portado do servidor do TJSE. Uma consulta ao GitHub (releases/latest) por processo, em THREAD DE FUNDO desde a subida:
# nenhuma resposta é atrasada — as ferramentas só leem o resultado se ele já chegou. Se houver versão MAIS NOVA, a primeira
# resposta ganha uma linha com o endereço FIXO da página de versões, nunca uma URL vinda da resposta da API. Sem rede, com
# erro, com repositório privado (404) ou passados 2 s: silêncio. Só o GitHub vê o IP; nada da pesquisa nem do caso sai daqui,
# e esta consulta não passa pelo disjuntor, que é dos portais da OAB. Desligar: TEDSP_MCP_SEM_AVISO_ATUALIZACAO=1.
ISSUES_NOVA = f"https://github.com/{REPO_GITHUB}/issues/new"
RELEASES_API = f"https://api.github.com/repos/{REPO_GITHUB}/releases/latest"
RELEASES_PAGINA = f"https://github.com/{REPO_GITHUB}/releases/latest"
CREDITO = "_Esta ferramenta foi desenvolvida por @robertogrecia (Roberto Grécia Bessa, OAB/RO 7865-A). Obrigado por usar!_"
_RE_TAG = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
_credito_dado = False
_aviso_dado = False
_versao_nova: str | None = None


def versao_mais_nova(atual: str, outra: str) -> bool:
    """True só se `outra` for estritamente maior. Formato estranho é False: aviso errado é pior que aviso nenhum."""
    a, b = _RE_TAG.match(str(atual or "").strip()), _RE_TAG.match(str(outra or "").strip())
    if not a or not b:
        return False
    return tuple(int(x) for x in b.groups()) > tuple(int(x) for x in a.groups())


def _checar_versao(timeout: float = 2.0) -> None:
    """Roda em thread de fundo. NUNCA levanta: qualquer falha é silêncio."""
    global _versao_nova
    try:
        if os.environ.get("TEDSP_MCP_SEM_AVISO_ATUALIZACAO") == "1" or httpx is None:
            return
        r = httpx.get(RELEASES_API, timeout=timeout, headers={"Accept": "application/vnd.github+json",
                                                              "User-Agent": "mcp-oab-jurisprudencia"})
        if r.status_code != 200:
            return
        tag = str((r.json() or {}).get("tag_name") or "").strip()
        if versao_mais_nova(VERSAO, tag):
            _versao_nova = tag.lstrip("v")
    except Exception:
        return


def iniciar_checagem_versao() -> None:
    if os.environ.get("TEDSP_MCP_SEM_AVISO_ATUALIZACAO") == "1":
        return
    threading.Thread(target=_checar_versao, daemon=True, name="oab-checar-versao").start()


def com_avisos(texto: str) -> str:
    """Crédito (uma vez por processo) e, se houver, aviso de versão (uma vez). Nunca espera rede."""
    global _credito_dado, _aviso_dado
    partes = [texto]
    if not _credito_dado:
        _credito_dado = True
        partes.append(CREDITO)
    if _versao_nova and not _aviso_dado:
        _aviso_dado = True
        partes.append(f"_Há uma versão mais nova (v{_versao_nova}); você usa a v{VERSAO}. Baixe em {RELEASES_PAGINA}_")
    return "\n\n".join(partes)


def _link_relato(tipo: str) -> str:
    """Nova issue no GitHub JÁ PREENCHIDA só com dado técnico — NUNCA com o texto da busca, número de processo ou nome
    (issues são públicas). Só para erro de verdade, nunca para PESQUISA NÃO REALIZADA (isso é o portal, não defeito)."""
    titulo = f"Erro {tipo} na v{VERSAO}"
    corpo = ("**Relato gerado pela ferramenta** (revise antes de enviar; não inclua nome de parte, número de processo nem o "
             "texto da sua busca — issues são públicas)\n\n"
             f"- Versão: {VERSAO}\n- Sistema: {platform.system()} {platform.release()}\n- Python: {platform.python_version()}\n"
             f"- Tipo do erro: {tipo}\n\n**O que eu estava fazendo:** \n\n**Desde quando acontece?** \n")
    return f"{ISSUES_NOVA}?title={urllib.parse.quote(titulo)}&body={urllib.parse.quote(corpo)}"


def _erro(ex: Exception) -> str:
    if isinstance(ex, (PortalRecusou, BaseAusente)):
        return str(ex)
    if isinstance(ex, ValueError):
        return f"Parâmetro inválido: {ex}"
    return (f"[PESQUISA NÃO REALIZADA] erro interno ({type(ex).__name__}: {ex}) — isto NÃO é 'não localizado'.\n"
            f"Se repetir, é defeito da ferramenta: relate em {_link_relato(type(ex).__name__)}")


# ───────────────────────── registro MCP ─────────────────────────

_HAS_MCP = False
try:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("Jurisprudência OAB (Conselho Federal + TED-SP)")
    iniciar_checagem_versao()

    @mcp.tool()
    async def buscar_ementas_ted_sp(consulta: str = "", grupos: list[list[str]] | None = None, cita: str | None = None,
                                    ano: int | None = None, relator: str | None = None, data_inicio: str | None = None,
                                    data_fim: str | None = None, campo: str = "tudo", exato: bool = False, triagem: bool = False,
                                    pagina: int = 1, por_pagina: int = 10) -> str:
        """Pesquisa o ementário da 1ª Turma de Ética Profissional do TED-OAB/SP (turma deontológica: responde consultas em tese
        sobre ética — publicidade, honorários, sigilo, conflito de interesses, captação, IA, sociedade de advogados, inscrição;
        1994–hoje), no índice local do TEXTO INTEGRAL (ementa + relatório + parecer). A busca do site só casa título.

        Args:
            consulta: pergunta em português corrente funciona — palavras soltas combinam por OU e o ranking ordena, com
                singular/plural automático. O que estiver "entre aspas" é obrigatório. radical$ pega o radical (honorar$).
                Operador em MAIÚSCULAS (E, OU, NÃO) ou parênteses liga o modo avançado, que obedece à letra.
            grupos: listas de sinônimos; OU dentro do grupo, E entre grupos. Ex.: [["publicidade","propaganda"],["instagram","redes sociais"]].
            cita: só pareceres que CITAM esta referência — precedente do TED ('E-4.607/2016', '25.0886.2024.010230-7') ou
                dispositivo ('art. 10 do EAOAB', 'art. 71 do CED'). Pode vir sem `consulta`.
            campo: "tudo" (padrão), "ementa" (título + ementa) ou "titulo".
            exato: desliga singular/plural.
            triagem: True devolve 30 candidatos em lista curta (ementa até 700 caracteres, sem panorama) para VOCÊ ler,
                descartar o que não trata do assunto e reordenar. Faça isso com pergunta ampla; depois leia o inteiro teor.
            data_inicio / data_fim: AAAA-MM-DD, sobre a data de julgamento extraída do fecho.

        Returns:
            Cabeçalho de cobertura (se disser BASE PARCIAL, zero resultado NÃO é 'não localizado'), aviso de natureza,
            panorama das ementas que casam (dispositivos e precedentes mais citados), resultados com link estável e, à
            parte, as ementas que casam só no TÍTULO e ainda não têm texto baixado — essas não podem ser citadas.
            Busca que zera diz qual parte zera. Orientação deontológica não é precedente vinculante."""
        try:
            return com_avisos(_buscar(consulta, ano, relator, data_inicio, data_fim, campo, pagina, por_pagina, grupos, exato, cita, triagem))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def mapa_de_citacoes_ted_sp(ref: str | None = None, tipo: str | None = None, limite: int = 20) -> str:
        """Grafo de citações do TED-OAB/SP, extraído do texto dos pareceres baixados — zero rede.
        Sem `ref`: as referências mais citadas (precedentes do TED e dispositivos; `tipo` = "ted" ou "dispositivo" filtra).
        Com `ref` ('E-4.607/2016', 'art. 34 do EAOAB'): quais pareceres a citam, mais recentes primeiro. Os pareceres
        encadeiam precedentes ("Precedentes: E-4.239/2013, E-4.607/2016") — o grafo mostra a linha de entendimento e
        enxerga precedentes ANTERIORES ao que foi baixado, que o índice não tem em texto."""
        try:
            return com_avisos(_mapa_citacoes(ref, tipo, limite))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def obter_parecer_ted_sp(numero: str | None = None, guid: str | None = None, reabrir_na_fonte: bool = False) -> str:
        """Texto integral (ementa + relatório + parecer) de uma consulta da 1ª Turma de Ética do TED-OAB/SP, com citação montada,
        fecho literal e link estável do portal. numero: nº da ementa no portal ('4989-0/2026', 'E-5.123/2019', '1.287') ou nº do
        processo do fecho; ou guid vindo da busca. reabrir_na_fonte=True baixa de novo do portal (1 requisição)."""
        try:
            return com_avisos(await _obter(numero, guid, reabrir_na_fonte))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def verificar_citacao_ted_sp(trecho: str, numero: str | None = None, guid: str | None = None) -> str:
        """Confere se um trecho que vai ENTRE ASPAS está literalmente no parecer/ementa indicado — reabre o texto no portal
        (1 requisição) e compara ignorando acento, caixa e espaçamento. Diz se o trecho está na ementa ou só no parecer.
        Se o portal estiver indisponível, confere contra a cópia local e avisa o teto reduzido."""
        try:
            return com_avisos(await _verificar(numero, guid, trecho))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def buscar_ementas_disciplinares_ted_sp(consulta: str, ano: int | None = None, pagina: int = 1, por_pagina: int = 10) -> str:
        """Pesquisa acórdãos das Turmas Disciplinares do TED-OAB/SP (infrações do art. 34 do EAOAB, penas, suspensão preventiva)
        no índice local da base antiga do portal — que só cobre 2010 a 2014. Traz turma, nº do acórdão, nº do PD, relator, data
        e link. Mesma sintaxe de consulta de `buscar_ementas_ted_sp`. Material didático, não precedente."""
        try:
            return com_avisos(_buscar_disc(consulta, ano, pagina, por_pagina))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def obter_acordao_disciplinar_ted_sp(id_ementa: int, reabrir_na_fonte: bool = False) -> str:
        """Acórdão disciplinar integral do TED-OAB/SP pelo idEmenta devolvido por `buscar_ementas_disciplinares_ted_sp`."""
        try:
            return com_avisos(await _obter_disc(id_ementa, reabrir_na_fonte))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def sincronizar_base_ted_sp(anos: list[int] | None = None, max_requisicoes: int = 120, incluir_disciplinar: bool = True) -> str:
        """Atualiza o índice local (incremental e retomável): listas por ano → pareceres novos → base disciplinar.
        max_requisicoes limita ESTA chamada (padrão 120 ≈ 4 min a 2 s por requisição); a carga inicial completa (~11 mil
        requisições, ~6 h) deve rodar no terminal: `servidor_tedsp.py --sincronizar`. Manutenção: uma vez por mês.
        Recusa do portal arma o disjuntor (cooldown de horas) e interrompe — não insista."""
        try:
            rel = await _sincronizar(anos, max(1, min(600, max_requisicoes)), incluir_disciplinar)
            return (f"Sincronização: {rel['requisicoes']} requisições · {rel['listas']} páginas de lista · {rel['novas']} ementas novas · "
                    f"{rel['pareceres']} pareceres baixados · disciplinar: {rel['disc_novas']} novas, {rel['disc_textos']} textos"
                    + (" · PAROU PELO ORÇAMENTO — chame de novo para continuar" if rel["parou_por_orcamento"] else " · em dia")
                    + "".join(f"\naviso: {a}" for a in rel["avisos"]) + "\n" + _diagnostico())
        except Exception as ex:
            return com_avisos(_erro(ex) + "\n" + _diagnostico())

    @mcp.tool()
    async def buscar_jurisprudencia_cfoab(consulta: str, modo: str = "todos", organizacao: str | None = None, orgao: str | None = None,
                                          relator: str | None = None, tipo: str | None = None, ordenacao: str = "relevantes",
                                          pagina: int = 1, por_pagina: int = 10, integral: bool = False, triagem: bool = False) -> str:
        """Pesquisa AO VIVO o ementário do Conselho Federal da OAB (jurisprudencia.oab.org.br; ~23 mil ementas: Órgão Especial,
        Conselho Pleno, 1ª Câmara — inscrição, incompatibilidade/impedimento, sociedades; 2ª Câmara e turmas — ética e disciplina;
        3ª Câmara — eleições e contas) e, em menor volume, Câmaras Recursais da OAB/SP. Busca textual no servidor, sem base local.
        modo: "todos" (E), "um" (OU) ou "frase". organizacao: "federal" ou "sp". orgao / relator: texto livre, resolvido contra as
        facetas da consulta (ex.: "Primeira Câmara", "Órgão Especial") — custa 1 requisição a mais. tipo: "disciplinar" ou "outro".
        ordenacao: "relevantes" ou "recentes". triagem=true devolve 20 candidatos em lista curta para VOCÊ ler e reordenar.
        integral=true traz ementa e acórdão sem corte (necessário nas ementas antigas,
        que não têm número de processo estruturado e por isso não abrem em `obter_ementa_cfoab`). Devolve ementa + acórdão (dispositivo) + publicação no Diário Eletrônico da OAB;
        o voto não é publicado. Não é jurisprudência judicial."""
        try:
            return com_avisos(await _cfoab_buscar(consulta, modo, organizacao, orgao, relator, tipo, ordenacao, pagina, por_pagina,
                                                  integral=integral, triagem=triagem))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def obter_ementa_cfoab(numero_processo: str) -> str:
        """Todas as ementas do Conselho Federal da OAB sob um número de processo (formato NN.NNNN.AAAA.NNNNNN-D, ex.
        '07.0000.2026.000055-0'), com ementa e acórdão INTEGRAIS, relator, sessão e publicação no DEOAB."""
        try:
            return com_avisos(await _cfoab_buscar("", "todos", None, None, None, None, "recentes", 1, 20, numero_processo=numero_processo, integral=True))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def verificar_citacao_cfoab(numero_processo: str, trecho: str) -> str:
        """Confere, reabrindo o portal do Conselho Federal, se o trecho que vai ENTRE ASPAS está literalmente na ementa ou no
        acórdão do processo indicado (ignora acento, caixa e espaçamento)."""
        try:
            return com_avisos(await _cfoab_verificar(numero_processo, trecho))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def buscar_normas_cfoab(consulta: str = "", tipo: str = "sumula", modo: str = "todos", pagina: int = 1, por_pagina: int = 10,
                                  integral: bool = False) -> str:
        """Súmulas, provimentos, resoluções, instruções normativas e portarias do Conselho Federal da OAB, ao vivo.
        tipo: "sumula" (padrão), "provimento", "resolucao", "instrucao_normativa", "portaria". consulta vazia lista por data.
        Use para checar se há súmula do Conselho Pleno/Órgão Especial sobre o tema antes de citar ementa de câmara.
        Por padrão devolve só ementa/1ª linha de cada norma (o texto integral de 10 provimentos é enorme); integral=true traz o
        texto completo — use com por_pagina baixo. Item cujos termos casaram só no corpo, e não na ementa, vem marcado como
        provável ruído: a busca do portal varre o texto inteiro."""
        try:
            return com_avisos(await _cfoab_normas(consulta, tipo, modo, pagina, por_pagina, integral))
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def pesquisar_tema_no_portal_ted_sp(palavra: str, ano: int | None = None, max_requisicoes: int = 150) -> str:
        """Busca DIRIGIDA, sem precisar da carga completa: pesquisa a palavra/expressão ao vivo no ementário do portal da OAB/SP,
        baixa só os pareceres que voltarem e os indexa — depois `buscar_ementas_ted_sp` pesquisa o texto integral deles.
        Limite que precisa ser dito a quem lê: o portal só casa o termo no TÍTULO da ementa (a lista de descritores em maiúsculas);
        parecer que trate do tema sem tê-lo no título não vem. Rode variações (singular/plural, sinônimo) — cada uma é barata.
        Zero resultado aqui = 'nenhuma ementa com esse termo no título', não 'o TED nunca tratou do tema'."""
        try:
            rel = await _sincronizar_tema(palavra, ano, max(5, min(400, max_requisicoes)))
            con = _db()
            try:
                linhas = [con.execute("SELECT * FROM ementas WHERE guid=?", (g,)).fetchone() for g in rel["guids"]]
                linhas.sort(key=lambda d: d["data_julgamento"] or "", reverse=True)
                out = [f"Busca no portal por '{rel['palavra']}' (título da ementa): {rel['encontradas']} ementa(s) · {rel['pareceres']} parecer(es) "
                       f"baixado(s) agora · {rel['requisicoes']} requisições" + (" · PAROU PELO ORÇAMENTO — chame de novo" if rel["parou_por_orcamento"] else ""),
                       AVISO_NATUREZA_DEONT, ""]
                for i, d in enumerate(linhas, 1):
                    out += [f"{i}. {_citacao(d)}", f"   Ementa nº {d['numero']} · guid {d['guid']}", f"   {d['titulo']}", f"   Link: {_link(d)}", ""]
                return "\n".join(out)
            finally:
                con.close()
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def importar_pacote_ted_sp() -> str:
        """Monta o índice do TED-OAB/SP a partir do pacote pronto publicado no GitHub deste projeto (hash conferido contra
        SHA256SUMS.txt). ZERO requisição ao portal da OAB/SP — é o jeito certo de começar. Não rebaixa o que já existe.
        Se o release ainda não trouxer pacote, diz isso e indica a busca dirigida por tema."""
        try:
            return com_avisos(await _importar_pacote())
        except Exception as ex:
            return _erro(ex)

    @mcp.tool()
    async def diagnostico_base_ted_sp() -> str:
        """Cobertura do índice local do TED-OAB/SP, pendências, última sincronização e estado do disjuntor. Não usa rede."""
        return _diagnostico()

    _HAS_MCP = True
except Exception:  # pragma: no cover
    _HAS_MCP = False


# ───────────────────────── selftest offline ─────────────────────────

def _selftest() -> None:
    import shutil
    import tempfile

    global ARQUIVO_DB, _ARQUIVO_ESTADO, PASTA_DADOS, PASTA_RECIBOS
    fx = os.path.join(PASTA, "fixtures")
    tmp = tempfile.mkdtemp()
    ARQUIVO_DB, _ARQUIVO_ESTADO = os.path.join(tmp, "t.db"), os.path.join(tmp, "estado.json")
    PASTA_DADOS, PASTA_RECIBOS = tmp, os.path.join(tmp, "recibos")

    lista = json.load(open(os.path.join(fx, "lista_1994.json"), encoding="utf-8"))
    itens = _parse_lista(lista["msg"])
    assert len(itens) == 12 and itens[0]["guid"] == "820342800" and itens[0]["numero"] == "1.287", itens[:1]

    m94 = json.load(open(os.path.join(fx, "modal_1994.json"), encoding="utf-8"))
    p = _parse_parecer(_html_para_texto(m94["html"]))
    assert p["processo"] == "1.287" and p["votacao"] == "v.u." and "TRAMA" in p["relator"] and "BARONI" in p["presidente"], p

    m26 = json.load(open(os.path.join(fx, "modal_2026.json"), encoding="utf-8"))
    t26 = _html_para_texto(m26["html"])
    assert "schema.org" not in t26
    p = _parse_parecer(t26)
    assert p["processo"] == "25.0886.2025.014989-0" and p["data_julgamento"] == "2026-04-16" and "PICCOLO" in p["relator"], p
    assert "VILARDO" in p["revisor"] and "HABER" in p["presidente"] and p["inteiro"].startswith("RELATÓRIO"), (p["revisor"], p["inteiro"][:40])

    disc = _parse_lista_disc(open(os.path.join(fx, "disciplinar_2014_09.html"), encoding="utf-8").read())
    assert len(disc) == 3 and disc[0]["id"] == 5024 and disc[0]["data"] == "2014-09-16", disc[:1]
    pt = _parse_texto_disc(open(os.path.join(fx, "disciplinar_texto_5024.html"), "rb").read().decode("utf-8", "replace"))
    assert pt["acordao"] == "996" and pt["processo"] == "99R0000012011" and "Quinta Turma" in pt["turma"] and "Boldrini" in pt["relator"], pt

    con = _db(criar=True)
    assert _gravar_item_lista(con, itens[0], 1994) and not _gravar_item_lista(con, itens[0], 1994)
    _gravar_parecer(con, itens[0]["guid"], m94["html"])
    it26 = {"guid": "1778262039", "slug": "26-05-08-144039-", "numero": "4989-0/2026", "titulo": m26["title"]}
    _gravar_item_lista(con, it26, 2026)
    _gravar_parecer(con, it26["guid"], m26["html"])
    _gravar_disc_lista(con, disc[0])
    _gravar_disc_texto(con, 5024, open(os.path.join(fx, "disciplinar_texto_5024.html"), "rb").read().decode("utf-8", "replace"))
    con.commit()
    con.close()

    r = _buscar('inteligencia artificial E supervis*', None, None, None, None, "tudo", 1, 10)
    assert "25.0886.2025.014989-0" in r and "BASE PARCIAL" in r and "#modal=26-05-08-144039-%5b%21%5d5" in r, r
    assert "1 resultado" in _buscar('"fatos consumados" OU consumados', 1994, None, None, None, "tudo", 1, 10)
    assert "Nenhuma ementa" in _buscar("zzzinexistente", None, None, None, None, "tudo", 1, 10)
    con = _db()
    assert len(_localizar(con, "4989-0/2026", None)) == 1 and len(_localizar(con, "25.0886.2025.014989-0", None)) == 1
    assert len(_localizar(con, "Proc. 1.287", None)) == 1
    con.close()
    assert "Quinta Turma" in _buscar_disc("tráfico entorpecente", 2014, 1, 10)
    assert _traduzir_consulta('honorar* OU "quota litis" NÃO') == '"honorar"* OR "quota litis"'
    assert _normalizar_para_comparar("Sigilo  –  Profissional ,") == _normalizar_para_comparar("sigilo-profissional,")

    jc = json.load(open(os.path.join(fx, "cfoab_ementa.json"), encoding="utf-8"))
    r0 = jc["resultado"][0]
    cit = _cfoab_citacao(r0)
    assert _cfoab_total(jc) == 239 and cit.startswith("CFOAB, Primeira Câmara, REPRESENTAÇÃO N. 07.0000.2026.000055-0/PCA") and "Ementa n. 044/2026/PCA" in cit, cit
    assert "Rel. THIAGO PIRES DE MELO" in cit and "j. 22/06/2026" in cit and "DEOAB 16/07/2026, ed. 1902, p. 4" in cit, cit
    assert len(_achar_faceta(jc["grupoOrgaoJulgador"], "primeira camara")) == 1 and _achar_faceta(jc["grupoOrgaoJulgador"], "inexistente") == []
    assert any("EMENTA:" in l for l in _cfoab_format(r0, 1, True))
    leg = {"orgaoJulgador": {"nomeOrganizacao": "Conselho Federal", "nomeSetor": "Órgão Especial"},
           "descricaoEmenta": "É obrigatória a inscrição suplementar. (Proc. 227/98/OEP, Rel. Edmar Lázaro Borges (GO), Ementa 002/99/OEP, julgamento: 08.03.99, DJ 15.03.99, p. 27, S1)"}
    div = dict(r0, descricaoEmenta="Recurso n. 1/SCA. Recorrente: X. Relatora: Conselheira Federal Dione Almeida Santos (SP). EMENTA N. 1.")
    assert "Rel. Dione Almeida Santos [⚠️" in _cfoab_citacao(div) and "THIAGO PIRES DE MELO" in _cfoab_citacao(div), _cfoab_citacao(div)
    assert "⚠️" not in _cfoab_citacao(r0)
    assert "Ementa 002/99/OEP, julgamento: 08.03.99" in _cfoab_citacao(leg) and "Proc. ?" not in _cfoab_citacao(leg), _cfoab_citacao(leg)

    # ── R1: de quem é a frase. E-5.936/2022 transcreve 3 ementas de outros pareceres (com e sem
    # aspas), cita voto convergente e doutrina. Um ✅ sem estes alertas atribuiria ao parecer a frase de outro.
    pr = json.load(open(os.path.join(fx, "parecer_e5936_2022.json"), encoding="utf-8"))
    tipos = lambda tr: [a.split(":")[0].replace("⚠️ ", "") for a in _alertas_atribuicao(pr["texto"], tr, pr["processo"]) if a.startswith("⚠️")]
    t11 = _alertas_atribuicao(pr["texto"], "EMENTA 4 - INSCRIÇÃO SUPLEMENTAR - HABITUALIDADE - LIMITE DE CINCO CAUSAS", pr["processo"])
    assert any("E-4.607/2016" in a for a in t11) and not any("E-4.239/2013" in a for a in t11), t11  # o FECHO, não o 1º "Proc."
    assert "TRANSCRIÇÃO" in tipos("O legislador, ao restringir o direito do advogado de")  # transcrita SEM aspas
    assert "TRANSCRIÇÃO" in tipos("Incidentes, recursos, processos cautelares, bem assim execuções de sentenças")
    assert "VOTO DE OUTRO JULGADOR" in tipos("melhor seria ou nova redação do texto, acrescentando apenas na parte final")
    assert "DOUTRINA" in tipos("causa deve ser entendida como processo judicial efetivamente ajuizado")
    assert tipos("Causa, pois, é entendido como o processo principal") == []  # voz própria do relator: sem falso alarme
    assert tipos("resta indubitável que incidentes processuais, ações incidentais, recursos") == []
    # frase que o parecer ADOTOU na própria ementa não é transcrição, mesmo que também apareça na ementa transcrita
    assert _contem_fragmentos(_normalizar_para_comparar(_parse_parecer(pr["texto"])["ementa"]),
                              "as medidas cautelares, ainda que requeridas em caráter antecedente, não se somarão ao pedido principal")
    # ── recibo: arquivo 0600, hash do texto, blocos alheios preenchidos (as 3 ementas transcritas e o voto convergente)
    import stat
    cam = _gravar_recibo(pr["guid"], "TED-OAB/SP", pr["processo"], pr["texto"], {"numero_ementa": pr["numero"]}, pr["processo"])
    rec = json.load(open(cam, encoding="utf-8"))
    assert stat.S_IMODE(os.stat(cam).st_mode) == 0o600 and stat.S_IMODE(os.stat(PASTA_RECIBOS).st_mode) == 0o700
    assert rec["sha256"] == hashlib.sha256(pr["texto"].encode("utf-8")).hexdigest() and rec["tribunal"] == "TED-OAB/SP"
    assert sum("E-4.607/2016" in t or "E-4.259/2013" in t or "E-5.556/2021" in t for t in rec["trechos_transcritos"]) >= 3
    assert "convincentes argumentos" in rec["trecho_divergente"], rec["trecho_divergente"][:80]
    assert not any("Causa, pois, é entendido como o processo principal" in t for t in rec["trechos_transcritos"])  # voz própria fica fora

    # ── pacote: ida e volta; nunca leva a tabela `meta` (histórico de temas pesquisados)
    con = _db(); _meta_set(con, "tema:segredo do usuario", "x"); con.commit(); con.close()
    exp = _exportar_pacote(os.path.join(tmp, "pac"))
    bruto = gzip.open(exp["arquivo"], "rt", encoding="utf-8").read()
    assert "segredo do usuario" not in bruto and exp["com_texto"] >= 2 and len(exp["sha256"]) == 64
    ARQ_ORIG = ARQUIVO_DB
    ARQUIVO_DB = os.path.join(tmp, "importada.db")
    rel = _importar_linhas(bruto.splitlines())
    assert rel["textos"] == exp["com_texto"] and _importar_linhas(bruto.splitlines())["textos"] == 0  # não rebaixa, não duplica
    con = _db(); assert con.execute("SELECT COUNT(*) FROM citacoes").fetchone()[0] > 0; con.close()  # grafo refeito na importação
    ARQUIVO_DB = ARQ_ORIG

    # ── grafo: o extrator sobre casos reais medidos
    disp = lambda tx: {r for tp, r in _extrair_citacoes(tx) if tp == "dispositivo"}
    assert disp("artigos 7º, II e XIX, 34, VII, e 36 do Estatuto") == {"art. 7 EAOAB", "art. 34 EAOAB", "art. 36 EAOAB"}
    assert disp("Exegese dos artigos 10, § 2º e 15 § 5º do Estatuto, artigos 26,34 § 1º, 134 § 4º do Regulamento Geral") == \
        {"art. 10 EAOAB", "art. 15 EAOAB", "art. 26 RG", "art. 34 RG", "art. 134 RG"}
    assert {r for tp, r in _extrair_citacoes("E-4.607/2016, E. 6.093/2023 e E-2.362/01")} == {"E-4.607/2016", "E-6.093/2023", "E-2.362/2001"}
    assert ("ted", "E-5.936/2022") not in _extrair_citacoes(pr["texto"], pr["processo"])  # não cita a si mesmo
    assert _canon_ref("art. 10 do Estatuto") == _canon_ref("art. 10 EAOAB") == "art. 10 EAOAB" and _canon_ref("xyz") is None
    # ── consulta: linguagem natural por OU, aspas obrigatórias, operador em maiúsculas = modo avançado
    m, partes = _montar_match('advogado de "outra seccional" sem inscrição')
    assert len(partes) == 2 and '"outra seccional"' in m and '"sem"' in m and '"de"' not in m, (m, partes)
    assert _montar_match("honorários E sucumbência")[1][0][0].startswith("consulta avançada")
    assert "inscricoes" in variantes_numero("inscricao") and variantes_numero("honorarios") == ["honorarios"]
    # ── aviso de versão: só versão estritamente maior, formato estranho é False
    assert versao_mais_nova("1.4.0", "v1.4.1") and not versao_mais_nova("1.4.0", "1.4.0") and not versao_mais_nova("1.4.0", "beta")

    # ── disjuntor: espaçamento, isolamento por portal, pausa que dobra, escada, fail-closed, tetos, modo carga
    global _MODO_CARGA
    t0 = time.time()
    assert _reservar(agora=t0) == 0.0 and abs(_reservar(agora=t0 + 0.5) - (ESCADA["oabsp"][0][0] - 0.5)) < 1e-6
    assert _reservar(agora=t0, host="cfoab") == 0.0  # portal separado não herda a fila do outro
    assert _registrar_bloqueio("teste", agora=t0) == _COOLDOWN_INICIAL_S["oabsp"]
    try:
        _reservar(agora=t0 + 1)
        raise AssertionError("pausa não barrou")
    except PortalRecusou as ex:
        assert "NÃO é 'não localizado'" in str(ex) and "Não contornar" in str(ex)
    assert _ler_estado()["oabsp"]["nivel"] == 1  # recusa sobe um degrau na escada
    assert _reservar(agora=t0 + 1, host="cfoab") >= 0.0  # pausa da OAB/SP não trava o Conselho Federal
    assert _registrar_bloqueio("teste2", agora=t0 + 2) == 2 * _COOLDOWN_INICIAL_S["oabsp"]  # recusa seguida dobra
    # fail-closed: estado ilegível pausa tudo em vez de liberar
    open(_ARQUIVO_ESTADO, "w").write("{isto não é json")
    _PAUSA_MEMORIA.clear()
    try:
        _reservar(host="cfoab")
        raise AssertionError("estado ilegível liberou requisição")
    except PortalRecusou as ex:
        assert "fail-closed" in str(ex)
    # teto por janela (e o modo carga, que só o terminal liga, ignora os tetos mas não a pausa)
    os.remove(_ARQUIVO_ESTADO)
    agora = time.time()
    _gravar_estado({"cfoab": {"requisicoes": [agora - i for i in range(ESCADA["cfoab"][0][1])], "nivel": 0}})
    try:
        _reservar(host="cfoab")
        raise AssertionError("teto por janela não barrou")
    except PortalRecusou as ex:
        assert "teto de" in str(ex)
    _MODO_CARGA = True
    assert _reservar(host="cfoab") >= 0.0
    _MODO_CARGA = False
    # formato da v1.3 (oabsp na raiz, cooldown_ate) é lido sem perder a pausa
    _gravar_estado({"cooldown_ate": agora + 600, "bloqueios_seguidos": 1, "hosts": {"cfoab": {}}})
    assert _ler_estado()["oabsp"]["pausa_ate"] == agora + 600 and _ler_estado()["cfoab"]["pausa_ate"] == 0.0
    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest offline OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--sincronizar" in sys.argv:
        _MODO_CARGA = True  # só o terminal: tetos por janela/dia não se aplicam; espaçamento, escada e pausa, sim
        anos = [int(a) for a in sys.argv[sys.argv.index("--anos") + 1].split(",")] if "--anos" in sys.argv else None
        teto = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 0
        print(f"Sincronizando TED-OAB/SP (anos: {anos or 'todos, do mais novo ao mais antigo'}; teto: {teto or 'sem'}) em {ARQUIVO_DB}", flush=True)
        try:
            rel = asyncio.run(_sincronizar(anos, teto, "--sem-disciplinar" not in sys.argv, progresso=lambda m: print(m, flush=True)))
        except (PortalRecusou, ValueError) as ex:
            print(_diagnostico())
            sys.exit(str(ex))
        print(f"OK: {rel}")
        print(_diagnostico())
    elif "--exportar-pacote" in sys.argv:
        destino = sys.argv[sys.argv.index("--exportar-pacote") + 1] if len(sys.argv) > sys.argv.index("--exportar-pacote") + 1 else "pacote"
        print(json.dumps(_exportar_pacote(destino), ensure_ascii=False, indent=1))
    elif "--diagnostico" in sys.argv:
        print(_diagnostico())
    elif _HAS_MCP:
        mcp.run()
    else:
        sys.exit("registro MCP falhou — instale na venv: pip install 'mcp[cli]>=1.4.0,<2' httpx truststore")
