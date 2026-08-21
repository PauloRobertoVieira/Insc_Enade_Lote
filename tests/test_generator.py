"""Testes da lógica de validação e geração (app.generator).

Cobrem as regras de negócio críticas: validação de CPF, normalização de
zeros à esquerda, faixas de ano, turnos e a montagem do arquivo oficial.
"""

from __future__ import annotations

import zipfile
from datetime import datetime

import pandas as pd
import pytest

from app.config import CATEGORIA_CONCLUINTE, CATEGORIA_INGRESSANTE
from app.generator import (
    MAX_PLANILHA_DESCOMPRIMIDA_MB,
    _cpf_valido,
    _normalizar_cpf,
    carregar_planilha,
    extensao_planilha_suportada,
    gerar_arquivo_zip,
    montar_conteudo,
    montar_nome_arquivo,
    validar_dataframe,
    validar_planilha_segura,
)

CPF_VALIDO = "52998224725"  # CPF com dígitos verificadores corretos


# ---------------------------------------------------------------------------
# CPF
# ---------------------------------------------------------------------------


def test_cpf_valido_reconhece_cpf_correto():
    assert _cpf_valido(CPF_VALIDO) is True


@pytest.mark.parametrize(
    "cpf",
    ["11111111111", "12345678900", "0000000000", "5299822472"],
)
def test_cpf_valido_rejeita_invalidos(cpf):
    assert _cpf_valido(cpf) is False


def test_normalizar_cpf_completa_zeros_a_esquerda():
    cpf, aviso = _normalizar_cpf(4159516157)  # 10 dígitos (perdeu zero)
    assert cpf == "04159516157"
    assert aviso is not None


def test_normalizar_cpf_vazio():
    cpf, aviso = _normalizar_cpf("")
    assert cpf == ""
    assert aviso is None


def test_normalizar_cpf_remove_pontuacao():
    cpf, _ = _normalizar_cpf("529.982.247-25")
    assert cpf == CPF_VALIDO


# ---------------------------------------------------------------------------
# Extensões
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nome", ["a.xlsx", "b.XLS", "c.csv"])
def test_extensao_suportada(nome):
    assert extensao_planilha_suportada(nome) is True


@pytest.mark.parametrize("nome", ["a.txt", "b.exe", "c.pdf", "semext"])
def test_extensao_nao_suportada(nome):
    assert extensao_planilha_suportada(nome) is False


# ---------------------------------------------------------------------------
# Validação de DataFrame
# ---------------------------------------------------------------------------


def _linha_concluinte_valida() -> dict:
    return {
        "CO_PROJETO": "2611101",
        "TP_ORIGEM": "E",
        "CO_IES": "2",
        "CO_CURSO": "123456",
        "NU_CPF": CPF_VALIDO,
        "NU_ANO_FIM_ENSINO_MEDIO": "2018",
        "CO_TURNO_GRADUACAO": "3",
        "NU_PERCENTUAL_INTEGRALIZACAO": "85.5",
        "NU_ANO_FORMATURA": "2026",
        "NU_SEMESTRE_FORMATURA": "1",
        "NU_ANO_INICIO_GRADUACAO": "2021",
        "IN_MUNICIPIO_POLO_EXTERIOR": "0",
        "CO_MUNICIPIO_POLO": "",
        "Modalidade": "Presencial",
    }


def test_validar_linha_concluinte_valida():
    df = pd.DataFrame([_linha_concluinte_valida()])
    res = validar_dataframe(df, categoria=CATEGORIA_CONCLUINTE, ano_referencia=2026)
    assert res.valido is True
    assert len(res.df_normalizado) == 1
    assert res.df_normalizado.iloc[0]["NU_CPF"] == CPF_VALIDO


def test_colunas_obrigatorias_ausentes_geram_erro():
    df = pd.DataFrame([{"CO_IES": "2"}])
    res = validar_dataframe(df, categoria=CATEGORIA_CONCLUINTE, ano_referencia=2026)
    assert res.valido is False
    assert any(m.nivel == "ERRO" for m in res.mensagens)


def test_turno_invalido_descarta_linha():
    linha = _linha_concluinte_valida()
    linha["CO_TURNO_GRADUACAO"] = "9"
    df = pd.DataFrame([linha])
    res = validar_dataframe(df, categoria=CATEGORIA_CONCLUINTE, ano_referencia=2026)
    assert res.df_normalizado.empty
    assert any(m.campo == "CO_TURNO_GRADUACAO" and m.nivel == "ERRO" for m in res.mensagens)


def test_ano_fora_da_faixa_descarta_linha():
    linha = _linha_concluinte_valida()
    linha["NU_ANO_FIM_ENSINO_MEDIO"] = "1800"
    df = pd.DataFrame([linha])
    res = validar_dataframe(df, categoria=CATEGORIA_CONCLUINTE, ano_referencia=2026)
    assert res.df_normalizado.empty


def test_percentual_fora_da_faixa_descarta_linha():
    linha = _linha_concluinte_valida()
    linha["NU_PERCENTUAL_INTEGRALIZACAO"] = "150"
    df = pd.DataFrame([linha])
    res = validar_dataframe(df, categoria=CATEGORIA_CONCLUINTE, ano_referencia=2026)
    assert res.df_normalizado.empty


def test_ingressante_layout_menor():
    linha = {
        "CO_PROJETO": "2611101",
        "TP_ORIGEM": "E",
        "CO_IES": "2",
        "CO_CURSO": "123456",
        "NU_CPF": CPF_VALIDO,
        "NU_ANO_FIM_ENSINO_MEDIO": "2023",
        "CO_TURNO_GRADUACAO": "1",
        "NU_PERCENTUAL_INTEGRALIZACAO": "10.0",
        "NU_ANO_INICIO_GRADUACAO": "2025",
        "NU_SEMESTRE_INICIO_GRADUACAO": "1",
    }
    df = pd.DataFrame([linha])
    res = validar_dataframe(df, categoria=CATEGORIA_INGRESSANTE, ano_referencia=2026)
    assert res.valido is True


# ---------------------------------------------------------------------------
# Geração de arquivo
# ---------------------------------------------------------------------------


def test_montar_nome_arquivo():
    nome = montar_nome_arquivo(
        co_curso="123456",
        data=datetime(2026, 6, 4),
        sequencia=1,
        categoria=CATEGORIA_CONCLUINTE,
    )
    assert nome == "ENADE2611101_N99_BR_04062026_123456_E001.TXT"


def test_montar_conteudo_usa_cabecalho_e_ponto_e_virgula():
    df = pd.DataFrame([_linha_concluinte_valida()])
    res = validar_dataframe(df, categoria=CATEGORIA_CONCLUINTE, ano_referencia=2026)
    conteudo = montar_conteudo(res.df_normalizado, categoria=CATEGORIA_CONCLUINTE)
    linhas = conteudo.strip().split("\r\n")
    assert linhas[0].startswith("CO_PROJETO;TP_ORIGEM;")
    assert ";" in linhas[1]


def test_gerar_arquivo_zip_contem_txt(tmp_path):
    df = pd.DataFrame([_linha_concluinte_valida()])
    res = validar_dataframe(df, categoria=CATEGORIA_CONCLUINTE, ano_referencia=2026)
    caminho_zip = gerar_arquivo_zip(
        df_norm=res.df_normalizado,
        pasta_saida=tmp_path,
        co_curso="123456",
        data=datetime(2026, 6, 4),
        sequencia=1,
        categoria=CATEGORIA_CONCLUINTE,
    )
    assert caminho_zip.suffix == ".ZIP"
    with zipfile.ZipFile(caminho_zip) as zf:
        nomes = zf.namelist()
    assert len(nomes) == 1
    assert nomes[0].endswith(".TXT")


def test_carregar_planilha_csv(tmp_path):
    csv = tmp_path / "dados.csv"
    csv.write_text("CO_IES;CO_CURSO\n2;123456\n", encoding="utf-8")
    df = carregar_planilha(csv)
    assert list(df.columns) == ["CO_IES", "CO_CURSO"]
    assert len(df) == 1


# ---------------------------------------------------------------------------
# Proteção contra zip bomb
# ---------------------------------------------------------------------------


def test_validar_planilha_segura_aceita_xlsx_legitimo(tmp_path):
    df = pd.DataFrame([_linha_concluinte_valida()])
    caminho = tmp_path / "planilha.xlsx"
    df.to_excel(caminho, index=False)
    validar_planilha_segura(caminho)  # não deve levantar


def test_validar_planilha_segura_ignora_csv(tmp_path):
    csv = tmp_path / "dados.csv"
    csv.write_text("CO_IES;CO_CURSO\n2;123456\n", encoding="utf-8")
    validar_planilha_segura(csv)  # não deve levantar


def test_validar_planilha_segura_ignora_xls(tmp_path):
    # .xls (OLE binário) não é um zip; não deve ser tratado como tal.
    xls = tmp_path / "dados.xls"
    xls.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
    validar_planilha_segura(xls)  # não deve levantar


def test_validar_planilha_segura_rejeita_zip_bomb(tmp_path):
    caminho = tmp_path / "bomba.xlsx"
    conteudo_grande = b"0" * (MAX_PLANILHA_DESCOMPRIMIDA_MB * 1024 * 1024 + 1)
    with zipfile.ZipFile(caminho, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", conteudo_grande)

    with pytest.raises(ValueError, match="rejeitado"):
        validar_planilha_segura(caminho)


def test_validar_planilha_segura_rejeita_arquivo_corrompido(tmp_path):
    caminho = tmp_path / "corrompido.xlsx"
    caminho.write_bytes(b"nao-e-um-zip-valido")

    with pytest.raises(ValueError, match="não é um .xlsx válido"):
        validar_planilha_segura(caminho)
