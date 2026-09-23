# Jurisprudência da OAB no Claude — instalação passo a passo

Este guia é para **quem nunca usou o Terminal**. Cada comando abaixo você **copia e cola** — não precisa entender o que ele
faz para funcionar (mas eu explico mesmo assim). Depois da instalação, o dia a dia é **100% dentro do Claude**, em português.

> **O que este projeto faz, em uma frase:** ele deixa o Claude pesquisar as decisões do **Conselho Federal da OAB** e os
> pareceres do **Tribunal de Ética e Disciplina da OAB/SP** e, antes de você citar uma frase em peça, confere se ela está
> literalmente no texto e **de quem ela é** — do próprio parecer, de outro parecer transcrito dentro dele, de um voto
> divergente ou de um doutrinador.

## Como ele funciona, em termos simples

São duas fontes, e elas funcionam de jeitos diferentes:

- **Conselho Federal:** consultado **ao vivo**, a cada pergunta, direto no portal oficial (`jurisprudencia.oab.org.br`).
  Não guarda nada no seu computador.
- **TED da OAB/SP:** o ementário fica **guardado no seu computador** e a busca roda nessa cópia. Motivo: a busca do site da
  OAB/SP só procura no *título* da ementa; aqui ela procura no parecer inteiro. E o portal da OAB/SP é frágil — por isso o
  ementário vem num **pacote pronto** do GitHub, sem ninguém precisar consultar o portal milhares de vezes.

> **Isto não é jurisprudência judicial.** São decisões administrativas de órgão de classe: o parecer do TED responde consulta
> em tese e só persuade fora de São Paulo. O servidor diz isso em toda resposta.

## Quanto espaço em disco reservar

| O que | Tamanho |
|---|---|
| O programa e suas bibliotecas | ~65 MB, medido |
| O ementário do TED-SP inteiro (~5.400 pareceres, 1994–hoje) | até ~400 MB (projeção) |
| **Folga recomendada** | **1 GB livre** |

## Antes de começar: dois programas que talvez faltem

### O que é o "Terminal"

É um aplicativo que já vem no Mac, para colar comandos de texto em vez de clicar em ícones. Você vai usá-lo só nesta instalação.

**Para abrir:** pressione `Cmd` + `Espaço`, digite `Terminal` e pressione `Enter`.

> **Como colar um comando:** copie o texto do bloco cinza, clique dentro da janela do Terminal, pressione `Cmd` + `V` e
> depois `Enter`. Espere terminar antes de colar o próximo.

*No Windows:* os comandos são os mesmos no **PowerShell**, trocando `.venv/bin/python` por `.venv\Scripts\python.exe` e
`~/MCP` por `$HOME\MCP`.

### 1. Python (versão 3.10 ou mais nova)

```bash
python3 --version
```

- **Apareceu `Python 3.10` ou maior?** Pule para o passo 2.
- **Deu erro ou versão menor?** Baixe o instalador em **[python.org/downloads](https://www.python.org/downloads/)**, dê dois
  cliques e siga a instalação normal. Feche e abra o Terminal e repita o comando.

### 2. Git

```bash
git --version
```

- **Apareceu um número de versão?** Pule para o passo 3.
- **O Mac perguntou "Instalar as ferramentas de linha de comando do Xcode?"** Clique em **Instalar**, espere terminar e repita.

## Instalar (10 minutos, a maior parte esperando)

### 3. Baixe o projeto

```bash
git clone https://github.com/robertogecia/mcp-oab-jurisprudencia.git ~/MCP/oab-jurisprudencia
cd ~/MCP/oab-jurisprudencia
```

O primeiro comando copia o projeto para a pasta `MCP/oab-jurisprudencia`, dentro da sua pasta de usuário. O segundo entra nela.

> **Não instale dentro do OneDrive, iCloud Drive ou Dropbox.** A base de dados e o sincronizador de nuvem brigam pelo mesmo
> arquivo. A pasta `~/MCP` fica fora deles.

### 4. Instale as peças de que ele precisa

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

O primeiro cria uma "caixa isolada" (`.venv`) só para este projeto. O segundo baixa, dentro dela, as três bibliotecas
necessárias. Pode levar um minuto.

> Dentro de `requirements.txt` está escrito `mcp<2` de propósito: com a versão 2.x, o servidor conecta ao Claude só na
> aparência e nenhuma ferramenta funciona, sem aviso.

### 5. Confira que deu tudo certo

```bash
.venv/bin/python servidor_tedsp.py --selftest
```

Roda as verificações internas, **sem acessar a internet**. A última linha tem de ser:

```
selftest offline OK
```

Se aparecer qualquer erro, pare aqui e não use em caso real — abra uma
[issue](https://github.com/robertogecia/mcp-oab-jurisprudencia/issues) com a saída (sem nenhum dado de cliente).

### 6. Ligue ao Claude

**Se você usa o Claude Code** (o Claude no Terminal):

```bash
claude mcp add oab_jurisprudencia -- ~/MCP/oab-jurisprudencia/.venv/bin/python ~/MCP/oab-jurisprudencia/servidor_tedsp.py
```

**Se você usa o Claude Desktop** (o aplicativo com janela):

1. Abra o Claude Desktop → **Configurações** → **Desenvolvedor** → **Editar configuração**. Abre o arquivo
   `claude_desktop_config.json`.
2. Dentro de `"mcpServers"`, acrescente o trecho abaixo, trocando `SEU-USUARIO` pelo nome da sua conta no Mac (se não souber,
   cole `whoami` no Terminal):

```json
{
  "mcpServers": {
    "oab_jurisprudencia": {
      "command": "/Users/SEU-USUARIO/MCP/oab-jurisprudencia/.venv/bin/python",
      "args": ["/Users/SEU-USUARIO/MCP/oab-jurisprudencia/servidor_tedsp.py"]
    }
  }
}
```

3. Salve e **feche e abra o Claude Desktop de novo** — ele só lê essa configuração ao iniciar.

### 7. Traga o ementário do TED-SP

Abra uma **conversa nova** no Claude e escreva:

> **Importe o pacote do TED-SP.**

O Claude pede permissão para usar a ferramenta `importar_pacote_ted_sp` — clique em **Permitir**. Ela baixa o pacote pronto
do GitHub, **confere a integridade** (hash sha256) e monta o índice. **Não acessa o portal da OAB/SP.**

Se a resposta for *"este release ainda NÃO traz o pacote pronto"*, o pacote ainda está sendo preparado. Enquanto isso, use a
**busca dirigida**, que traz só os pareceres de um tema e pesa pouco no portal:

> **Pesquise o tema "publicidade" no portal do TED-SP.**

Para conferir, peça: **"Rode o diagnóstico da base da OAB."**

## Como usar (sem Terminal)

> "O que o Conselho Federal decidiu sobre incompatibilidade de advogado que ocupa cargo público?"
>
> "O que o TED-SP entende sobre contrato de honorários com cláusula *quota litis*?"
>
> "Quais pareceres do TED-SP aplicam o E-4.607/2016?"
>
> "Traga o parecer E-5.936/2022 inteiro."
>
> "Confira se esta frase está literalmente no parecer E-5.936/2022: «…»"

**Nunca cite entre aspas sem passar pela conferência.** Ela responde se a frase está palavra por palavra no texto **e de quem
ela é**: o parecer do TED costuma transcrever ementas inteiras de outros pareceres, e um "confere" sem esse aviso atribuiria
ao parecer a frase de outro.

## O que ele NÃO cobre

- **TED-SP:** só a 1ª Turma (deontológica) e só o que está no índice. As Turmas Disciplinares só existem de 2010 a 2014.
- **Conselho Federal:** ementa e acórdão, nunca o voto (a API não publica).
- **Zero resultado nunca é "a OAB nunca decidiu"** — é "não há no que foi consultado". O servidor diz a cobertura em cada resposta.
- Não é jurisprudência judicial, e não substitui a pesquisa nos tribunais.

## Avisos

- **Projeto não-oficial.** Os dados vêm de publicação oficial e aberta da OAB, mas o projeto não é da OAB. Se o portal mudar,
  pode parar até ser atualizado.
- **Toda saída é rascunho.** Quem assina a peça confere.
- **Ritmo.** O servidor consulta devagar e pausa sozinho se o portal recusar (veja o [README](README.md)). Não contorne por
  navegador ou outro programa: o limite existe para o portal continuar de pé.
- **Aviso de atualização.** Ao iniciar, o servidor consulta uma vez a página de versões deste repositório, em segundo plano,
  para avisar se há versão nova. Nada da sua pesquisa sai do seu computador. Para desligar: `TEDSP_MCP_SEM_AVISO_ATUALIZACAO=1`.

## Atualizar

```bash
cd ~/MCP/oab-jurisprudencia && git pull && .venv/bin/python servidor_tedsp.py --selftest
```

Depois reinicie o Claude. **O índice em `dados/` não se perde**: se o parser mudou, ele se refaz sozinho a partir do texto
já guardado, sem rede.

## Desinstalar

1. **Claude Desktop:** Configurações → Desenvolvedor → Editar configuração, e apague o trecho `"oab_jurisprudencia": { ... }`.
   **Claude Code:** `claude mcp remove oab_jurisprudencia`.
2. Apague a pasta: `rm -rf ~/MCP/oab-jurisprudencia` (isso apaga também a base baixada e os recibos).

## Autor

**Roberto Grécia Bessa** — OAB/RO 7865-A
Instagram: [@robertogrecia](https://instagram.com/robertogrecia)
