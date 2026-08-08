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

## Conferência

Vale abrir o executável construído antes de distribuir e passar por:
abrir um PDF, exportar um PGN, conferir com o motor. São os três caminhos que
dependem de arquivo externo, e portanto os três que o empacotamento pode quebrar
sem que nenhum teste perceba.
