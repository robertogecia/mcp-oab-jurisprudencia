# Jurisprudência da OAB no Claude — Conselho Federal e TED-OAB/SP

[![tests](https://github.com/robertogecia/mcp-oab-jurisprudencia/actions/workflows/test.yml/badge.svg)](https://github.com/robertogecia/mcp-oab-jurisprudencia/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Servidor MCP que dá ao **Claude** a capacidade de **pesquisar as decisões do sistema OAB** — o ementário do **Conselho
Federal** (Órgão Especial, Pleno, Câmaras e Turmas; inscrição, incompatibilidade, disciplina em grau de recurso, súmulas e
provimentos) e os pareceres do **Tribunal de Ética e Disciplina da OAB/SP** (publicidade, honorários, sigilo, captação,
conflito de interesses, inscrição suplementar…) — e, antes de você citar em peça, **conferir se a frase está literalmente no
texto e de quem ela é**: do próprio parecer, de outro parecer transcrito, de um voto divergente ou de um doutrinador.
Sem login, sem captcha e sem programar.

> **O que isto NÃO é:** jurisprudência judicial. São decisões administrativas de órgão de classe. O parecer do TED responde
> consulta em tese e só **persuade** fora de São Paulo; decisão do Conselho Federal orienta o sistema OAB, mas não vincula o
> Judiciário. Toda resposta do servidor traz esse aviso de natureza.

## Instalar (10 minutos)

O servidor é em Python. Você vai **copiar e colar cinco comandos** no Terminal e depois conversar com o Claude em português.

**Nunca usou o Terminal?** O **[guia passo a passo (INSTALAR.md)](INSTALAR.md)** explica cada clique, do zero.

```bash
git clone https://github.com/robertogecia/mcp-oab-jurisprudencia.git ~/MCP/oab-jurisprudencia
cd ~/MCP/oab-jurisprudencia
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python servidor_tedsp.py --selftest
claude mcp add oab_jurisprudencia -- ~/MCP/oab-jurisprudencia/.venv/bin/python ~/MCP/oab-jurisprudencia/servidor_tedsp.py
```

A quarta linha deve terminar em `selftest offline OK`. A quinta liga ao **Claude Code**; para o **Claude Desktop**, veja o
passo 6 do [guia](INSTALAR.md). Depois, **abra uma conversa nova** e peça:

1. ***"Importe o pacote do TED-SP."*** — traz o ementário pronto do GitHub, **sem tocar o portal da OAB/SP**.
2. ***"Rode o diagnóstico da base da OAB."*** — mostra quantos pareceres estão no índice.
3. Pergunte o que quiser: *"O que o TED-SP entende sobre advogado anunciar no Instagram?"*

O Conselho Federal **não precisa de pacote**: é consultado ao vivo, direto na API do portal.

**Se não funcionar:**

| Sintoma | O que é |
|---|---|
| O Claude diz que não tem a ferramenta | Abra uma **conversa nova** — conversa aberta antes de ligar o servidor não o enxerga. No Desktop, feche e abra o app. |
| "índice local do TED-OAB/SP ainda não existe" | Falta o passo 1: *"Importe o pacote do TED-SP."* |
| "este release ainda NÃO traz o pacote pronto" | O pacote é publicado à parte. Enquanto isso, use a **busca dirigida**: *"Pesquise o tema publicidade no portal do TED-SP."* |
| A busca deu zero resultado | **Nunca é "o TED nunca decidiu"** — é "não há no que foi baixado". O cabeçalho diz a cobertura; se disser `BASE PARCIAL`, leve a sério. |
| `[PESQUISA NÃO REALIZADA] … em pausa` | O portal recusou antes e o disjuntor pausou as consultas por algumas horas. **Não contorne** por navegador ou outro programa. |
| `selftest` falhou | Não use em caso real. Abra uma [issue](https://github.com/robertogecia/mcp-oab-jurisprudencia/issues) com a saída (sem dado de cliente). |

### Quanto espaço em disco

| O que | Tamanho |
|---|---|
| O programa e suas bibliotecas (`.venv`) | ~65 MB, medido |
| Base de uso real: 74 pareceres em texto integral + 1.547 títulos catalogados | 5 MB, medido |
| O ementário deontológico inteiro (~5.400 pareceres, 1994–hoje) | até ~400 MB, **projeção** a partir da medida acima |
| **Folga recomendada** | **1 GB livre** |

## Por que ele é assim

| Fonte oficial | Como é lida | Por quê |
|---|---|---|
| **Conselho Federal** — `jurisprudencia.oab.org.br` | ao vivo, pela mesma API que o site usa | a API já faz busca textual; guardar cópia não acrescenta nada |
| **TED-OAB/SP, 1ª Turma (deontológica)** — ementário em `oabsp.org.br` | **índice local** (SQLite FTS5) do texto integral: ementa, relatório e parecer | a busca do site **só casa o título** da ementa; aqui a busca varre o parecer inteiro |
| **TED-OAB/SP, Turmas Disciplinares** — base antiga do portal | índice local | o portal só publica 2010–2014, e o processo disciplinar é sigiloso (art. 72, §2º, do EAOAB) |

Nada disso tem captcha ou login, e o servidor **se identifica como o que é** (User-Agent próprio, aceito pelos três
endereços — medido). O portal da OAB/SP é um CMS comum que solta erro 503 quando recebe consulta demais; por isso o ementário
do TED é montado **uma vez**, pelo autor, e distribuído como pacote pronto — ninguém mais precisa martelar o portal.

## Ferramentas

| Ferramenta | O que faz | Rede |
|---|---|---|
| `buscar_jurisprudencia_cfoab` | ementário do Conselho Federal: filtros por órgão, relator, organização, tipo; `triagem` | ao vivo |
| `obter_ementa_cfoab` / `verificar_citacao_cfoab` | ementa e acórdão integrais; conferência literal | ao vivo |
| `buscar_normas_cfoab` | súmulas, provimentos, resoluções, instruções normativas, portarias | ao vivo |
| `buscar_ementas_ted_sp` | pareceres do TED-SP: **pergunta em português corrente**, `grupos` de sinônimos, **`cita`** (quem aplica tal precedente ou artigo), panorama, `triagem` | não |
| `mapa_de_citacoes_ted_sp` | grafo: precedentes do TED e artigos mais citados, e quem cita cada um | não |
| `obter_parecer_ted_sp` | ementa + relatório + parecer, com fecho literal, link estável e **recibo** | só na 1ª vez |
| `verificar_citacao_ted_sp` | confere a frase **e diz de quem ela é** (transcrição de outro parecer, voto de outro julgador, doutrina, entre aspas, negação) | reabre o portal |
| `importar_pacote_ted_sp` | monta o índice do pacote do release (hash conferido) — zero requisição à OAB/SP | GitHub |
| `pesquisar_tema_no_portal_ted_sp` | busca dirigida: traz só os pareceres de um tema | sim, pouco |
| `buscar_ementas_disciplinares_ted_sp` / `obter_acordao_disciplinar_ted_sp` | acórdãos disciplinares 2010–2014 | índice / 1ª vez |
| `sincronizar_base_ted_sp` / `diagnostico_base_ted_sp` | atualização incremental / cobertura, disjuntor, pendências | sim / não |

## Três coisas que a busca por palavra não faz sozinha

**De quem é a frase.** O parecer do TED transcreve ementas inteiras de outros pareceres — com aspas ou sem elas —, cita voto
convergente ou vencido e cita doutrina. Um "✅ confere" puro atribuiria ao parecer a frase de outro. A conferência lê o
parágrafo que contém o trecho e avisa: `TRANSCRIÇÃO` (com o número do parecer de origem, lido do **fecho** do bloco — não do
primeiro número que aparece nele, que costuma ser um precedente citado *dentro* da ementa transcrita), `ENTRE ASPAS`,
`VOTO DE OUTRO JULGADOR`, `DOUTRINA`, `CITAÇÃO ANUNCIADA` e `NEGAÇÃO LOGO ANTES`. Medido sobre um parecer real com três
ementas transcritas, voto convergente e doutrina: sete de sete, sem falso alarme na voz do relator.

**O grafo de citações.** Os pareceres encadeiam precedentes ("Precedentes: E-4.239/2013, E-4.607/2016…") e dispositivos.
O extrator normaliza as quatro formas de chamar o Estatuto (EAOAB, EOAB, "Estatuto", "Estatuto da Advocacia"), listas de
artigos ("artigos 7º, II e XIX, 34 e 36 do Estatuto") e separa Estatuto de Regulamento Geral na mesma frase. Com ele se
pergunta *"quais pareceres aplicam o E-4.607/2016"* em vez de adivinhar as palavras que eles usaram — e o grafo enxerga
precedentes **anteriores** ao que foi baixado.

**Pergunta em português corrente.** Palavras soltas combinam por OU e o ranking ordena; o que estiver "entre aspas" é
obrigatório; `grupos` exige um termo de cada grupo; singular e plural entram sozinhos. Busca que zera diz **qual** parte
zerou e quantos voltariam sem ela. Operador em MAIÚSCULAS (`E`, `OU`, `NÃO`) liga o modo avançado, que obedece à letra.
(Desenho portado do [servidor do TJSE](https://github.com/robertogecia/mcp-tjse-jurisprudencia), onde foi medido com gold set.)

Fluxo: `diagnostico` → `buscar` (Conselho Federal primeiro: é a instância que uniformiza) → `obter` → `verificar` antes de
qualquer aspas.

## Recibo de custódia

`obter_*` e `verificar_*` gravam um recibo por documento em `dados/recibos/<id>.json`: o texto que a fonte entregou, hash
sha256 e, em `trechos_transcritos` e `trecho_divergente`, o que **não** é palavra do órgão. É o mesmo formato dos servidores
do TJRO, STJ, TJSE, TCE-RO e TRF1: um verificador de ficha de citação confere a peça contra o arquivo, não contra o que o
modelo diz ter lido. Permissão `0600`; **não publique recibos** — podem nomear quem consultou ou foi representado.

## Limites, ditos sem rodeio

- **TED-SP: só a 1ª Turma (deontológica)** no índice principal, e só o que foi baixado. As Turmas Disciplinares só existem
  no portal de 2010 a 2014.
- **Conselho Federal: ementa e acórdão**, nunca o voto — a API não o publica. Ementa antiga (anos 1990–2000) vem sem número
  estruturado: a referência sai do parêntese final do próprio texto, literal.
- **Relator, revisor e data do TED** saem de regra sobre o fecho do parecer. Com voto vencido, a citação manda ler o fecho —
  o parecer vencedor pode ser do revisor. No Conselho Federal, quando o campo estruturado diverge do texto publicado, prevalece
  o texto, e a divergência é dita (inclusive "Relator ad hoc").
- **Título de ementário não é ementa.** A busca lista à parte, marcadas `[SÓ TÍTULO]`, as ementas cujo título casa mas cujo
  texto ainda não foi baixado — não cite nenhuma delas como entendimento do TED.
- **Superação não é marcada pela fonte.** O TED muda de orientação sem anotar a ementa antiga (ex.: publicidade antes e
  depois do Provimento 205/2021 do Conselho Federal). Prefira a mais recente e diga a data.
- Os alertas de atribuição são heurísticos: pegam o padrão comum, não tudo (doutrina transcrita sem aspas nem atribuição passa).
  Dizem "confira quem fala", não substituem ler o parecer.

## Ritmo e boa vizinhança

| Portal | Espaçamento | Teto em 10 min | Teto em 24 h | Recusa (403, 429, 503 repetido) |
|---|---|---|---|---|
| OAB/SP (CMS e base antiga) | 2 s | 30 | 300 | pausa de 2 h, que dobra a cada recusa seguida, até 24 h |
| Conselho Federal (API) | 1,5 s | 60 | 600 | pausa de 1 h, que dobra, até 24 h |

Depois de uma recusa o ritmo **aperta sozinho** (escada de quatro degraus) e só afrouxa após 100 consultas limpas. O que foi
medido no CMS da OAB/SP: 503 avulso depois de ~35 a ~270 requisições a 2 s cada; um 503 isolado, sem `Retry-After`, ganha **uma**
nova tentativa após 45 s, porque o mesmo item costuma baixar logo depois. Timeout e queda de conexão não armam o disjuntor.
O estado fica em disco, compartilhado entre processos (`flock`); **estado ilegível ou sem trava não libera requisição**
(fail-closed). Os tetos são de proteção, não limites medidos — não os suba: o servidor é de todos.

A carga completa pelo Terminal (`--sincronizar`), feita uma vez para montar o pacote, respeita espaçamento, escada e pausa,
mas não os tetos de janela e dia (senão levaria semanas). Nenhuma ferramenta MCP liga esse modo.

## Configuração

| Variável | Para quê |
|---|---|
| `TEDSP_DIR_DADOS` | onde ficam índice, recibos e disjuntor (padrão: `dados/` na pasta do projeto). **Evite pasta sincronizada em nuvem** (OneDrive, iCloud, Dropbox): SQLite e sincronizador brigam pelo arquivo. |
| `TEDSP_DIR_RECIBOS` | só os recibos, em outro lugar |
| `TEDSP_COMPLEMENTOS` | outras bases que você assina, separadas por `;` — as respostas passam a apontar para elas no que este servidor não cobre |
| `TEDSP_USER_AGENT` | troca o User-Agent (ex.: rede corporativa que só deixa passar navegador) — por conta e risco de quem troca |
| `TEDSP_MCP_SEM_AVISO_ATUALIZACAO=1` | desliga a consulta de versão nova |

**Aviso de atualização.** Na subida, o servidor consulta uma vez a página de versões deste repositório, em segundo plano, com
2 s de limite; se houver versão mais nova, a primeira resposta avisa. Nada da sua pesquisa ou do seu caso sai daqui.

## Desenvolvimento

- `python3 servidor_tedsp.py --selftest` roda offline sobre fixtures reais **anonimizados** (`scripts/anonimizar_fixtures.py`:
  nome e OAB de advogado-parte viram iniciais; número de processo disciplinar vira sintético; o dump interno do CMS sai). O
  bloco `R1` é regressão de uso real. Roda também no CI a cada push.
- Parser ou extrator mudou? **Suba `PARSER_VERSAO`**: o índice se refaz sozinho a partir do texto guardado, sem rede.
- `python3 servidor_tedsp.py --sincronizar` monta a base inteira (horas); `--exportar-pacote DIR` gera o pacote e o
  `SHA256SUMS.txt` para o release. O pacote leva só o ementário deontológico — **nunca** o histórico de temas pesquisados,
  recibos ou a base disciplinar.

Integrações opcionais para quem usa Claude Code (skill autônoma e trecho de roteamento para agente de pesquisa):
[`integracoes/`](integracoes/). Histórico: [`CHANGELOG.md`](CHANGELOG.md).

## Autor

**Roberto Grécia Bessa** — OAB/RO 7865-A
Instagram: [@robertogrecia](https://instagram.com/robertogrecia)

Projeto não-oficial: os dados vêm de publicação oficial e aberta do Conselho Federal e da OAB/SP, mas o servidor não é da OAB.
Toda saída é rascunho — quem assina a peça confere.

## Licença

MIT.
