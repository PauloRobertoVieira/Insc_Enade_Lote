"""Servidor web Flask para o ENADE – Gerar Arquivo Cadastro em Lote.

Reusa integralmente os módulos ``app.config`` e ``app.generator`` já existentes,
expondo a funcionalidade via rotas HTTP para o frontend no navegador.

A configuração sensível (secret key, credenciais, limites) é lida de variáveis
de ambiente, permitindo um deploy seguro sem alterar código. Consulte o
``.env.example`` e o README para a referência completa.

Execução local (desenvolvimento):
    python web_app.py

Execução em produção (via Gunicorn/Docker):
    gunicorn --bind 0.0.0.0:5000 web_app:app
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
    after_this_request,
    jsonify,
    render_template,
    request,
    send_file,
)

from app import __version__
from app.config import (
    CATEGORIAS,
    CO_PROJETO,
    NO_PROGRAMA,
    TP_ORIGEM,
    UF_FIXA,
    Categoria,
    ano_corrente,
    categoria_por_chave,
)
from app.generator import (
    EXTENSOES_PLANILHA,
    ResultadoValidacao,
    carregar_planilha,
    cursos_distintos,
    descobrir_co_curso,
    extensao_planilha_suportada,
    gerar_arquivo_zip,
    validar_dataframe,
    validar_planilha_segura,
)

# ---------------------------------------------------------------------------
# Configuração via ambiente
# ---------------------------------------------------------------------------


def _env_bool(nome: str, padrao: bool = False) -> bool:
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "yes", "on", "sim")


def _env_int(nome: str, padrao: int) -> int:
    try:
        return int(os.environ.get(nome, padrao))
    except (TypeError, ValueError):
        return padrao


DEBUG = _env_bool("FLASK_DEBUG", False)
MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 16)
# Credenciais opcionais de HTTP Basic Auth. Quando ambas estão definidas,
# todas as rotas passam a exigir autenticação.
AUTH_USER = os.environ.get("APP_AUTH_USERNAME", "").strip()
AUTH_PASS = os.environ.get("APP_AUTH_PASSWORD", "")
# Confia em cabeçalhos X-Forwarded-* apenas quando atrás de um proxy conhecido.
BEHIND_PROXY = _env_bool("BEHIND_PROXY", False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("enade.web")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = Flask(__name__)
# SECRET_KEY vem do ambiente em produção; gera uma efêmera se ausente (dev).
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["JSON_SORT_KEYS"] = False

if BEHIND_PROXY:
    # Corrige request.remote_addr / esquema quando servido atrás de nginx.
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

# Rate limiting (defense-in-depth; o proxy deve limitar também).
#
# ATENÇÃO — storage_uri "memory://" (padrão) mantém os contadores isolados
# por processo. Com Gunicorn rodando com mais de 1 worker (GUNICORN_WORKERS),
# cada worker aplica o limite de forma independente, então o limite efetivo
# vira (limite configurado × nº de workers), distribuído de forma imprevisível
# entre as requisições. Em produção com múltiplos workers, configure
# RATE_LIMIT_STORAGE=redis://host:6379 para um contador compartilhado.
_RATE_STORAGE = os.environ.get("RATE_LIMIT_STORAGE", "memory://")
_GUNICORN_WORKERS = _env_int("GUNICORN_WORKERS", 1)

if _RATE_STORAGE.startswith("memory://") and _GUNICORN_WORKERS > 1:
    logger.warning(
        "RATE_LIMIT_STORAGE=memory:// com GUNICORN_WORKERS=%d: o rate limit "
        "NÃO é compartilhado entre processos — o limite efetivo pode chegar a "
        "%d× o valor configurado. Configure RATE_LIMIT_STORAGE=redis://... "
        "para um limite consistente em produção.",
        _GUNICORN_WORKERS,
        _GUNICORN_WORKERS,
    )

limiter: Limiter | None
_RATE_UPLOAD: str | None

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[os.environ.get("RATE_LIMIT_DEFAULT", "120 per minute")],
        storage_uri=_RATE_STORAGE,
    )
    _RATE_UPLOAD = os.environ.get("RATE_LIMIT_UPLOAD", "20 per minute")
except Exception:  # pragma: no cover - limiter é opcional
    limiter = None
    _RATE_UPLOAD = None
    logger.warning("flask-limiter indisponível; rate limiting desativado no app.")


def _rate_limit(regra):
    """Decorator no-op quando o limiter não está disponível."""
    if limiter is not None and regra:
        return limiter.limit(regra)

    def _identity(func):
        return func

    return _identity


if AUTH_USER and AUTH_PASS:
    logger.info("Autenticação HTTP Basic ATIVADA.")
else:
    logger.warning(
        "Autenticação DESATIVADA (APP_AUTH_USERNAME/APP_AUTH_PASSWORD não definidos). "
        "Não exponha esta instância na internet pública sem autenticação."
    )


# ---------------------------------------------------------------------------
# Autenticação (HTTP Basic opcional)
# ---------------------------------------------------------------------------


def _auth_ok(auth) -> bool:
    if not (AUTH_USER and AUTH_PASS):
        return True
    if auth is None:
        return False
    usuario_ok = secrets.compare_digest(auth.username or "", AUTH_USER)
    senha_ok = secrets.compare_digest(auth.password or "", AUTH_PASS)
    return usuario_ok and senha_ok


def requer_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _auth_ok(request.authorization):
            resp = Response(
                "Autenticação necessária.",
                401,
                {"WWW-Authenticate": 'Basic realm="ENADE Cadastro em Lote"'},
            )
            return resp
        return func(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Cabeçalhos de segurança
# ---------------------------------------------------------------------------


@app.after_request
def _headers_seguranca(resp: Response) -> Response:
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    # HSTS só faz sentido sob HTTPS (proxy/produção).
    if request.is_secure or BEHIND_PROXY:
        resp.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return resp


@app.errorhandler(413)
def _payload_grande(_e):
    return (
        jsonify({"erro": f"Arquivo excede o limite de {MAX_UPLOAD_MB} MB."}),
        413,
    )


def _salvar_upload_temporario(arquivo) -> Path:
    """Persiste o upload com a extensão original (.xlsx, .xls ou .csv)."""
    nome = arquivo.filename or ""
    ext = Path(nome).suffix.lower()
    if not extensao_planilha_suportada(nome):
        extensoes = ", ".join(sorted(EXTENSOES_PLANILHA))
        raise ValueError(f"Formato não suportado. Envie um arquivo: {extensoes}")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        arquivo.save(tmp)
        return Path(tmp.name)


class _ErroRequisicao(Exception):
    """Erro de entrada do usuário, pronto para virar resposta JSON."""

    def __init__(self, mensagem: str, status: int = 400) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


@dataclass
class _UploadValidado:
    categoria: Categoria
    ano_referencia: int
    resultado: ResultadoValidacao
    total_linhas: int


def _carregar_e_validar(req) -> _UploadValidado:
    """Lê o upload + parâmetros comuns (``arquivo``, ``categoria``,
    ``ano_referencia``) e retorna o resultado da validação.

    Centraliza o fluxo compartilhado por ``/api/validar`` e ``/api/gerar``;
    levanta :class:`_ErroRequisicao` (mensagem + status HTTP) para qualquer
    falha de entrada, que o chamador converte em ``jsonify``.
    """
    if "arquivo" not in req.files:
        raise _ErroRequisicao("Nenhum arquivo enviado.")

    arquivo = req.files["arquivo"]
    if not arquivo.filename:
        raise _ErroRequisicao("Nome de arquivo vazio.")

    categoria_chave = req.form.get("categoria", "concluinte")
    ano_referencia_str = req.form.get("ano_referencia", str(ano_corrente()))

    try:
        ano_referencia = int(ano_referencia_str)
    except ValueError as exc:
        raise _ErroRequisicao(f"Ano de referência inválido: {ano_referencia_str}") from exc

    try:
        cat = categoria_por_chave(categoria_chave)
    except KeyError as exc:
        raise _ErroRequisicao(f"Categoria desconhecida: {categoria_chave}") from exc

    try:
        tmp_path = _salvar_upload_temporario(arquivo)
    except ValueError as exc:
        raise _ErroRequisicao(str(exc)) from exc

    try:
        try:
            validar_planilha_segura(tmp_path)
            df = carregar_planilha(tmp_path)
            resultado = validar_dataframe(df, categoria=cat, ano_referencia=ano_referencia)
        except ValueError as exc:
            raise _ErroRequisicao(str(exc)) from exc
        except Exception as exc:
            logger.exception("Falha ao processar planilha em %s", req.path)
            raise _ErroRequisicao(
                "Não foi possível processar a planilha. Verifique o formato e as colunas."
            ) from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return _UploadValidado(
        categoria=cat, ano_referencia=ano_referencia, resultado=resultado, total_linhas=len(df)
    )


# ---------------------------------------------------------------------------
# Health check (para load balancer / orquestrador) — sem auth
# ---------------------------------------------------------------------------


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": __version__})


# ---------------------------------------------------------------------------
# Rotas de página
# ---------------------------------------------------------------------------


@app.route("/")
@requer_auth
def index():
    """Serve a página principal da aplicação."""
    return render_template("index.html", version=__version__)


# ---------------------------------------------------------------------------
# API — Categorias
# ---------------------------------------------------------------------------


@app.route("/api/categorias", methods=["GET"])
@requer_auth
def api_categorias():
    """Retorna a lista de categorias disponíveis com seus metadados."""
    resultado = []
    for cat in CATEGORIAS.values():
        resultado.append(
            {
                "chave": cat.chave,
                "rotulo": cat.rotulo,
                "identificacao_arquivo": cat.identificacao_arquivo,
                "anexo": cat.anexo,
                "campos": cat.campos,
                "casas_decimais_percentual": cat.casas_decimais_percentual,
                "exige_formatura": cat.exige_formatura,
                "exige_polo": cat.exige_polo,
                "exige_origem_enamed": cat.exige_origem_enamed,
            }
        )
    return jsonify(
        {
            "categorias": resultado,
            "ano_corrente": ano_corrente(),
            # Constantes fixas do nome do arquivo — fonte única em app/config.py,
            # evita duplicá-las "à mão" no frontend (static/app.js).
            "co_projeto": CO_PROJETO,
            "no_programa": NO_PROGRAMA,
            "uf": UF_FIXA,
            "tp_origem": TP_ORIGEM,
        }
    )


# ---------------------------------------------------------------------------
# API — Validar planilha
# ---------------------------------------------------------------------------


@app.route("/api/validar", methods=["POST"])
@requer_auth
@_rate_limit(_RATE_UPLOAD)
def api_validar():
    """Recebe upload de planilha + categoria e retorna resultado da validação."""
    try:
        upload = _carregar_e_validar(request)
    except _ErroRequisicao as exc:
        return jsonify({"erro": exc.mensagem}), exc.status

    cat = upload.categoria
    resultado = upload.resultado

    # Montar resposta
    co_curso_detectado = descobrir_co_curso(resultado.df_normalizado)
    cursos = cursos_distintos(resultado.df_normalizado)

    # Preview dos dados (até 200 linhas para a tabela)
    dados_preview = []
    for _, row in resultado.df_normalizado.head(200).iterrows():
        dados_preview.append(
            {campo: str(row.get(campo, "")) for campo in cat.campos}
        )

    mensagens = []
    for m in resultado.mensagens:
        mensagens.append(
            {
                "linha": m.linha,
                "nivel": m.nivel,
                "campo": m.campo,
                "mensagem": m.mensagem,
            }
        )

    return jsonify(
        {
            "valido": resultado.valido,
            "linhas_validas": len(resultado.df_normalizado),
            "total_linhas": upload.total_linhas,
            "campos": cat.campos,
            "dados_preview": dados_preview,
            "mensagens": mensagens,
            "erros": len(resultado.erros),
            "avisos": len(resultado.avisos),
            "info": len(resultado.info),
            "co_curso_detectado": co_curso_detectado,
            "cursos_distintos": cursos,
        }
    )


# ---------------------------------------------------------------------------
# API — Gerar arquivo .ZIP (contém o .TXT)
# ---------------------------------------------------------------------------


@app.route("/api/gerar", methods=["POST"])
@requer_auth
@_rate_limit(_RATE_UPLOAD)
def api_gerar():
    """Valida a planilha e gera o arquivo .ZIP (com o .TXT dentro) para download."""
    data_str = request.form.get("data", datetime.now().strftime("%d%m%Y"))
    sequencia_str = request.form.get("sequencia", "1")
    co_curso_override = request.form.get("co_curso", "").strip()
    encoding = request.form.get("encoding", "utf-8")

    try:
        data = datetime.strptime(data_str, "%d%m%Y")
    except ValueError:
        return jsonify({"erro": f"Data inválida: {data_str}. Use DDMMAAAA."}), 400

    try:
        sequencia = int(sequencia_str)
        if not 1 <= sequencia <= 999:
            raise ValueError
    except ValueError:
        return jsonify({"erro": "Sequência deve ser um número entre 1 e 999."}), 400

    if encoding not in ("utf-8", "cp1252"):
        encoding = "utf-8"

    if co_curso_override and not co_curso_override.isdigit():
        return jsonify({"erro": "CO_CURSO deve conter apenas dígitos."}), 400

    try:
        upload = _carregar_e_validar(request)
    except _ErroRequisicao as exc:
        return jsonify({"erro": exc.mensagem}), exc.status

    cat = upload.categoria
    resultado = upload.resultado

    if resultado.df_normalizado.empty:
        return jsonify({"erro": "Nenhuma linha válida para gerar o arquivo."}), 400

    co_curso = co_curso_override or descobrir_co_curso(resultado.df_normalizado)
    if not co_curso or not co_curso.isdigit():
        return (
            jsonify({"erro": "CO_CURSO inválido. Informe um código numérico."}),
            400,
        )

    # Gerar em pasta temporária
    pasta_tmp = Path(tempfile.mkdtemp())
    try:
        caminho = gerar_arquivo_zip(
            df_norm=resultado.df_normalizado,
            pasta_saida=pasta_tmp,
            co_curso=co_curso,
            data=data,
            sequencia=sequencia,
            categoria=cat,
            encoding=encoding,
        )

        @after_this_request
        def _limpar_pasta_temporaria(response):
            shutil.rmtree(pasta_tmp, ignore_errors=True)
            return response

        return send_file(
            str(caminho),
            as_attachment=True,
            download_name=caminho.name,
            mimetype="application/zip",
        )
    except Exception:
        shutil.rmtree(pasta_tmp, ignore_errors=True)
        logger.exception("Falha ao gerar arquivo em /api/gerar")
        return jsonify({"erro": "Erro ao gerar o arquivo."}), 500


# ---------------------------------------------------------------------------
# Inicialização (apenas desenvolvimento local)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"\n  ENADE Cadastro em Lote — Web  v{__version__}")
    print("  Abra no navegador: http://localhost:5000\n")
    # host padrão 127.0.0.1 (somente local). Ajuste via HOST/PORT se necessário.
    host = os.environ.get("HOST", "127.0.0.1")
    port = _env_int("PORT", 5000)
    app.run(debug=DEBUG, host=host, port=port)
