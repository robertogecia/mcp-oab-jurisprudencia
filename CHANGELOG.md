# Histórico

- **v1.4.0 (23/09/2026) — o que o servidor do TJSE aprendeu, trazido para a OAB, e publicação.** Lido de verdade no código do
  TJSE, não pelo nome das funções; o que não se aplica ficou de fora e está dito abaixo.
  - **De quem é a frase.** `verificar_citacao_ted_sp` lê o parágrafo que contém o trecho e avisa `TRANSCRIÇÃO` (ementa de outro
    parecer reproduzida no texto — com aspas ou sem elas), `ENTRE ASPAS`, `VOTO DE OUTRO JULGADOR`, `DOUTRINA`, `CITAÇÃO
    ANUNCIADA` e `NEGAÇÃO LOGO ANTES`. Redesenhado sobre a estrutura MEDIDA do parecer do TED (34 parágrafos de um parecer
    real): a origem da transcrição sai do **fecho** do bloco ("Proc. X … Presidente"), porque o primeiro "Proc." de uma ementa
    transcrita costuma ser um precedente que ela cita — a regra ingênua apontaria o parecer errado. Sete de sete casos reais,
    sem falso alarme na voz do relator; congelados como regressão `R1`. No Conselho Federal: aspas e negação.
  - **Recibo de custódia** no formato dos outros servidores (TJRO, STJ, TJSE, TCE-RO, TRF1): texto que a fonte entregou, sha256,
    e os blocos que não são palavra do órgão em `trechos_transcritos` / `trecho_divergente`. Permissão 0600.
  - **Grafo de citações** extraído do texto (262 precedentes e 126 dispositivos distintos em 74 pareceres, medido antes de
    escrever o extrator): o Estatuto chamado de quatro jeitos, listas de artigos, Estatuto e Regulamento Geral na mesma frase,
    a variante "E. 6.093/2023". Filtro `cita=` na busca e ferramenta nova `mapa_de_citacoes_ted_sp`.
  - **Busca em português corrente** (do harness do TJSE): palavras soltas por OU com ranking, "aspas" obrigatórias, `grupos`,
    singular/plural automático, radical$, palavras vazias que preservam "não", "sem" e "menor". Operador em MAIÚSCULAS liga o
    modo avançado. **Busca que zera diz qual parte zerou.** **Panorama** das ementas que casam (período, dispositivos,
    precedentes, relatores). **`triagem=true`** (30 no TED, 20 no Conselho Federal): lista curta para o modelo reordenar — no
    TJSE, 40,5 % → 59,5 % de precisão nos 10 primeiros.
  - **Pacote pronto** (`importar_pacote_ted_sp`, `--exportar-pacote`): hash conferido contra `SHA256SUMS.txt`, nunca rebaixa, e
    nunca leva o histórico de temas pesquisados, recibos ou a base disciplinar.
  - **Disjuntor** do TJSE: **fail-closed** (estado ilegível ou sem trava não libera requisição), tetos por 10 min e por dia por
    portal, **escada de ritmo** que aperta após recusa e afrouxa após 100 consultas limpas, pausa em memória se o disco falhar.
    Estado antigo (v1.3) é lido sem perder pausa.
  - **User-Agent honesto** por padrão (`mcp-oab-jurisprudencia/<versão>`), medido como aceito pelos três endereços usados;
    `TEDSP_USER_AGENT` troca. Modo híbrido `TEDSP_COMPLEMENTOS`. Dados em `TEDSP_DIR_DADOS`, pastas 0700.
  - **Crédito de autoria**, **aviso de versão nova** (thread de fundo, 2 s, endereço fixo) e **link de relato de erro** que só
    leva dado técnico.
  - **Guarda o HTML bruto** do parecer e **`PARSER_VERSAO`**: parser mudou, o índice se refaz sem rede.
  - **Fixtures anonimizados** para o repositório público (`scripts/anonimizar_fixtures.py`): o fixture do Conselho Federal
    trazia nome e OAB de advogados-parte; o do modal trazia 450 KB de dump interno do CMS; o disciplinar, o número do processo.
  - CI (GitHub Actions) roda o selftest offline em Python 3.10, 3.12 e 3.13.
  - **Não portado, de propósito:** extensão `.mcpb` de um clique (exige porte para Node com teste de paridade sobre o corpus —
    próximo passo); vocabulário distintivo do panorama (fts5vocab); sinônimos curados (precisam de medição no corpus do TED).
- **v1.3.0 (20/09/2026)** — `[...]` aceito na conferência de citação (fragmentos na ordem, no mesmo campo); relator ad hoc;
  sufixo do órgão removido do nº do processo do Conselho Federal; normas enxutas por padrão; alerta de data inconsistente na
  própria fonte; ementas só-título deixam de ser invisíveis na busca.
- **v1.2.x (19–20/09/2026)** — Conselho Federal ao vivo (API do portal); 503 avulso do CMS da OAB/SP ganha uma nova tentativa;
  parser de fecho no formato antigo; busca dirigida por tema.
- **v1.0.0 (19/09/2026)** — ementário do TED-OAB/SP em índice local; disjuntor com pausa de horas.
