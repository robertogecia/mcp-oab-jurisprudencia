# Integração opcional: agente de pesquisa com vários tribunais

Para quem mantém, no Claude Code, um subagente de pesquisa jurídica com tabela de roteamento por tribunal.
Nada aqui é necessário para o servidor funcionar. O prefixo das ferramentas é o nome que você deu ao servidor em
`claude mcp add` (aqui, `oab_jurisprudencia`).

**Ferramentas a liberar no frontmatter do agente** (deixe `sincronizar_base_ted_sp` de fora: quem mexe na base é a conversa principal)

```
mcp__oab_jurisprudencia__buscar_jurisprudencia_cfoab, mcp__oab_jurisprudencia__obter_ementa_cfoab,
mcp__oab_jurisprudencia__verificar_citacao_cfoab, mcp__oab_jurisprudencia__buscar_normas_cfoab,
mcp__oab_jurisprudencia__buscar_ementas_ted_sp, mcp__oab_jurisprudencia__mapa_de_citacoes_ted_sp,
mcp__oab_jurisprudencia__obter_parecer_ted_sp, mcp__oab_jurisprudencia__verificar_citacao_ted_sp,
mcp__oab_jurisprudencia__buscar_ementas_disciplinares_ted_sp, mcp__oab_jurisprudencia__obter_acordao_disciplinar_ted_sp,
mcp__oab_jurisprudencia__pesquisar_tema_no_portal_ted_sp, mcp__oab_jurisprudencia__diagnostico_base_ted_sp
```

**Linhas de roteamento**

| Órgão | Chave | Motor | Teto de verificação |
|---|---|---|---|
| Conselho Federal da OAB | matéria de regulação da advocacia (inscrição, incompatibilidade, sociedade, disciplina em recurso, eleições e contas da OAB); nº no formato `NN.NNNN.AAAA.NNNNNN-D` (não é CNJ) | `*_cfoab`, ao vivo — **pesquisar antes do TED** | `só ementa/índice` (a API não publica o voto) |
| TED-OAB/SP | matéria deontológica (publicidade, honorários, sigilo, captação, conflito de interesses); nº `E-4.607/2016` ou `25.0886.AAAA.NNNNNN-D` | `*_ted_sp`, índice local | `inteiro teor lido` após `obter_parecer_ted_sp` |

**Ficha de precedente**: `tribunal` = `CFOAB`, `OAB/SP` (Câmara Recursal) ou `TED-OAB/SP`; `id_documento` = o `id` da API
(24 hexadecimais) no Conselho Federal, o `guid` (9 ou 10 dígitos) no TED — número de processo não é id; `link` do TED = o que
a ferramenta devolveu; no Conselho Federal, vazio (o portal não tem endereço por ementa — nunca fabricar). Os recibos ficam
em `dados/recibos/<id>.json`, no formato dos outros servidores: um verificador de ficha pode conferir contra eles.

**Regras que o agente precisa conhecer**: começar pelo diagnóstico; zero resultado nunca é "não localizado"; decisão da OAB
não é jurisprudência judicial; aspas só depois de `verificar_citacao_*`; alerta de TRANSCRIÇÃO significa que o trecho é de
outro parecer — o alerta diz qual.

No servidor, declare outras bases para que as saídas apontem para elas: `TEDSP_COMPLEMENTOS="Nome da sua base"`.
