# Histórico

- **Extensão v1.8.1 (07/10/2026).** Aviso de versão nova com link DIRETO do arquivo .mcpb e a instrução "dê dois cliques no arquivo baixado e reinicie o Claude Desktop" (antes, só o link da página de versões; o texto em itálico terminava colado ao link e podia quebrá-lo). O .mcpb fica no repositório privado da extensão (o link abre para quem está logado no GitHub com acesso). Testes da extensão deixam de escrever a versão por extenso. NEGAÇÃO, segunda validação cega (120 trechos de documentos inéditos, 102/120 concordes, 18 adjudicados): o alerta forte acertou 63% (na primeira, 80%) e 13% das negações reais ficaram sem aviso (na primeira, 28%) — a variação entre amostras é grande, por isso vão os dois números. Testado e REJEITADO baixar o alcance mínimo de 3 para 1 palavra: os casos novos são quase todos fragmentos de ementa ("NÃO [COMPROVADA. …]") e a perda de precisão não compensa (sem aviso 13% → 10%, forte 63% → 59%).
- **v1.8.1 (07/10/2026) — correção.** o servidor Python não abria no Python 3.10 e 3.11 (SyntaxError: barra invertida dentro da expressão de uma f-string, no texto do alerta de POSIÇÃO — só o Python 3.12+ aceita). Afetava as versões publicadas desde 06/10/2026; o CI do GitHub acusava. A extensão .mcpb (Node) não tinha o defeito.
- **v1.8.0 (07/10/2026) — POSIÇÃO NO JULGADO refeita para pareceres com mais de um voto, e negação em dois níveis.** Títulos com o nome do julgador passam a ser reconhecidos ("VOTO VENCIDO DO RELATOR DR. …", "VOTO-VISTA VENCEDOR DO JULGADOR …", "VOTO CONVERGENTE AO VENCEDOR …", "DECLARAÇÃO DE VOTO DIVERGENTE DO JULGADOR …", "VOTO PARCIALMENTE DIVERGENTE …"), e as seções RELATÓRIO/PARECER que vêm depois deles pertencem a esse voto — antes, o parecer do relator vencido saía como "voto condutor" e o texto do convergente ficava sem rótulo. Também: "Relatório:"/"Parecer:" em caixa baixa, "PARECER/VOTO", "PARECER – O Provimento…", linha do julgamento sem "v.u." ("…em 16/10/2008, …por votação unânime…"), "É o relatório"/"Recebo a consulta" dentro de um RELATÓRIO sem outro título, e "parecer do Rel. X e ementa do Rev. Y" deixa de inverter quem venceu (o parecer do relator venceu; o revisor só redigiu a ementa). Rótulos dizem que voto é (VOTO VENCIDO, CONVERGENTE, DIVERGENTE, VOTO-VISTA, declaração de voto). Medido às cegas: **98% numa validação final de 46 trechos de 150 pareceres inéditos** (concordância 45/47) (pareceres que nunca entraram em rodada nenhuma; rodadas de ajuste intermediárias: 86%, 87% e 86%/88%). NEGAÇÃO em dois níveis. Continua "NEGAÇÃO:" quando a negação está colada ao trecho (até uma palavra antes) ou é existencial ("não há/houve/existe …", até cinco palavras). O resto que a regra anterior pegava sai como "NEGAÇÃO (distante)?", dizendo que em geral ela fecha a própria oração e não inverte o recorte. Gabarito cego e duplo em 120 trechos NOVOS de cinco tribunais (TJRO, TJSE, STJ, TCE-RO e TED-OAB; concordância 108/120, 12 adjudicados pela definição escrita), ponderado pela população: o alerta forte acerta 80% (falso alarme 6%); a regra anterior, sozinha, acertava 50% (falso alarme 33%) nesta amostra. Somados, os dois níveis avisam nos mesmos 72% das negações reais; o forte sozinho pega 53%. Paridade Python×Node total.
- **v1.7.0 (06/10/2026) — POSIÇÃO NO JULGADO mais precisa no TED-SP.** Linha do julgamento também com vírgula ("Proc. E-1.881/99, v.u."); título na mesma linha do texto ("RELATÓRIO – Informa a consulente…"), sem confundir com verbete em caixa alta; "CONSULTA E RELATÓRIO" que segue direto no parecer corta em "é o relatório"; com "VOTO VENCIDO" explícito, o texto principal é o vencedor; "Por tais razões, conheço da consulta" no começo é admissibilidade, não dispositivo (fórmula só no último terço). Medido às cegas numa amostra NOVA de 39 trechos: **85%**, contra 76% na validação anterior. Depois da medida entrou "VOTO CONVERGENTE" como voto de outro julgador (era o erro mais comum que sobrou). Paridade Python×Node total.
- **v1.6.0 (06/10/2026) — POSIÇÃO NO JULGADO no TED-SP.** `verificar_citacao_ted_sp` diz onde a frase está no parecer: ementa, linha do julgamento ("Proc. … – v.u., parecer e ementa do Rel. …"), relatório/consulta, parecer ou voto do relator (a CONCLUSÃO é o dispositivo), voto divergente ou declaração de voto; quando o revisor ou o divergente venceu, o voto dele é o condutor. Medido às cegas: **76% numa validação de 43 trechos novos** (82% na amostra de ajuste). Pareceres sem títulos e transcrições de outros pareceres ainda confundem. NEGAÇÃO mais estreita: só avisa com a negação até 6 palavras antes do trecho; não avisa quando ela nega um particípio ("não utilizado pelo…") ou recusa uma alternativa ("…, e não sobre…"); "não é outro o entendimento", "não se desconhece" e "não se pode deixar de" afirmam. Gabarito cego e duplo: ajuste em 617 trechos já rotulados (TJSE, STJ, TRT14, OAB, TCE-RO), validação em 120 trechos NOVOS de cinco tribunais (concordância 114/120, 6 adjudicados): precisão 38% → 44%, falso alarme 42% → 31%, cobertura 100% → 98%. Continua o alerta mais fraco do bloco: é aviso para ler a frase, não veredito. OBITER DICTUM? reconhece também "registre-se, por oportuno", "a título de registro" e "apenas para registro" (6 de 6 obiter às cegas). Outras marcas testadas ficaram de fora por imprecisas: "de passagem" 67%, "por cautela" 25%, "ainda que se entenda/admita" 30%. Paridade Python×Node total.
- **v1.5.0 (06/10/2026) — negação por alcance e OBITER DICTUM?, medidos às cegas no próprio TED.** `verificar_citacao_ted_sp`
  passa a usar, dentro do parágrafo, a regra de negação do TJRO (operador sem quebra de oração até o trecho, 3+ palavras dele;
  "não há dúvida", "não obstante" e "ainda que assim não fosse" não negam) e ganha o alerta OBITER DICTUM? ("ainda que assim não
  fosse", "a título de argumentação"). TRANSCRIÇÃO, ENTRE ASPAS, VOTO DE OUTRO JULGADOR e DOUTRINA continuam os do TED.
  Gabarito cego e duplo sobre os pareceres do índice local (130 trechos, kappa 0,87-0,88): **OBITER 92% de precisão** (cobertura
  baixa por desenho); **NEGAÇÃO 66% de precisão e 65% de cobertura**, contra 80% e 12% da regra antiga (que olhava só 40
  caracteres): avisa um pouco mais à toa e passa a pegar cinco vezes mais recortes que invertem o parecer. Node (`oab-jurisprudencia-mcpb`)
  em paridade.
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
