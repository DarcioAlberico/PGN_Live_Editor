# PGN Live Editor

Editor de texto assistido para transformar texto de livro de xadrez em PGN confiável.
Cole o texto do PDF, vá marcando comentários, variantes e linha principal, é o programa
reconhece a estrutura em tempo real com tabuleiro sincronizado.

Documentação do projeto: [análise técnica](docs/ANALISE.md) - [específicação](docs/SPEC.md) - [roadmap](docs/ROADMAP.md).

## Requisitos

- `uv` instalado
- Python 3.12 disponível localmente

## Uso com uv

Entre na pasta do projeto:

```powershell
cd pgn_live_editor
```

Se existir um `.venv` antigo apontando para um Python removido, recrie o ambiente:

```powershell
Remove-Item -Recurse -Force .venv
uv sync
```

Executar a aplicação:

```powershell
uv run pgn-live-editor
```

Compatibilidade com o launcher antigo:

```powershell
uv run python main.py
```

## Fluxo de trabalho

1. **Colar** o texto do PDF (ou **importar** um `.pgn`/`.txt` com `Ctrl+I`).
2. **Limpar a colagem** com `Ctrl+Shift+V`: remonta palavras cortadas no fim da linha,
   junta linhas quebradas, tira números de página e cabeçalhos repetidos. Tem prévia.
3. Ir marcando a estrutura **sem digitar delimitador nenhum**:
   selecionar a prosa e `Ctrl+K`, selecionar os lances alternativos e `Ctrl+9`.
4. **Renumerar** com `Ctrl+R` para o programa acertar todos os números de lance
   a partir do tabuleiro.
5. Conferir os alertas (`F8`) e o tabuleiro.
6. **Salvar o projeto** (`Ctrl+S`) em `.plez` - guarda o **texto bruto**, que é o trabalho de verdade.
7. **Exportar PGN** (`Ctrl+Shift+E`) quando a partida estiver pronta.

### Um capitulo inteiro, não uma partida

Um documento pode conter o livro todo. O programa corta em partidas sozinho no
início de cada bloco `[Tag "..."]` precedido de linha em branco; em texto que
ainda não tem cabeçalho, uma linha `=== Partida 2 ===` faz o mesmo serviço.

A lista da esquerda aparece a partir da segunda partida: título tirado dos
cabeçalhos (`13. Smyslov - Rudakovsky, Moscou 1945`), número de lances, alertas
e a marca de **concluida**. Clicar leva o cursor ate la; `Ctrl+Shift+P` e
`Ctrl+Shift+O` andam entre partidas e `Ctrl+Shift+D` marca a atual como pronta.

O tabuleiro, o preview e o painel de alertas seguem sempre a partida que contem
o cursor. Cada uma tem a **sua** posição inicial: um exercício que parte de um
diagrama só precisa do `[FEN]`, e o botão *Posição inicial* grava no cabeçalho
a posição que estiver no tabuleiro, com `[SetUp "1"]` junto. Marcadores de
diagrama do livro (`[D]`, `(diagrama)`) viram o comentário padrão `{[#]}`.

`Ctrl+H` abre os cabeçalhos da partida sob o cursor. O que se repete no livro
inteiro -- evento, local, data, rodada, comentarista -- pode ser guardado como
**template do livro** e reaproveitado nas partidas seguintes com um clique.

Para exportar PGN ha três caminhos: **esta partida** (`Ctrl+Shift+E`), **o livro
num arquivo so** ou **uma partida por arquivo** (`01_Smyslov-Rudakovsky.pgn`,
`02_...`). Nos três, cada partida passa pela conferência de round-trip antes de
ser gravada, é o relatório diz quais não conferiram.

Ha ainda duas saídas que não são PGN. O **`.json` estrutural** é a única que não
perde nada: guarda o trecho de origem no texto bruto, a confiança de cada lance e
o que o programa leu antes de corrigir -- é dissó que precisa quem for conferir o
trabalho com outro programa. O **`.txt` normalizado** é o oposto: so número e
lance, sem chave nem parentese, para passar o olho ao lado da página impressa.

Digitar num capitulo grande não trava: só a partida que está sendo editada e
reanalisada, é isso acontece fora da thread da interface. Abrir um livro de 64
partidas custa cerca de meio segundo, uma vez; cada tecla depois disso custa uma
partida.

O texto bruto é a fonte da verdade; o PGN é uma saída derivada. Por isso *Salvar projeto*
e *Exportar PGN* são ações diferentes.

### Marcar a estrutura sem digitar chaves

| Gesto | Atalho | O que faz |
|---|---|---|
| Envolver em comentário | `Ctrl+K` | a seleção vira `{...}` |
| Envolver em variante | `Ctrl+9` | a seleção vira `(...)` **já com o número de lance certo**, é o lance seguinte da linha principal recupera o dele |
| Desenvolver | `Ctrl+Shift+K` | tira o `{}` ou `()` em volta do cursor |
| Paragrafo vira comentário | `Ctrl+Shift+C` | pega a prosa solta sob o cursor é a envolve em chaves |
| Renumerar documento | `Ctrl+R` | recalcula todos os números a partir do tabuleiro |
| Promover variante | `Ctrl+Shift+Up` | a variante vira linha principal e vice-versa |
| Conferir delimitadores | `Ctrl+Shift+B` | lista `{` e `(` sem fechamento |

Digitar `{` ou `(` com texto selecionado também envolve a seleção; sem seleção, o
delimitador já entra fechado. Cada gesto é **um único Ctrl+Z**.

### Copiar trechos

O menu **Copiar** e o botão direito sobre a árvore de lances oferecem: esta linha entre
parênteses, daqui ate o fim, sem comentários, a FEN da posição, a linha principal, ou
um PGN completo com `[FEN]`/`[SetUp]` a partir da posição atual.

### Nada e corrigido em segredo

O painel de alertas na parte de baixo mostra tudo que o parser fez com o texto:

| Marca no editor | Significado |
|---|---|
| azul | lance entendido sem nenhuma dúvida |
| verde sublinhado | corrigido com alta confiança (ex.: `Cf3` -> `Nf3`, `4Jf3` -> `Nf3`) |
| amarelo pontilhado | corrigido **em caráter provisorio** -- precisa de conferência |
| vermelho ondulado | não reconhecido; nenhum lance foi aplicado |

Cada alerta tem quatro ações: **Aceitar correção** (grava no texto bruto), **Virar comentário**,
**Aprender** (guarda a correção no dicionário do projeto) e **Ignorar**.
`F8` percorre os alertas, `F4` aceita o selecionado, `F9` mostra/oculta o painel.

Prosa nunca e alterada: `chances`, `Cada`, `Chega` e `mate` continuam exatamente como você
escreveu. A troca de letra de peça so acontece sobre um token com cara de lance **e** so se o
resultado for legal na posição.

O programa também descobre sozinho em que idioma o livro está escrito. Achando um `Cf3`
ou um `Dc7` -- letras que não existem na notação inglesa -- ele passa a ler `Rh1` como
**Rei**, e não como Torre. Num final de torre as duas leituras costumam ser legais na
mesma posição, e escolher errado estraga a partida inteira dali para frente. Num PGN
inglês importado nada disso acontece: sem a prova, `Re1` continua sendo a torre.

### O dicionário aprende com você

`Ctrl+Shift+I` abre o dicionário de OCR. Ele tem três camadas, na ordem em que são
consultadas: **embutido** (o que é verdade em qualquer digitalização), **meu**
(o ruído do seu scanner, que vale para todos os seus livros) e **deste projeto**
(a leitura errada de uma fonte específica, que não deve contaminar os outros).
A camada mais específica vence.

Cada regra tem **escopo**: o token inteiro (`Bh5` -> `Bb5`), o início do lance
(`4J` -> `N`, e `4Jf3` vira `Nf3`) ou qualquer trecho. O último é o mais perigoso,
e por isso a tabela mostra a coluna **Alcance** -- quantos lances do texto aberto
agora cada regra pega, antes de você grava-la.

Você não precisa abrir esse dialogo para o programa aprender: **na terceira vez**
que você aceitar a mesma correção a mao, ele oferece grava-la. Uma vez é acaso,
duas é coincidência, três é o scanner fazendo sempre a mesma coisa.

### O PDF do livro ao lado do editor

`F11` mostra o painel de PDF: a página em cima, o texto extraido dela embaixo.
Selecione o trecho e mande para o cursor com `Ctrl+Enter`; o botao *Próxima
página e anexar* percorre um capitulo sem sair do teclado. A página em que você
parou fica guardada no projeto.

### Só o que foi mexido

`F12` liga a **dupla visao**: o realce apaga tudo que o programa apenas confirmou
e deixa acesó o que ele corrigiu, aplicou em caráter provisorio ou não entendeu.
Numa partida de 80 lances com três correções, são as três que saltam -- que é a
única revisão que vale a pena fazer. A barra de status mostra a conta separada.

### O motor desconfia por você

`Ctrl+E` manda o motor conferir a linha principal da partida sob o cursor. Ele não está
ali para analisar a partida -- está para **desconfiar do OCR**. Um lance mal lido também
é um lance legal, e por isso passa por todas as outras verificações; o que o entrega é a
avaliação despencar sem o livro ter marcado com `?`. Se fosse erro do mestre, o livro
teria comentado.

Quando existe um lance legal a um caractere do que foi lido, e so ele não perde material,
o alerta já vem com o conserto pronto para gravar. Havendo mais de um, o alerta lista e
**não aplica**.

O motor é opcional: sem Stockfish instalado o programa segue inteiro. Ele e procurado no
`PATH`, na variavel `PGN_LIVE_EDITOR_ENGINE` ou no caminho que você apontar em
*Revisão - Motor de xadrez...*.

### O tabuleiro

Coordenadas, inversao (`Ctrl+F`), arrastar-e-soltar com peça fantasma e
clique-clique -- os dois funcionam. Ao promover, o programa **pergunta** qual
peça; soltar com `Shift` promove a dama direto.

O botao *Setas* liga o modo anotação: arrastar com o botao direito desenha uma
seta, clicar marca a casa, e `Alt`, `Ctrl` e `Shift` trocam a cor. Sai como
`[%cal]` e `[%csl]` no comentário do lance -- o formato que todo programa de
xadrez le. O contrario também vale, e é a metade mais útil: **o que o livro
desenhou aparece no tabuleiro**.

### Preferencias

`Ctrl+,` guarda o que vale para todos os trabalhos: idioma da notação, tema
(escuro ou claro), fonte do editor, caminho do motor e auto-fechamento de
delimitadores. O tamanho da janela e a divisão dos paineis voltam como estavam.

Cabeçalho, dicionário do projeto e PDF pertencem ao **livro**, não ao programa:
ficam no `.plez`.

### Segurança do trabalho

- **Autosave** a cada 30 s ou a cada 200 caracteres digitados.
- **Recuperação de sessão**: se o programa fechar sem salvar, ao reabrir ele oferece o texto de volta.
- **Conferência na exportação**: todo PGN gerado e relido pelo `python-chess` e comparado
  com a análise antes de ser gravado. Divergencia gera aviso, nunca um arquivo silenciosamente errado.

### Atalhos

| Atalho | Acao |
|---|---|
| `Ctrl+N` / `Ctrl+O` / `Ctrl+S` | novo / abrir / salvar projeto |
| `Ctrl+I` | importar PGN ou texto |
| `Ctrl+Shift+V` | limpar texto colado do PDF |
| `Ctrl+K` / `Ctrl+9` | envolver em comentário / variante |
| `Ctrl+Shift+K` / `Ctrl+Shift+C` | desenvolver / paragrafo vira comentário |
| `Ctrl+R` | renumerar o documento inteiro |
| `Ctrl+Shift+E` | exportar esta partida |
| `Ctrl+Shift+P` / `Ctrl+Shift+O` | próxima / anterior partida do livro |
| `Ctrl+H` | cabeçalhos da partida |
| `Ctrl+Shift+D` | marcar a partida como concluida |
| `Ctrl+Left` / `Ctrl+Right` | lance anterior / próximo |
| `Ctrl+F` | inverter tabuleiro |
| `F8` / `Shift+F8` | próximo / anterior alerta |
| `F4` | aceitar a correção do alerta selecionado |
| `Ctrl+E` | conferir a partida com o motor |
| `Ctrl+Shift+I` | dicionário de OCR |
| `Ctrl+,` | preferencias |
| `Ctrl+Space` | listar os lances legais neste ponto |
| `Ctrl+D` | paleta de símbolos |
| `Ctrl+Home` / `Ctrl+End` | primeiro / último lance |
| `Ctrl+Shift+Up` / `Ctrl+Shift+Down` | promover variante / rebaixar a linha principal |
| `Alt+Up` / `Alt+Down` | subir / descer a variante entre as irmas |
| `Ctrl+Shift+A` | reancorar a variante no lance sob o cursor |
| `F11` / `F12` | painel de PDF / dupla visao |
| `F9` / `F10` | mostrar/ocultar o painel de alertas / a lista de partidas |
| `Ctrl+Z` | desfazer (funciona também em *Join Lines* e *Inserir Headers*) |

## Desenvolvimento

Executar os testes:

```powershell
uv run python -m unittest discover -s tests
```

### Corpus de regressao

`tests/corpus/` guarda páginas de livro em texto bruto com o PGN que cada uma **tem** que
produzir. E o único teste que roda o programa na direção em que ele e usado de verdade --
texto colado entra, PGN sai -- e por isso é o que mede a coisa que interessa: nenhum lance
perdido em silêncio.

Para acrescentar uma página bastam dois arquivos, `nome.txt` e `nome.expected.pgn`; o
runner acha sozinho. Os detalhes estao em [tests/corpus/README.md](tests/corpus/README.md).

Vale o aviso: no primeiro dia em que rodou, o corpus achou cinco defeitos que 225 testes
verdes não achavam -- entre eles, comentário de mais de uma linha sendo lido como chave
aberta, e `R` de Rei sendo lido como Torre. Uma página que quebra hoje vale mais que dez
que passam.

Lint, formatação, tipos e cobertura -- os mesmos quatro que o CI roda:

```powershell
uv run ruff check pgn_live_editor tests
uv run ruff format --check pgn_live_editor tests
uv run mypy
uv run coverage run -m pytest -q; uv run coverage report
```

O `mypy` roda em modo gradual: `core/` é obrigatório e está limpo, `ui/` e
`services/` ficam para depois. A cobertura tem piso de 80 % em `core/` e esta
em 93 %.

## Executável do Windows

Quem for usar o programa não precisa instalar Python:

```powershell
uv sync --extra package
uv run pyinstaller packaging/pgn-live-editor.spec --noconfirm
```

O resultado sai em `dist/PGN Live Editor/` -- a pasta inteira é o programa.
Detalhes em [packaging/README.md](packaging/README.md).
