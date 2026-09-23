---
name: jurisprudencia-oab
description: Pesquisa as decisões do sistema OAB pelo servidor MCP `oab_jurisprudencia` — ementário do Conselho Federal ao vivo (inscrição, incompatibilidade e impedimento, sociedade de advogados, disciplina em grau de recurso, súmulas e provimentos) e pareceres do Tribunal de Ética e Disciplina da OAB/SP em índice local (publicidade, honorários, sigilo, captação, conflito de interesses), com conferência literal de citação que diz de quem é a frase. Use quando o usuário perguntar o que a OAB, o Conselho Federal ou o TED entendem sobre ética profissional ou exercício da advocacia, quando for defender advogado em representação ou processo disciplinar, responder notificação da Seccional, ou conferir citação de ementa da OAB numa peça. Não use para jurisprudência judicial (tribunais), nem trate decisão da OAB como precedente judicial.
---

# Jurisprudência da OAB (Conselho Federal + TED-OAB/SP)

Decisão da OAB é **decisão administrativa de órgão de classe**, não jurisprudência judicial. O parecer do TED responde
consulta em tese e só persuade fora da seccional; decisão do Conselho Federal orienta o sistema OAB, mas não vincula o
Judiciário. Diga isso na entrega — o servidor já o diz em toda resposta.

## Fluxo

1. **Conselho Federal primeiro** — é a instância que uniformiza. `buscar_normas_cfoab(tipo="sumula")` sobre o tema: súmula
   do Pleno ou do Órgão Especial prevalece sobre ementa de câmara. Depois `buscar_jurisprudencia_cfoab` (`orgao`, `relator`,
   `tipo="disciplinar"`; `triagem=true` para ler 20 candidatos e reordenar).
2. **TED-OAB/SP** — `diagnostico_base_ted_sp` para ver a cobertura. Índice vazio: `importar_pacote_ted_sp` (zero requisição
   à OAB). Tema sem texto baixado: `pesquisar_tema_no_portal_ted_sp` com UM termo do título (a busca do portal casa a
   expressão inteira: "instagram" traz, "publicidade instagram" zera).
3. **`buscar_ementas_ted_sp`** — pergunta em português corrente funciona; `grupos` para sinônimos; `cita="E-4.607/2016"` ou
   `cita="art. 34 do EAOAB"` para ver quem aplica um precedente ou dispositivo. O **panorama** mostra os precedentes que o
   TED mais cita sobre o tema — siga a linha até o mais recente. `mapa_de_citacoes_ted_sp` mostra a cadeia.
4. **`obter_parecer_ted_sp`** / **`obter_ementa_cfoab`** nos que importam (no máximo 3). Relator e data do TED: o que vale é
   o **fecho literal** que a ferramenta devolve; com voto vencido, o parecer vencedor pode ser do revisor.
5. **`verificar_citacao_*`** antes de qualquer aspas. Leve a sério os alertas:
   - **TRANSCRIÇÃO** — o parecer reproduz a ementa de OUTRO parecer (o alerta diz qual). Cite esse outro, ou "citando o…".
   - **VOTO DE OUTRO JULGADOR** / **DOUTRINA** / **ENTRE ASPAS** — não é a voz da Turma.
   - **NEGAÇÃO LOGO ANTES** — o recorte pode dizer o contrário.

## O que dizer e o que não dizer

- Zero resultado nunca é "a OAB nunca decidiu". No TED, se o cabeçalho diz `BASE PARCIAL`, é "não há no que foi baixado".
- Ementas marcadas `[SÓ TÍTULO]` não têm texto: título de ementário não é ementa (já houve título dizendo o oposto do parecer).
- O TED não marca ementa superada (ex.: publicidade antes e depois do Provimento 205/2021). Prefira a mais recente e diga a data.
- `PESQUISA NÃO REALIZADA` se relata como tal. Nunca contorne o disjuntor por navegador, proxy ou script.
- Nunca cite número de processo, relator ou órgão de memória. Só o que veio desta sessão.
- Ementa disciplinar do Conselho Federal pode trazer nome e OAB do advogado representado: cite por número, não reproduza o nome.

## Entrega

Para cada decisão aproveitada: órgão (do fecho, no TED), número do processo e da ementa, relator, data, link, nível de
verificação (`só ementa/índice` no Conselho Federal, que não publica o voto; `inteiro teor lido` no TED depois de
`obter_parecer_ted_sp`), o trecho conferido com os alertas, e uma linha dizendo **o que a decisão orienta e se sustenta,
sustenta em parte ou contraria a tese** — mais a natureza: orientação administrativa, não precedente judicial. Decisão
contrária encontrada se entrega também.
