"""Constantes e definições de layout do ENADE para o gerador de arquivos.

Suporta três categorias de inscrição em lote conforme os Anexos
do Edital específico do Enade publicado anualmente pelo INEP:

- Concluintes                 -> Anexo VII (identificador de arquivo: N99)
- Ingressantes                -> Anexo VIII (identificador de arquivo: N92)
- Estudantes do 4º de Medicina-> Anexo VI  (identificador de arquivo: N99M)

O código não fica preso a uma edição específica do Enade: o ano de
referência usado nas validações (faixas mínima/máxima de anos) é
informado pelo usuário ou inferido a partir da data corrente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Constantes fixas do nome de arquivo (todas as categorias)
# ---------------------------------------------------------------------------

CO_PROJETO = "2611101"
TP_ORIGEM = "E"
UF_FIXA = "BR"
NO_PROGRAMA = "ENADE"

# ---------------------------------------------------------------------------
# Categorias suportadas
# ---------------------------------------------------------------------------

CATEGORIA_CONCLUINTE = "concluinte"
CATEGORIA_INGRESSANTE = "ingressante"
CATEGORIA_MEDICINA = "medicina4"

# Campos do layout para cada categoria, na ordem oficial do edital.

CAMPOS_LAYOUT_CONCLUINTE: list[str] = [
    "CO_PROJETO",
    "TP_ORIGEM",
    "CO_IES",
    "CO_CURSO",
    "NU_CPF",
    "NU_ANO_FIM_ENSINO_MEDIO",
    "CO_TURNO_GRADUACAO",
    "NU_PERCENTUAL_INTEGRALIZACAO",
    "NU_ANO_FORMATURA",
    "NU_SEMESTRE_FORMATURA",
    "NU_ANO_INICIO_GRADUACAO",
    "IN_MUNICIPIO_POLO_EXTERIOR",
    "CO_MUNICIPIO_POLO",
]

CAMPOS_LAYOUT_INGRESSANTE: list[str] = [
    "CO_PROJETO",
    "TP_ORIGEM",
    "CO_IES",
    "CO_CURSO",
    "NU_CPF",
    "NU_ANO_FIM_ENSINO_MEDIO",
    "CO_TURNO_GRADUACAO",
    "NU_PERCENTUAL_INTEGRALIZACAO",
    "NU_ANO_INICIO_GRADUACAO",
    "NU_SEMESTRE_INICIO_GRADUACAO",
]

CAMPOS_LAYOUT_MEDICINA: list[str] = [
    "CO_PROJETO",
    "TP_ORIGEM",
    "CO_IES",
    "CO_CURSO",
    "NU_CPF",
    "NU_ANO_FIM_ENSINO_MEDIO",
    "CO_TURNO_GRADUACAO",
    "NU_PERCENTUAL_INTEGRALIZACAO",
    "NU_ANO_FORMATURA",
    "NU_SEMESTRE_FORMATURA",
    "NU_ANO_INICIO_GRADUACAO",
    "IN_MUNICIPIO_POLO_EXTERIOR",
    "CO_MUNICIPIO_POLO",
    "IN_ORIGEM_ENAMED",
]


@dataclass(frozen=True)
class Categoria:
    """Descreve o layout/comportamento de uma categoria de inscrição em lote."""

    chave: str
    rotulo: str
    identificacao_arquivo: str
    anexo: str
    campos: list[str]
    casas_decimais_percentual: int
    exige_formatura: bool
    exige_polo: bool
    exige_origem_enamed: bool


CATEGORIAS: dict[str, Categoria] = {
    CATEGORIA_CONCLUINTE: Categoria(
        chave=CATEGORIA_CONCLUINTE,
        rotulo="Concluintes",
        identificacao_arquivo="N99",
        anexo="Anexo VII",
        campos=CAMPOS_LAYOUT_CONCLUINTE,
        casas_decimais_percentual=1,
        exige_formatura=True,
        exige_polo=True,
        exige_origem_enamed=False,
    ),
    CATEGORIA_INGRESSANTE: Categoria(
        chave=CATEGORIA_INGRESSANTE,
        rotulo="Ingressantes",
        identificacao_arquivo="N92",
        anexo="Anexo VIII",
        campos=CAMPOS_LAYOUT_INGRESSANTE,
        casas_decimais_percentual=1,
        exige_formatura=False,
        exige_polo=False,
        exige_origem_enamed=False,
    ),
    CATEGORIA_MEDICINA: Categoria(
        chave=CATEGORIA_MEDICINA,
        rotulo="Medicina – 4º ano",
        identificacao_arquivo="N99M",
        anexo="Anexo VI",
        campos=CAMPOS_LAYOUT_MEDICINA,
        casas_decimais_percentual=1,
        exige_formatura=True,
        exige_polo=True,
        exige_origem_enamed=True,
    ),
}


def categoria_por_chave(chave: str) -> Categoria:
    if chave not in CATEGORIAS:
        raise KeyError(f"Categoria desconhecida: {chave!r}")
    return CATEGORIAS[chave]


# ---------------------------------------------------------------------------
# Valores válidos
# ---------------------------------------------------------------------------

TURNOS_VALIDOS: dict[int, str] = {
    1: "Matutino",
    2: "Vespertino",
    3: "Integral",
    4: "Noturno",
}

# Faixas de anos. O ano de referência é fornecido pelo chamador (default = ano atual).
ANO_MIN_ENSINO_MEDIO_BASE = 1950
ANO_MIN_INICIO_GRAD_BASE = 1980
ANOS_FORMATURA_HORIZONTE = 10


def ano_corrente() -> int:
    return datetime.now().year


def faixas_de_anos(ano_referencia: int) -> dict[str, tuple[int, int]]:
    """Constrói as faixas de validação de anos para um ano de referência.

    Mantém uma janela razoável para cada campo, sem amarrar o software a
    uma edição específica do Enade.
    """
    return {
        "NU_ANO_FIM_ENSINO_MEDIO": (ANO_MIN_ENSINO_MEDIO_BASE, ano_referencia),
        "NU_ANO_INICIO_GRADUACAO": (ANO_MIN_INICIO_GRAD_BASE, ano_referencia),
        "NU_ANO_FORMATURA": (ano_referencia, ano_referencia + ANOS_FORMATURA_HORIZONTE),
    }
