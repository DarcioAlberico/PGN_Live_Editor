"""De onde veio um texto lido, como vocabulário -- e não como `if` no meio do pipeline (S-207).

**Este módulo nasceu de uma regra que já existia, e que a S-207 quase quebrou.**
`tests/test_text_recognizer.py::test_a_s43_nao_precisou_saber_que_o_glifo_existe` cobra que
`ocr_caption.py` e `pdf_text.py` **não mencionem motor nenhum pelo nome** -- porque no dia em que
mencionarem, a próxima fonte de texto vai precisar de uma segunda porta, que é exatamente o que a
S-43 recusou construir.

A S-207 precisa que a linha lida pelo classificador de casa saia distinguível da lida pelo motor
de terceiros. Feito ingenuamente, isso é um `if nome == "glifo"` dentro dos dois arquivos
proibidos. Feito aqui, é uma tabela: **os dois módulos passam o nome do motor e recebem a
procedência**, sem saber quais existem.

## As três procedências, e por que `glifo` não é `ocr`

    text     a camada do PDF, escrita pelo editor. Não é palpite: vale 1,0
    ocr      motor de terceiros (RapidOCR, EasyOCR, Tesseract)
    glifo    o classificador de 314 classes treinado neste projeto, neste acervo

Os dois últimos são motor lendo pixel, e não são a mesma coisa. Colapsá-los faria
`[SideToMoveSource "ocr"]` significar duas qualidades diferentes -- e a Fase 3 existe justamente
para que um palpite pareça o palpite que é.

## O escopo é sufixo, e por isso não há segunda tabela

Uma declaração de lado a jogar vale para o diagrama ou para a página inteira, e as duas formas
existem para as três procedências. O nome da de página é o da de diagrama mais `-page-scope`, sem
exceção -- então `escopo_de_pagina` é uma função de uma linha em vez de seis constantes que
poderiam divergir.
"""

from __future__ import annotations

from typing import Literal

MOTOR_DE_CASA = "glifo"
"""O nome do único motor deste projeto, e a procedência que ele produz.

Uma definição: `ocr.KNOWN_ENGINES` a lista, `text/recognizer.NOME` a devolve, `build_recognizer`
despacha por ela, e `procedencia_do_motor` a traduz. Escrita em quatro lugares, a primeira troca
de nome deixaria três certos e um errado -- e o errado seria o que grava o header do PGN."""

SUFIXO_DE_PAGINA = "-page-scope"

LineOrigin = Literal["text", "ocr", "glifo"]
"""De onde a linha veio. Ver "As três procedências" no cabeçalho."""

SideOrigin = Literal["text", "ocr", "glifo", "text-page-scope", "ocr-page-scope", "glifo-page-scope"]
"""As seis procedências textuais do lado a jogar: três fontes x dois escopos."""

DE_TERCEIROS: LineOrigin = "ocr"
DA_CAMADA: LineOrigin = "text"


def procedencia_do_motor(nome: str) -> LineOrigin:
    """`glifo` para o motor de casa, `ocr` para qualquer outro.

    **Quem chama passa o nome e não sabe a tabela**, que é o ponto: `ocr_caption` pergunta ao seu
    reconhecedor como ele se chama e repassa a resposta. Um motor novo de terceiros entra sem uma
    linha de mudança lá; um segundo motor de casa entraria aqui, num lugar só.
    """
    return MOTOR_DE_CASA if nome == MOTOR_DE_CASA else DE_TERCEIROS  # type: ignore[return-value]


def escopo_de_pagina(origem: str) -> SideOrigin:
    """A procedência de página correspondente. `text` -> `text-page-scope`.

    Idempotente: uma origem que já é de página volta como está. Sem isso, um chamador que a
    aplicasse duas vezes produziria `text-page-scope-page-scope`, que não é valor de `SideOrigin`
    nenhum e atravessaria o programa até o header do PGN sem ninguém reclamar.
    """
    if origem.endswith(SUFIXO_DE_PAGINA):
        return origem  # type: ignore[return-value]
    return f"{origem}{SUFIXO_DE_PAGINA}"  # type: ignore[return-value]
