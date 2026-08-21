FROM python:3.12-slim

# Não escreve .pyc, saída sem buffer, sem cache do pip.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Instala dependências primeiro (melhor cache de camadas).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia apenas o necessário para rodar (NÃO copia planilhas/PII).
COPY app/ ./app/
COPY static/ ./static/
COPY templates/ ./templates/
COPY web_app.py .

# Usuário não-privilegiado.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# Healthcheck usando a rota /health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3).status==200 else sys.exit(1)"

# workers/threads/timeout configuráveis por ambiente.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:5000 --workers ${GUNICORN_WORKERS:-2} --timeout ${GUNICORN_TIMEOUT:-120} --access-logfile - --error-logfile - web_app:app"]
