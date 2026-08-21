# ENADE — Cadastro em Lote (Web)

Aplicação web para geração de arquivos `.TXT` de inscrição em lote no **Sistema ENADE/INEP**.

O coordenador de curso faz upload de uma planilha `.xlsx` com os dados dos estudantes, a aplicação valida cada linha conforme as regras do edital e gera o arquivo `.TXT` no formato oficial — pronto para importação no sistema do INEP.

---

## Categorias suportadas

| Categoria | Anexo do Edital | Identificador | Campos |
|-----------|----------------|---------------|--------|
| **Concluintes** | Anexo VII | N99 | 13 |
| **Ingressantes** | Anexo VIII | N92 | 10 |
| **Medicina — 4º ano** | Anexo VI | N99M | 14 |

---

## Como funciona

```
Coordenador (Navegador)
    │
    ├── 1. Seleciona a categoria (Concluintes / Ingressantes / Medicina)
    ├── 2. Faz upload da planilha .xlsx
    ├── 3. Clica "Validar planilha"
    │       └── Sistema verifica: campos obrigatórios, formatos, CPF, faixas de ano
    ├── 4. Visualiza tabela de dados + log com erros/avisos
    ├── 5. Ajusta parâmetros (data, sequência, código do curso)
    └── 6. Clica "Gerar arquivo .TXT" → download automático
```

### Validações realizadas

- Campos obrigatórios (CO_IES, CO_CURSO, NU_CPF, etc.)
- CPF com 11 dígitos e verificação do dígito verificador
- Turnos válidos (1-Matutino, 2-Vespertino, 3-Integral, 4-Noturno)
- Percentual de integralização (0–100%, até 25% para ingressantes)
- Faixas de anos (ensino médio, início da graduação, formatura)
- Polo EaD e município (código IBGE de 7 dígitos)
- Indicador de origem Enamed (apenas Medicina)

---

## Pré-requisitos

- **Python 3.10+**
- Bibliotecas: `pandas`, `openpyxl`, `flask`

---

## Instalação e execução

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Iniciar o servidor
python web_app.py
```

Acesse no navegador:

```
http://localhost:5000
```

> O servidor roda na porta **5000** por padrão. Para alterar, edite o final do `web_app.py`.

---

## Execução com Docker

Requisito: [Docker](https://docs.docker.com/get-docker/) e [Docker Compose](https://docs.docker.com/compose/install/) instalados.

### Opção 1 — Docker Compose (recomendado)

```bash
# Subir a aplicação em segundo plano
docker compose up -d --build

# Ver logs
docker compose logs -f

# Parar
docker compose down
```

### Opção 2 — Docker sem Compose

```bash
# Construir a imagem
docker build -t enade-cadastro-lote .

# Executar o container
docker run -d --name Enade_Cadastro_Lote -p 5000:5000 enade-cadastro-lote

# Parar e remover
docker stop Enade_Cadastro_Lote && docker rm Enade_Cadastro_Lote
```

Acesse no navegador:

```
http://localhost:5000
```

> No `docker-compose.yml` o serviço `web` fica exposto apenas em `127.0.0.1:5000` — a exposição pública deve passar pelo reverse proxy (ver abaixo). Para acesso direto na intranet, troque para `"5000:5000"`.

---

## Deploy seguro (produção / internet)

A aplicação processa **dados pessoais (CPF, nome, e-mail)** e está sujeita à **LGPD**. **Nunca** exponha uma instância na internet pública sem autenticação e HTTPS.

### 1. Configurar segredos e credenciais

```bash
cp .env.example .env
# Gere uma SECRET_KEY:
python -c "import secrets; print(secrets.token_hex(32))"
# Edite o .env: defina SECRET_KEY, APP_AUTH_USERNAME e APP_AUTH_PASSWORD (senha forte).
```

Quando `APP_AUTH_USERNAME` e `APP_AUTH_PASSWORD` estão definidos, **todas as rotas exigem login** (HTTP Basic). O app avisa no log se a autenticação estiver desativada.

### 2. TLS / HTTPS via reverse proxy (nginx)

Coloque seus certificados em `deploy/certs/` (`fullchain.pem` e `privkey.pem`). Para gerar gratuitamente com Let's Encrypt, use o `certbot` no host e aponte os caminhos, ou monte os certificados institucionais.

Suba o app + proxy:

```bash
docker compose --profile proxy up -d --build
```

O nginx (`deploy/nginx.conf`) termina o TLS, redireciona HTTP→HTTPS, aplica **rate limiting** e envia cabeçalhos de segurança. Edite `server_name` com o seu domínio.

### 3. Camadas de segurança já incluídas

| Camada | Implementação |
|--------|---------------|
| Autenticação | HTTP Basic opt-in (`APP_AUTH_*`), comparação em tempo constante |
| HTTPS | Terminado no nginx (TLS 1.2/1.3) + HSTS |
| Cabeçalhos | CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` |
| Rate limiting | nginx (`limit_req`) + `flask-limiter` no app (ver aviso abaixo) |
| Isolamento | Container não-root, `read_only`, `cap_drop: ALL`, `no-new-privileges` |
| Erros | Exceções logadas no servidor; cliente recebe mensagem genérica |
| Upload | Limite de tamanho (`MAX_UPLOAD_MB`) + validação de extensão |
| Healthcheck | Rota `/health` (sem autenticação) |
| PII | `.gitignore`/`.dockerignore` bloqueiam `*.xlsx/*.xls/*.csv` |

> **Rate limiting com múltiplos workers:** por padrão, `flask-limiter` guarda os
> contadores em memória (`RATE_LIMIT_STORAGE=memory://`), isolados por processo.
> Como o `Dockerfile` sobe o Gunicorn com `GUNICORN_WORKERS=2` por padrão, o
> limite efetivo pode chegar a **2× o valor configurado**, distribuído de forma
> imprevisível entre os workers. O app avisa no log quando detecta essa
> combinação. Em produção com mais de 1 worker, suba o serviço `redis` do
> `docker-compose.yml` (`docker compose --profile redis up -d`) e configure:
> ```
> RATE_LIMIT_STORAGE=redis://redis:6379
> ```
> para um contador compartilhado e consistente entre todos os workers.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes cobrem a lógica crítica de validação (CPF, faixas de ano, turnos), a geração do
arquivo oficial (`tests/test_generator.py`) e as rotas Flask — autenticação, uploads
inválidos, cabeçalhos de segurança, limite de tamanho (`tests/test_web_app.py`).

## Qualidade de código

```bash
ruff check .      # lint
mypy              # checagem de tipos (app/ e web_app.py)
pip-audit -r requirements.txt   # vulnerabilidades conhecidas nas dependências
```

Configurados em `pyproject.toml`. O workflow `.github/workflows/ci.yml` roda os três,
mais `pytest`, em cada push/PR (Python 3.10 e 3.12); o Dependabot (`.github/dependabot.yml`)
abre PRs semanais para atualizações de `pip`, `docker` e GitHub Actions.

---

## Estrutura do projeto

```
├── app/
│   ├── __init__.py      # Versão do pacote
│   ├── config.py        # Constantes, categorias e layouts do ENADE
│   └── generator.py     # Validação de dados e geração do arquivo .TXT
├── static/
│   ├── app.js           # JavaScript do frontend
│   └── styles.css       # Estilos da interface
├── templates/
│   └── index.html       # Página HTML principal
├── deploy/
│   └── nginx.conf       # Reverse proxy TLS + rate limit + headers
├── tests/
│   └── test_generator.py # Testes da lógica de validação/geração
├── requirements.txt     # Dependências Python (versões fixadas)
├── requirements-dev.txt # Dependências de teste
├── .env.example         # Modelo de variáveis de ambiente/segredos
├── web_app.py           # Servidor Flask (ponto de entrada)
├── Dockerfile           # Imagem Docker (não-root, healthcheck)
├── docker-compose.yml   # Orquestração (app + proxy opcional)
└── README.md
```

### Responsabilidades dos módulos

| Módulo | Responsabilidade |
|--------|-----------------|
| `config.py` | Define as 3 categorias, seus campos, constantes fixas (CO_PROJETO, TP_ORIGEM) e faixas de validação de anos |
| `generator.py` | Carrega planilha `.xlsx`, valida linha a linha, normaliza dados e gera o conteúdo do `.TXT` com delimitador `;` |
| `web_app.py` | Expõe a lógica via API REST (Flask): `/api/categorias`, `/api/validar`, `/api/gerar` |
| `app.js` | Gerencia interações do frontend: upload com drag & drop, chamadas à API, renderização de tabela e log |

---

## API REST

| Rota | Método | Descrição |
|------|--------|-----------|
| `/` | GET | Página principal da aplicação |
| `/api/categorias` | GET | Lista as categorias disponíveis com campos e metadados |
| `/api/validar` | POST | Recebe planilha + categoria → retorna JSON com resultado da validação |
| `/api/gerar` | POST | Recebe planilha + parâmetros → retorna o arquivo `.TXT` para download |

### Parâmetros de `/api/validar`

- `arquivo` (file) — planilha `.xlsx`
- `categoria` (string) — `concluinte`, `ingressante` ou `medicina4`
- `ano_referencia` (int) — ano de referência do ENADE

### Parâmetros de `/api/gerar`

- `arquivo` (file) — planilha `.xlsx`
- `categoria` (string) — `concluinte`, `ingressante` ou `medicina4`
- `ano_referencia` (int) — ano de referência
- `data` (string) — data no formato `DDMMAAAA`
- `sequencia` (int) — número sequencial do arquivo (1–999)
- `co_curso` (string) — código do curso (somente dígitos)
- `encoding` (string) — `utf-8` ou `cp1252`

---

## Formato do arquivo gerado

O arquivo `.TXT` segue o padrão oficial do INEP:

- **Delimitador:** ponto e vírgula (`;`)
- **Separador decimal:** ponto (`.`)
- **Quebra de linha:** `\r\n` (Windows)
- **Nome do arquivo:** `ENADE{CO_PROJETO}_{IDENTIFICADOR}_{UF}_{DATA}_{CO_CURSO}_{TP_ORIGEM}{SEQ}.TXT`

Exemplo: `ENADE2611101_N99_BR_04062026_123456_E001.TXT`

---

## Planilha de entrada

A planilha `.xlsx` deve conter as colunas correspondentes à categoria selecionada na **primeira aba**. Colunas adicionais (como `Nome`, `Matrícula`, `Modalidade`) são ignoradas, exceto `Modalidade` que é usada para detectar automaticamente cursos EaD/presenciais.

**Coluna NU_CPF:** no Excel, formate a coluna como **Texto** antes de colar os CPFs. Se a célula estiver como número, o Excel remove o zero à esquerda (ex.: `04159516157` vira `4159516157`) e a validação falha. O sistema tenta recuperar zeros à esquerda quando o valor tem 9 ou 10 dígitos, mas o ideal é manter o CPF completo na planilha.

### Exemplo de colunas (Concluintes)

| CO_PROJETO | TP_ORIGEM | CO_IES | CO_CURSO | NU_CPF | NU_ANO_FIM_ENSINO_MEDIO | CO_TURNO_GRADUACAO | NU_PERCENTUAL_INTEGRALIZACAO | NU_ANO_FORMATURA | NU_SEMESTRE_FORMATURA | NU_ANO_INICIO_GRADUACAO | IN_MUNICIPIO_POLO_EXTERIOR | CO_MUNICIPIO_POLO |
|------------|-----------|--------|----------|--------|------------------------|--------------------|------------------------------|------------------|----------------------|------------------------|---------------------------|-------------------|
| 2611101 | E | 2 | 123456 | 12345678901 | 2018 | 3 | 85.50 | 2026 | 1 | 2021 | 0 | 5208707 |
