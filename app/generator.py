"""Validação e geração de arquivos TXT para inscrição em lote no ENADE.

Recebe um DataFrame proveniente de planilha .xlsx ou .csv, valida linha a linha
conforme a categoria escolhida (Concluintes, Ingressantes ou Medicina –
4º ano) e produz o arquivo .txt no formato oficial.
"""

from __future__ import annotations

import csv
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import (
    ANOS_FORMATURA_HORIZONTE,
    CATEGORIA_INGRESSANTE,
    CATEGORIA_MEDICINA,
    CO_PROJETO,
    NO_PROGRAMA,
    TP_ORIGEM,
    TURNOS_VALIDOS,
    UF_FIXA,
    Categoria,
    ano_corrente,
    categoria_por_chave,
    faixas_de_anos,
)

NIVEL_ERRO = "ERRO"
NIVEL_AVISO = "AVISO"
NIVEL_INFO = "INFO"


@dataclass
class Mensagem:
    """Mensagem de validação associada a uma linha da planilha."""

    linha: int
    nivel: str
    campo: str
    mensagem: str

    def __str__(self) -> str:
        return f"[{self.nivel}] Linha {self.linha} - {self.campo}: {self.mensagem}"


@dataclass
class ResultadoValidacao:
    df_normalizado: pd.DataFrame
    mensagens: list[Mensagem] = field(default_factory=list)
    categoria: Categoria | None = None

    @property
    def erros(self) -> list[Mensagem]:
        return [m for m in self.mensagens if m.nivel == NIVEL_ERRO]

    @property
    def avisos(self) -> list[Mensagem]:
        return [m for m in self.mensagens if m.nivel == NIVEL_AVISO]

    @property
    def info(self) -> list[Mensagem]:
        return [m for m in self.mensagens if m.nivel == NIVEL_INFO]

    @property
    def valido(self) -> bool:
        return not self.erros and not self.df_normalizado.empty


# ---------------------------------------------------------------------------
# Carregamento
# ---------------------------------------------------------------------------

EXTENSOES_PLANILHA = frozenset({".xlsx", ".xls", ".csv"})
_EXTENSOES_EXCEL = frozenset({".xlsx", ".xls"})
# .xlsx é um .zip com XML dentro; .xls (formato binário OLE antigo) não é.
_EXTENSAO_ZIP_OFFICE = ".xlsx"
_ENCODINGS_CSV = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# Limites contra "zip bomb": um .xlsx pequeno cujo conteúdo descomprimido é
# desproporcionalmente grande, capaz de esgotar memória/CPU ao ser aberto
# pelo openpyxl/pandas antes mesmo de qualquer validação de dados.
MAX_PLANILHA_DESCOMPRIMIDA_MB = 200
_RAZAO_MAXIMA_COMPRESSAO_ZIP = 100  # descomprimido / comprimido


def extensao_planilha_suportada(caminho: str | Path) -> bool:
    return Path(caminho).suffix.lower() in EXTENSOES_PLANILHA


def validar_planilha_segura(caminho: str | Path) -> None:
    """Recusa arquivos ``.xlsx`` suspeitos de "zip bomb" antes de abri-los.

    Levanta ``ValueError`` (mensagem apta a ser exibida ao usuário) quando o
    conteúdo descomprimido excede um limite absoluto ou quando a razão entre
    tamanho descomprimido e comprimido é suspeita demais para uma planilha
    legítima. Não se aplica a ``.xls`` (formato binário OLE, não é um zip)
    nem a ``.csv``.
    """
    path = Path(caminho)
    if path.suffix.lower() != _EXTENSAO_ZIP_OFFICE:
        return

    tamanho_comprimido = path.stat().st_size
    if tamanho_comprimido == 0:
        return

    try:
        with zipfile.ZipFile(path) as zf:
            tamanho_descomprimido = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Arquivo '{path.name}' não é um .xlsx válido.") from exc

    limite_absoluto = MAX_PLANILHA_DESCOMPRIMIDA_MB * 1024 * 1024
    razao_compressao = tamanho_descomprimido / tamanho_comprimido
    if tamanho_descomprimido > limite_absoluto or razao_compressao > _RAZAO_MAXIMA_COMPRESSAO_ZIP:
        raise ValueError(
            f"Arquivo '{path.name}' rejeitado: conteúdo descomprimido "
            f"({tamanho_descomprimido / (1024 * 1024):.1f} MB) incompatível com o "
            "tamanho do upload. Verifique se o arquivo não está corrompido ou manipulado."
        )


def _normalizar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _detectar_separador_csv(caminho: Path, encoding: str) -> str:
    with caminho.open(encoding=encoding, newline="") as handle:
        amostra = handle.read(8192)
    if not amostra.strip():
        return ","
    try:
        dialect = csv.Sniffer().sniff(amostra, delimiters=",;")
        return dialect.delimiter
    except csv.Error:
        return ";" if amostra.count(";") > amostra.count(",") else ","


def _ler_csv(caminho: Path) -> pd.DataFrame:
    ultimo_erro: Exception | None = None
    for encoding in _ENCODINGS_CSV:
        try:
            separador = _detectar_separador_csv(caminho, encoding)
            df = pd.read_csv(
                caminho,
                sep=separador,
                dtype=object,
                encoding=encoding,
                keep_default_na=False,
            )
            return _normalizar_colunas(df)
        except (UnicodeDecodeError, UnicodeError) as exc:
            ultimo_erro = exc
        except pd.errors.ParserError as exc:
            ultimo_erro = exc
    raise ValueError(
        f"Não foi possível ler o CSV '{caminho.name}'. "
        "Verifique a codificação (UTF-8 ou CP1252) e o separador (, ou ;)."
    ) from ultimo_erro


def carregar_planilha(caminho: str | Path) -> pd.DataFrame:
    """Lê planilha .xlsx/.xls ou arquivo .csv com os dados dos estudantes.

    Planilhas Excel: primeira aba. CSV: primeira linha como cabeçalho.
    Colunas adicionais (Nome, Matrícula, Modalidade, etc.) são preservadas.
    """
    path = Path(caminho)
    ext = path.suffix.lower()
    if ext not in EXTENSOES_PLANILHA:
        extensoes = ", ".join(sorted(EXTENSOES_PLANILHA))
        raise ValueError(
            f"Formato '{ext or '(sem extensão)'}' não suportado. Use: {extensoes}"
        )

    if ext in _EXTENSOES_EXCEL:
        df = pd.read_excel(path, sheet_name=0, dtype=object)
        return _normalizar_colunas(df)

    return _ler_csv(path)


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------


def _e_vazio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and pd.isna(valor):
        return True
    try:
        if pd.isna(valor):
            return True
    except (TypeError, ValueError):
        pass
    return str(valor).strip() == ""


def _so_digitos(valor) -> str:
    if _e_vazio(valor):
        return ""
    return "".join(ch for ch in str(valor) if ch.isdigit())


def _normalizar_cpf(valor) -> tuple[str, str | None]:
    """Extrai CPF com 11 dígitos, compensando perda de zeros no Excel.

    Planilhas costumam gravar NU_CPF como número (int/float), o que remove zeros
    à esquerda. Valores com 9 ou 10 dígitos são completados com zfill(11).

    Retorna (cpf_normalizado, aviso_opcional).
    """
    if _e_vazio(valor):
        return "", None

    aviso: str | None = None
    bruto = valor

    if isinstance(valor, float):
        if pd.isna(valor):
            return "", None
        if not valor.is_integer():
            return _so_digitos(valor), None
        bruto = int(valor)
    elif isinstance(valor, int):
        bruto = valor
    else:
        texto = str(valor).strip()
        if "." in texto or "e" in texto.lower():
            try:
                numero = float(texto.replace(",", "."))
                if numero.is_integer():
                    bruto = int(numero)
            except ValueError:
                pass

    cpf = str(bruto) if isinstance(bruto, int) else _so_digitos(bruto)

    if not cpf:
        return "", None

    if len(cpf) < 11:
        if len(cpf) < 9:
            return cpf, None
        cpf_antes = cpf
        cpf = cpf.zfill(11)
        aviso = (
            f"CPF com {len(cpf_antes)} dígito(s) na planilha ('{cpf_antes}'); "
            f"completado com zero(s) à esquerda para 11 dígitos ('{cpf}'). "
            "Se o Excel exibir o CPF como número, formate a coluna como Texto."
        )

    return cpf, aviso


def _para_inteiro(valor) -> int | None:
    if _e_vazio(valor):
        return None
    try:
        if isinstance(valor, float):
            if valor.is_integer():
                return int(valor)
            return None
        return int(str(valor).strip())
    except (ValueError, TypeError):
        return None


def _para_float(valor) -> float | None:
    if _e_vazio(valor):
        return None
    try:
        return float(str(valor).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def _formatar_percentual(valor: float, casas_decimais: int) -> str:
    """Formata percentual com a quantidade exigida de casas decimais.

    O separador decimal é sempre o ponto, conforme exige o leiaute oficial.
    """
    return f"{valor:.{casas_decimais}f}"


def _cpf_valido(cpf: str) -> bool:
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False

    def calc_dv(slice_: str) -> int:
        soma = sum(
            int(d) * peso
            for d, peso in zip(slice_, range(len(slice_) + 1, 1, -1), strict=True)
        )
        resto = (soma * 10) % 11
        return 0 if resto == 10 else resto

    return calc_dv(cpf[:9]) == int(cpf[9]) and calc_dv(cpf[:10]) == int(cpf[10])


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def validar_dataframe(
    df: pd.DataFrame,
    categoria: str | Categoria,
    ano_referencia: int | None = None,
) -> ResultadoValidacao:
    """Valida o DataFrame para a categoria informada.

    Retorna a versão normalizada (somente as colunas do layout, já no
    formato textual que será gravado no .TXT) acompanhada de mensagens.
    """
    cat = categoria if isinstance(categoria, Categoria) else categoria_por_chave(categoria)
    ano_ref = ano_referencia or ano_corrente()
    faixas = faixas_de_anos(ano_ref)

    msgs: list[Mensagem] = []

    faltando = [c for c in cat.campos if c not in df.columns]
    if faltando:
        msgs.append(
            Mensagem(
                linha=0,
                nivel=NIVEL_ERRO,
                campo="(cabeçalho)",
                mensagem=(
                    f"Colunas obrigatórias ausentes na planilha (categoria {cat.rotulo}): "
                    + ", ".join(faltando)
                ),
            )
        )
        return ResultadoValidacao(
            df_normalizado=pd.DataFrame(columns=cat.campos),
            mensagens=msgs,
            categoria=cat,
        )

    coluna_modalidade = _detectar_coluna_modalidade(df)
    df_norm = pd.DataFrame(columns=cat.campos)
    linhas_validas: list[dict] = []

    for idx, row in df.iterrows():
        linha_planilha = int(idx) + 2

        if _linha_totalmente_vazia(row, cat.campos):
            continue

        registro, mensagens_linha = _validar_linha(
            row=row,
            linha=linha_planilha,
            categoria=cat,
            faixas=faixas,
            col_modalidade=coluna_modalidade,
        )
        msgs.extend(mensagens_linha)

        if registro is not None:
            linhas_validas.append(registro)

    if linhas_validas:
        df_norm = pd.DataFrame(linhas_validas, columns=cat.campos)

    if not linhas_validas and not any(m.nivel == NIVEL_ERRO for m in msgs):
        msgs.append(
            Mensagem(
                linha=0,
                nivel=NIVEL_ERRO,
                campo="(arquivo)",
                mensagem="Nenhuma linha de dados encontrada na planilha.",
            )
        )

    return ResultadoValidacao(df_normalizado=df_norm, mensagens=msgs, categoria=cat)


def _detectar_coluna_modalidade(df: pd.DataFrame) -> str | None:
    for cand in ("Modalidade", "MODALIDADE", "modalidade"):
        if cand in df.columns:
            return cand
    return None


def _linha_totalmente_vazia(row: pd.Series, campos: list[str]) -> bool:
    return all(_e_vazio(row[c]) for c in campos)


@dataclass
class _ContextoLinha:
    """Estado acumulado ao validar uma linha da planilha.

    Cada validador do pipeline (:data:`_VALIDADORES_CAMPO`) lê ``row`` /
    ``categoria`` / ``faixas``, grava o valor normalizado em ``reg`` e
    retorna ``False`` para abortar a linha (erro fatal) ou ``True`` para
    seguir ao próximo validador.
    """

    row: pd.Series
    linha: int
    categoria: Categoria
    faixas: dict[str, tuple[int, int]]
    col_modalidade: str | None
    msgs: list[Mensagem] = field(default_factory=list)
    reg: dict[str, str] = field(default_factory=dict)
    ano_formatura: int | None = None

    def erro(self, campo: str, mensagem: str) -> None:
        self.msgs.append(Mensagem(self.linha, NIVEL_ERRO, campo, mensagem))

    def aviso(self, campo: str, mensagem: str) -> None:
        self.msgs.append(Mensagem(self.linha, NIVEL_AVISO, campo, mensagem))

    def info(self, campo: str, mensagem: str) -> None:
        self.msgs.append(Mensagem(self.linha, NIVEL_INFO, campo, mensagem))


def _validar_co_projeto(ctx: _ContextoLinha) -> bool:
    """CO_PROJETO é fixo: qualquer valor divergente na planilha é substituído."""
    valor = _so_digitos(ctx.row["CO_PROJETO"]) if "CO_PROJETO" in ctx.row else ""
    if valor and valor != CO_PROJETO:
        ctx.aviso(
            "CO_PROJETO", f"Valor '{valor}' substituído por {CO_PROJETO} (código fixo do Enade)."
        )
    ctx.reg["CO_PROJETO"] = CO_PROJETO
    return True


def _validar_tp_origem(ctx: _ContextoLinha) -> bool:
    """TP_ORIGEM é fixo em 'E' (IES): qualquer valor divergente é substituído."""
    bruto = ctx.row.get("TP_ORIGEM")
    valor = str(bruto).strip().upper() if not _e_vazio(bruto) else ""
    if valor and valor != TP_ORIGEM:
        ctx.aviso("TP_ORIGEM", f"Valor '{valor}' substituído por '{TP_ORIGEM}' (fixo IES).")
    ctx.reg["TP_ORIGEM"] = TP_ORIGEM
    return True


def _validar_co_ies(ctx: _ContextoLinha) -> bool:
    co_ies = _so_digitos(ctx.row["CO_IES"])
    if not co_ies:
        ctx.erro("CO_IES", "Campo obrigatório não preenchido.")
        return False
    if len(co_ies) > 14:
        ctx.erro("CO_IES", f"Excede 14 dígitos: '{co_ies}'.")
        return False
    ctx.reg["CO_IES"] = co_ies
    return True


def _validar_co_curso(ctx: _ContextoLinha) -> bool:
    co_curso = _so_digitos(ctx.row["CO_CURSO"])
    if not co_curso:
        ctx.erro("CO_CURSO", "Campo obrigatório não preenchido.")
        return False
    if len(co_curso) > 14:
        ctx.erro("CO_CURSO", f"Excede 14 dígitos: '{co_curso}'.")
        return False
    ctx.reg["CO_CURSO"] = co_curso
    return True


def _validar_cpf(ctx: _ContextoLinha) -> bool:
    cpf, aviso_cpf = _normalizar_cpf(ctx.row["NU_CPF"])
    if aviso_cpf:
        ctx.info("NU_CPF", aviso_cpf)
    if not cpf:
        ctx.erro("NU_CPF", "Campo obrigatório não preenchido.")
        return False
    if len(cpf) != 11:
        ctx.erro("NU_CPF", f"CPF deve ter 11 dígitos (encontrado {len(cpf)}: '{cpf}').")
        return False
    if not _cpf_valido(cpf):
        ctx.aviso(
            "NU_CPF", f"CPF '{cpf}' tem dígito verificador inválido (será enviado mesmo assim)."
        )
    ctx.reg["NU_CPF"] = cpf
    return True


def _validar_ano_fim_ensino_medio(ctx: _ContextoLinha) -> bool:
    ano_em = _para_inteiro(ctx.row["NU_ANO_FIM_ENSINO_MEDIO"])
    em_min, em_max = ctx.faixas["NU_ANO_FIM_ENSINO_MEDIO"]
    if ano_em is None:
        ctx.erro(
            "NU_ANO_FIM_ENSINO_MEDIO", "Ano inválido ou em branco (formato esperado: AAAA)."
        )
        return False
    if not (em_min <= ano_em <= em_max):
        ctx.erro(
            "NU_ANO_FIM_ENSINO_MEDIO", f"Ano {ano_em} fora da faixa permitida ({em_min}-{em_max})."
        )
        return False
    ctx.reg["NU_ANO_FIM_ENSINO_MEDIO"] = f"{ano_em:04d}"
    return True


def _validar_turno(ctx: _ContextoLinha) -> bool:
    turno = _para_inteiro(ctx.row["CO_TURNO_GRADUACAO"])
    if turno not in TURNOS_VALIDOS:
        ctx.erro(
            "CO_TURNO_GRADUACAO",
            "Valor inválido. Use 1-Matutino, 2-Vespertino, 3-Integral, 4-Noturno.",
        )
        return False
    ctx.reg["CO_TURNO_GRADUACAO"] = str(turno)
    return True


def _validar_percentual_integralizacao(ctx: _ContextoLinha) -> bool:
    perc = _para_float(ctx.row["NU_PERCENTUAL_INTEGRALIZACAO"])
    if perc is None:
        ctx.erro("NU_PERCENTUAL_INTEGRALIZACAO", "Percentual inválido ou em branco.")
        return False
    if not (0.0 <= perc <= 100.0):
        ctx.erro("NU_PERCENTUAL_INTEGRALIZACAO", f"Percentual {perc} fora da faixa 0-100.")
        return False
    if ctx.categoria.chave == CATEGORIA_INGRESSANTE and perc > 25.0:
        ctx.aviso(
            "NU_PERCENTUAL_INTEGRALIZACAO",
            f"Ingressantes têm até 25% de integralização (encontrado {perc:.2f}%).",
        )
    ctx.reg["NU_PERCENTUAL_INTEGRALIZACAO"] = _formatar_percentual(
        perc, ctx.categoria.casas_decimais_percentual
    )
    return True


def _validar_formatura(ctx: _ContextoLinha) -> bool:
    """NU_ANO_FORMATURA / NU_SEMESTRE_FORMATURA — só para Concluintes e Medicina."""
    if not ctx.categoria.exige_formatura:
        return True

    ano_form_min, ano_form_max = ctx.faixas["NU_ANO_FORMATURA"]
    ano_form = _para_inteiro(ctx.row["NU_ANO_FORMATURA"])
    if ano_form is None:
        ctx.erro("NU_ANO_FORMATURA", "Ano de formatura é obrigatório (formato AAAA).")
        return False
    if not (ano_form_min <= ano_form <= ano_form_max):
        ctx.aviso(
            "NU_ANO_FORMATURA", f"Ano {ano_form} fora da faixa usual ({ano_form_min}-{ano_form_max})."
        )
    ctx.reg["NU_ANO_FORMATURA"] = f"{ano_form:04d}"
    ctx.ano_formatura = ano_form

    sem_form = _para_inteiro(ctx.row["NU_SEMESTRE_FORMATURA"])
    if sem_form not in (1, 2):
        ctx.erro("NU_SEMESTRE_FORMATURA", "Semestre de formatura deve ser 1 ou 2.")
        return False
    ctx.reg["NU_SEMESTRE_FORMATURA"] = str(sem_form)
    return True


def _validar_ano_inicio_graduacao(ctx: _ContextoLinha) -> bool:
    ig_min, ig_max = ctx.faixas["NU_ANO_INICIO_GRADUACAO"]
    ano_ini = _para_inteiro(ctx.row["NU_ANO_INICIO_GRADUACAO"])
    if ano_ini is None:
        ctx.erro("NU_ANO_INICIO_GRADUACAO", "Ano de início da graduação é obrigatório (AAAA).")
        return False
    if not (ig_min <= ano_ini <= ig_max):
        ctx.erro(
            "NU_ANO_INICIO_GRADUACAO", f"Ano {ano_ini} fora da faixa permitida ({ig_min}-{ig_max})."
        )
        return False
    if ctx.ano_formatura is not None and ano_ini > ctx.ano_formatura:
        ctx.aviso(
            "NU_ANO_INICIO_GRADUACAO",
            f"Início ({ano_ini}) posterior à formatura ({ctx.ano_formatura}).",
        )
    ctx.reg["NU_ANO_INICIO_GRADUACAO"] = f"{ano_ini:04d}"
    return True


def _validar_semestre_inicio_graduacao(ctx: _ContextoLinha) -> bool:
    """NU_SEMESTRE_INICIO_GRADUACAO — só para categorias com esse campo (Ingressantes)."""
    if "NU_SEMESTRE_INICIO_GRADUACAO" not in ctx.categoria.campos:
        return True

    sem_ini = _para_inteiro(ctx.row["NU_SEMESTRE_INICIO_GRADUACAO"])
    if sem_ini not in (1, 2):
        ctx.erro("NU_SEMESTRE_INICIO_GRADUACAO", "Semestre de ingresso deve ser 1 ou 2.")
        return False
    ctx.reg["NU_SEMESTRE_INICIO_GRADUACAO"] = str(sem_ini)
    return True


def _validar_polo(ctx: _ContextoLinha) -> bool:
    """IN_MUNICIPIO_POLO_EXTERIOR / CO_MUNICIPIO_POLO — Concluintes e Medicina."""
    if not ctx.categoria.exige_polo:
        return True

    row, col_modalidade = ctx.row, ctx.col_modalidade
    modalidade = (
        str(row[col_modalidade]).strip().lower()
        if col_modalidade and not _e_vazio(row[col_modalidade])
        else ""
    )
    eh_ead = "ead" in modalidade or "distância" in modalidade or "distancia" in modalidade
    eh_presencial = "presencial" in modalidade

    polo_ext = _para_inteiro(row["IN_MUNICIPIO_POLO_EXTERIOR"])
    if polo_ext is None:
        if eh_presencial:
            polo_ext = 0
            ctx.info(
                "IN_MUNICIPIO_POLO_EXTERIOR", "Valor 0 preenchido automaticamente (curso presencial)."
            )
        else:
            ctx.erro(
                "IN_MUNICIPIO_POLO_EXTERIOR",
                "Campo obrigatório. Use 0 (Brasil/presencial) ou 1 (exterior).",
            )
            return False
    if polo_ext not in (0, 1):
        ctx.erro("IN_MUNICIPIO_POLO_EXTERIOR", "Valor inválido. Use apenas 0 ou 1.")
        return False
    ctx.reg["IN_MUNICIPIO_POLO_EXTERIOR"] = str(polo_ext)

    co_polo = _so_digitos(row["CO_MUNICIPIO_POLO"])

    if eh_presencial and co_polo:
        ctx.aviso(
            "CO_MUNICIPIO_POLO", "Curso presencial: campo será omitido (não deve ser informado)."
        )
        co_polo = ""
    elif eh_ead and polo_ext == 0 and not co_polo:
        ctx.erro(
            "CO_MUNICIPIO_POLO",
            "Obrigatório para EaD com polo no Brasil (código IBGE de 7 dígitos).",
        )
        return False
    elif co_polo and polo_ext == 1:
        ctx.aviso("CO_MUNICIPIO_POLO", "Polo no exterior: código será omitido.")
        co_polo = ""

    if co_polo and len(co_polo) != 7:
        ctx.aviso(
            "CO_MUNICIPIO_POLO",
            f"Código IBGE costuma ter 7 dígitos (encontrado {len(co_polo)}: '{co_polo}').",
        )

    ctx.reg["CO_MUNICIPIO_POLO"] = co_polo
    return True


def _validar_origem_enamed(ctx: _ContextoLinha) -> bool:
    """IN_ORIGEM_ENAMED — apenas Medicina 4º ano."""
    if not ctx.categoria.exige_origem_enamed:
        return True

    valor = _para_inteiro(ctx.row["IN_ORIGEM_ENAMED"])
    if valor is None:
        valor = 1
        ctx.info(
            "IN_ORIGEM_ENAMED",
            "Valor 1 preenchido automaticamente (estudante do 4º ano de Medicina – Enamed).",
        )
    elif valor not in (0, 1):
        ctx.erro("IN_ORIGEM_ENAMED", "Valor inválido. Use apenas 0 ou 1.")
        return False
    ctx.reg["IN_ORIGEM_ENAMED"] = str(valor)
    return True


# Pipeline executado em ordem para cada linha da planilha. Estender para um
# novo edital do Enade normalmente significa adicionar/remover/reordenar um
# item aqui, sem tocar nos demais validadores.
_VALIDADORES_CAMPO: list[Callable[[_ContextoLinha], bool]] = [
    _validar_co_projeto,
    _validar_tp_origem,
    _validar_co_ies,
    _validar_co_curso,
    _validar_cpf,
    _validar_ano_fim_ensino_medio,
    _validar_turno,
    _validar_percentual_integralizacao,
    _validar_formatura,
    _validar_ano_inicio_graduacao,
    _validar_semestre_inicio_graduacao,
    _validar_polo,
    _validar_origem_enamed,
]


def _validar_linha(
    *,
    row: pd.Series,
    linha: int,
    categoria: Categoria,
    faixas: dict[str, tuple[int, int]],
    col_modalidade: str | None,
) -> tuple[dict | None, list[Mensagem]]:
    """Executa o pipeline de validadores de campo (:data:`_VALIDADORES_CAMPO`)
    para uma linha da planilha, na ordem definida.
    """
    ctx = _ContextoLinha(
        row=row, linha=linha, categoria=categoria, faixas=faixas, col_modalidade=col_modalidade
    )
    for validador in _VALIDADORES_CAMPO:
        if not validador(ctx):
            return None, ctx.msgs
    return ctx.reg, ctx.msgs


# ---------------------------------------------------------------------------
# Geração do arquivo
# ---------------------------------------------------------------------------


def montar_nome_arquivo(
    co_curso: str,
    data: datetime,
    sequencia: int,
    categoria: str | Categoria,
) -> str:
    cat = categoria if isinstance(categoria, Categoria) else categoria_por_chave(categoria)
    return (
        f"{NO_PROGRAMA}{CO_PROJETO}_{cat.identificacao_arquivo}_{UF_FIXA}_"
        f"{data.strftime('%d%m%Y')}_{co_curso}_{TP_ORIGEM}{sequencia:03d}.TXT"
    )


def montar_conteudo(df_norm: pd.DataFrame, categoria: str | Categoria) -> str:
    cat = categoria if isinstance(categoria, Categoria) else categoria_por_chave(categoria)
    linhas: list[str] = [";".join(cat.campos)]
    for _, row in df_norm.iterrows():
        valores = [str(row[c]) if not _e_vazio(row[c]) else "" for c in cat.campos]
        linhas.append(";".join(valores))
    return "\r\n".join(linhas) + "\r\n"


def gerar_arquivo(
    df_norm: pd.DataFrame,
    pasta_saida: str | Path,
    co_curso: str,
    data: datetime,
    sequencia: int,
    categoria: str | Categoria,
    encoding: str = "utf-8",
) -> Path:
    """Grava o arquivo .txt e retorna o caminho final."""
    cat = categoria if isinstance(categoria, Categoria) else categoria_por_chave(categoria)
    pasta = Path(pasta_saida)
    pasta.mkdir(parents=True, exist_ok=True)

    nome = montar_nome_arquivo(
        co_curso=co_curso, data=data, sequencia=sequencia, categoria=cat
    )
    caminho = pasta / nome
    conteudo = montar_conteudo(df_norm, categoria=cat)
    caminho.write_text(conteudo, encoding=encoding, newline="")
    return caminho


def gerar_arquivo_zip(
    df_norm: pd.DataFrame,
    pasta_saida: str | Path,
    co_curso: str,
    data: datetime,
    sequencia: int,
    categoria: str | Categoria,
    encoding: str = "utf-8",
) -> Path:
    """Grava o .TXT e empacota em .ZIP (mesmo nome, extensão .ZIP)."""
    caminho_txt = gerar_arquivo(
        df_norm=df_norm,
        pasta_saida=pasta_saida,
        co_curso=co_curso,
        data=data,
        sequencia=sequencia,
        categoria=categoria,
        encoding=encoding,
    )
    caminho_zip = caminho_txt.with_suffix(".ZIP")
    with zipfile.ZipFile(
        caminho_zip, "w", compression=zipfile.ZIP_DEFLATED
    ) as zf:
        zf.write(caminho_txt, arcname=caminho_txt.name)
    return caminho_zip


def descobrir_co_curso(df_norm: pd.DataFrame) -> str:
    """Determina o código do curso a usar no nome do arquivo.

    Se houver um único CO_CURSO, retorna-o. Se houver múltiplos, retorna
    o primeiro (a UI alertará o usuário).
    """
    if df_norm.empty or "CO_CURSO" not in df_norm.columns:
        return ""
    cursos = df_norm["CO_CURSO"].dropna().unique().tolist()
    return cursos[0] if cursos else ""


def cursos_distintos(df_norm: pd.DataFrame) -> list[str]:
    if df_norm.empty or "CO_CURSO" not in df_norm.columns:
        return []
    return sorted(set(df_norm["CO_CURSO"].dropna().tolist()))


__all__ = [
    "ANOS_FORMATURA_HORIZONTE",
    "CATEGORIA_MEDICINA",
    "Mensagem",
    "NIVEL_AVISO",
    "NIVEL_ERRO",
    "NIVEL_INFO",
    "ResultadoValidacao",
    "EXTENSOES_PLANILHA",
    "MAX_PLANILHA_DESCOMPRIMIDA_MB",
    "carregar_planilha",
    "extensao_planilha_suportada",
    "cursos_distintos",
    "descobrir_co_curso",
    "gerar_arquivo",
    "gerar_arquivo_zip",
    "montar_conteudo",
    "montar_nome_arquivo",
    "validar_dataframe",
    "validar_planilha_segura",
]
