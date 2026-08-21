"""Testes de integração das rotas Flask (web_app.py).

Cobrem autenticação HTTP Basic, uploads inválidos, cabeçalhos de segurança
e o limite de tamanho de upload — fluxos que tests/test_generator.py não
alcança por testar apenas a lógica pura de validação/geração.
"""

from __future__ import annotations

import importlib
import io

import pandas as pd
import pytest

import web_app as web_app_module

_LINHA_CONCLUINTE_VALIDA = {
    "CO_PROJETO": "2611101",
    "TP_ORIGEM": "E",
    "CO_IES": "2",
    "CO_CURSO": "123456",
    "NU_CPF": "52998224725",
    "NU_ANO_FIM_ENSINO_MEDIO": "2018",
    "CO_TURNO_GRADUACAO": "3",
    "NU_PERCENTUAL_INTEGRALIZACAO": "85.5",
    "NU_ANO_FORMATURA": "2026",
    "NU_SEMESTRE_FORMATURA": "1",
    "NU_ANO_INICIO_GRADUACAO": "2021",
    "IN_MUNICIPIO_POLO_EXTERIOR": "0",
    "CO_MUNICIPIO_POLO": "",
}


def _planilha_xlsx_bytes(linhas: list[dict]) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(linhas).to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Fixtures — recarregam web_app.py para que as variáveis de ambiente lidas
# no import (AUTH_USER/AUTH_PASS, GUNICORN_WORKERS etc.) reflitam o cenário
# de cada teste.
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_sem_auth(monkeypatch):
    monkeypatch.delenv("APP_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)
    importlib.reload(web_app_module)
    web_app_module.app.config.update(TESTING=True)
    yield web_app_module.app


@pytest.fixture()
def client(app_sem_auth):
    return app_sem_auth.test_client()


@pytest.fixture()
def app_com_auth(monkeypatch):
    monkeypatch.setenv("APP_AUTH_USERNAME", "coordenador")
    monkeypatch.setenv("APP_AUTH_PASSWORD", "senha-forte")
    importlib.reload(web_app_module)
    web_app_module.app.config.update(TESTING=True)
    yield web_app_module.app
    # Restaura o módulo para o estado "sem auth" usado pelos demais testes.
    monkeypatch.delenv("APP_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    importlib.reload(web_app_module)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def test_health_nao_exige_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Autenticação HTTP Basic
# ---------------------------------------------------------------------------


def test_index_sem_auth_configurada_libera_acesso(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_index_com_auth_configurada_exige_credenciais(app_com_auth):
    resp = app_com_auth.test_client().get("/")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_index_com_auth_configurada_aceita_credenciais_corretas(app_com_auth):
    resp = app_com_auth.test_client().get("/", auth=("coordenador", "senha-forte"))
    assert resp.status_code == 200


def test_index_com_auth_configurada_rejeita_credenciais_erradas(app_com_auth):
    resp = app_com_auth.test_client().get("/", auth=("coordenador", "senha-errada"))
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Cabeçalhos de segurança
# ---------------------------------------------------------------------------


def test_headers_de_seguranca_presentes(client):
    resp = client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# API — Categorias
# ---------------------------------------------------------------------------


def test_api_categorias_retorna_tres_categorias(client):
    resp = client.get("/api/categorias")
    assert resp.status_code == 200
    data = resp.get_json()
    assert {c["chave"] for c in data["categorias"]} == {
        "concluinte",
        "ingressante",
        "medicina4",
    }


def test_api_categorias_expoe_constantes_fixas_do_nome_de_arquivo(client):
    resp = client.get("/api/categorias")
    data = resp.get_json()
    assert data["co_projeto"] == "2611101"
    assert data["no_programa"] == "ENADE"
    assert data["uf"] == "BR"
    assert data["tp_origem"] == "E"


# ---------------------------------------------------------------------------
# API — Validar
# ---------------------------------------------------------------------------


def test_api_validar_sem_arquivo(client):
    resp = client.post("/api/validar", data={"categoria": "concluinte"})
    assert resp.status_code == 400
    assert "Nenhum arquivo" in resp.get_json()["erro"]


def test_api_validar_extensao_nao_suportada(client):
    data = {
        "arquivo": (io.BytesIO(b"conteudo"), "planilha.pdf"),
        "categoria": "concluinte",
    }
    resp = client.post("/api/validar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Formato não suportado" in resp.get_json()["erro"]


def test_api_validar_categoria_desconhecida(client):
    xlsx = _planilha_xlsx_bytes([_LINHA_CONCLUINTE_VALIDA])
    data = {
        "arquivo": (io.BytesIO(xlsx), "planilha.xlsx"),
        "categoria": "categoria-que-nao-existe",
    }
    resp = client.post("/api/validar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Categoria desconhecida" in resp.get_json()["erro"]


def test_api_validar_planilha_valida(client):
    xlsx = _planilha_xlsx_bytes([_LINHA_CONCLUINTE_VALIDA])
    data = {
        "arquivo": (io.BytesIO(xlsx), "planilha.xlsx"),
        "categoria": "concluinte",
        "ano_referencia": "2026",
    }
    resp = client.post("/api/validar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["valido"] is True
    assert body["linhas_validas"] == 1


# ---------------------------------------------------------------------------
# API — Gerar
# ---------------------------------------------------------------------------


def test_api_gerar_planilha_valida_retorna_zip(client):
    xlsx = _planilha_xlsx_bytes([_LINHA_CONCLUINTE_VALIDA])
    data = {
        "arquivo": (io.BytesIO(xlsx), "planilha.xlsx"),
        "categoria": "concluinte",
        "ano_referencia": "2026",
        "data": "04062026",
        "sequencia": "1",
        "co_curso": "123456",
        "encoding": "utf-8",
    }
    resp = client.post("/api/gerar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    assert resp.headers["Content-Disposition"].startswith("attachment")


def test_api_gerar_data_invalida(client):
    xlsx = _planilha_xlsx_bytes([_LINHA_CONCLUINTE_VALIDA])
    data = {
        "arquivo": (io.BytesIO(xlsx), "planilha.xlsx"),
        "categoria": "concluinte",
        "data": "31-02-2026",
    }
    resp = client.post("/api/gerar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Data inválida" in resp.get_json()["erro"]


def test_api_gerar_co_curso_nao_numerico(client):
    xlsx = _planilha_xlsx_bytes([_LINHA_CONCLUINTE_VALIDA])
    data = {
        "arquivo": (io.BytesIO(xlsx), "planilha.xlsx"),
        "categoria": "concluinte",
        "co_curso": "abc123",
    }
    resp = client.post("/api/gerar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "CO_CURSO" in resp.get_json()["erro"]


def test_api_gerar_sem_linhas_validas(client):
    linha_invalida = dict(_LINHA_CONCLUINTE_VALIDA, CO_TURNO_GRADUACAO="9")
    xlsx = _planilha_xlsx_bytes([linha_invalida])
    data = {
        "arquivo": (io.BytesIO(xlsx), "planilha.xlsx"),
        "categoria": "concluinte",
        "co_curso": "123456",
    }
    resp = client.post("/api/gerar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "Nenhuma linha válida" in resp.get_json()["erro"]


# ---------------------------------------------------------------------------
# Limite de upload (413)
# ---------------------------------------------------------------------------


def test_upload_excede_limite_retorna_413(app_sem_auth):
    app_sem_auth.config["MAX_CONTENT_LENGTH"] = 1024  # 1 KB só para este teste
    client = app_sem_auth.test_client()
    payload_grande = b"0" * 4096
    data = {
        "arquivo": (io.BytesIO(payload_grande), "planilha.xlsx"),
        "categoria": "concluinte",
    }
    resp = client.post("/api/validar", data=data, content_type="multipart/form-data")
    assert resp.status_code == 413
    assert "erro" in resp.get_json()
