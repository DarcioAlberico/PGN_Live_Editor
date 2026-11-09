# Empacotamento

O público-alvo não instala Python. O que ele quer é um `.exe` que abre.

```powershell
uv sync --extra dev
uv run pyinstaller packaging/pgn-live-editor.spec --noconfirm
```

O resultado sai em `dist/PGN Live Editor/`. A pasta inteira é o programa —
copiar só o `.exe` não funciona.

## Duas decisões que estão no spec

**Pasta única, não arquivo único.** O `--onefile` descompacta o Qt inteiro numa
pasta temporária *a cada abertura*: são alguns segundos de espera antes da
janela aparecer, e é o padrão que mais aciona antivírus. Uma pasta de 109 MB que
abre na hora vale mais que um arquivo de 60 MB que demora.

**Sem os módulos Qt que o programa não usa.** QML, Quick, Multimedia e
WebEngine somam cerca de 40 MB e não entram em nada aqui. Já `QtPdf`, `QtSvg` e
os widgets deles precisam ser declarados à mão em `hiddenimports`: o analisador
do PyInstaller não os encontra sozinho, porque o painel de PDF e o tabuleiro os
importam por caminho que ele não segue. Sem isso o executável constrói, abre, e
quebra na primeira vez que se abre um PDF — que é o pior tipo de falha, porque
só aparece na mão do usuário.

## Stockfish junto (opcional)

Por padrão o motor **não** vai no pacote: o executável tem ~100 MB e o programa
funciona sem ele. Para levar o Stockfish que está em `engine/`:

```powershell
$env:PGN_BUNDLE_ENGINE = "1"
uv run pyinstaller packaging/pgn-live-editor.spec --noconfirm
```

Entram o `stockfish*.exe` e, ao lado dele, `Copying.txt`, `AUTHORS` e o `README.md`
do Stockfish. Não é enfeite: o Stockfish é GPLv3, e distribuir o binário obriga a
levar a licença junto e a indicar onde está o código-fonte (o README dele aponta o
repositório oficial). O código-fonte em `engine/stockfish/src` não entra no pacote.

O programa acha o motor sozinho dentro do pacote; ninguém precisa configurar nada.

## OCR de texto

Se o ambiente que empacota tiver o extra `text-ocr` (`uv sync --extra text-ocr`),
os módulos `winrt` entram no pacote e o executável lê PDFs escaneados. Sem o
extra, o executável funciona igual, só sem o botão de OCR.

## Conferência

Vale abrir o executável construído antes de distribuir e passar por:
abrir um PDF, exportar um PGN, conferir com o motor. São os três caminhos que
dependem de arquivo externo, e portanto os três que o empacotamento pode quebrar
sem que nenhum teste perceba.
