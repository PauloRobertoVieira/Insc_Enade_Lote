/**
 * ENADE Cadastro em Lote — Frontend Application
 * Manages file upload, validation, and .ZIP generation via Flask API.
 */

(function () {
  "use strict";

  // ── State ──────────────────────────────────────────────────────
  const state = {
    categorias: [],
    categoriaAtual: null,
    arquivo: null,
    validado: false,
    resultado: null,
    // Constantes fixas do nome do arquivo, carregadas de /api/categorias
    // (fonte única: app/config.py) para não duplicá-las aqui.
    constantes: { coProjeto: "", noPrograma: "", uf: "", tpOrigem: "" },
  };

  // ── DOM refs ───────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const el = {
    subtitle: $("#header-subtitle"),
    catRadios: $("#categoria-radios"),
    catInfo: $("#categoria-info"),
    uploadArea: $("#upload-area"),
    fileInput: $("#file-input"),
    fileInfo: $("#file-info"),
    fileName: $("#file-name"),
    fileSize: $("#file-size"),
    fileRemove: $("#file-remove"),
    anoRef: $("#ano-ref"),
    dataGeracao: $("#data-geracao"),
    coCurso: $("#co-curso"),
    sequencia: $("#sequencia"),
    encoding: $("#encoding"),
    previewNome: $("#preview-nome"),
    btnValidar: $("#btn-validar"),
    btnGerar: $("#btn-gerar"),
    resumoBadge: $("#resumo-badge"),
    emptyTable: $("#empty-table"),
    dataTable: $("#data-table"),
    tableHead: $("#table-head"),
    tableBody: $("#table-body"),
    badgeGroup: $("#badge-group"),
    emptyLog: $("#empty-log"),
    logEntries: $("#log-entries"),
    loadingOverlay: $("#loading-overlay"),
    loadingText: $("#loading-text"),
    toastContainer: $("#toast-container"),
  };

  // ── Constants ──────────────────────────────────────────────────
  const EXT_PLANILHA = /\.(xlsx|xls|csv)$/i;

  const CAT_DESC = {
    concluinte:
      "Concluintes de bacharelado, licenciatura e tecnológico. Layout com 13 campos, incluindo formatura e polo de apoio (EaD).",
    ingressante:
      "Ingressantes: estudantes nos primeiros 0–25% da carga horária. Layout com 10 campos, sem dados de formatura.",
    medicina4:
      "Estudantes do 4º ano dos cursos de Medicina (Enamed). Layout com 14 campos, com indicação de origem Enamed.",
  };

  // ── Init ───────────────────────────────────────────────────────
  async function init() {
    setupDateDefault();
    setupEventListeners();
    await loadCategorias();
    updatePreviewNome();
  }

  function setupDateDefault() {
    const now = new Date();
    const dd = String(now.getDate()).padStart(2, "0");
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const yyyy = now.getFullYear();
    el.dataGeracao.value = `${dd}${mm}${yyyy}`;
  }

  // ── Event listeners ────────────────────────────────────────────
  function setupEventListeners() {
    // Upload area
    el.uploadArea.addEventListener("click", () => el.fileInput.click());
    el.fileInput.addEventListener("change", handleFileSelect);
    el.uploadArea.addEventListener("dragover", (e) => {
      e.preventDefault();
      el.uploadArea.classList.add("dragover");
    });
    el.uploadArea.addEventListener("dragleave", () =>
      el.uploadArea.classList.remove("dragover")
    );
    el.uploadArea.addEventListener("drop", (e) => {
      e.preventDefault();
      el.uploadArea.classList.remove("dragover");
      if (e.dataTransfer.files.length) {
        el.fileInput.files = e.dataTransfer.files;
        handleFileSelect();
      }
    });
    el.fileRemove.addEventListener("click", removeFile);

    // Params → update preview
    el.anoRef.addEventListener("input", updatePreviewNome);
    el.dataGeracao.addEventListener("input", updatePreviewNome);
    el.coCurso.addEventListener("input", updatePreviewNome);
    el.sequencia.addEventListener("input", updatePreviewNome);

    // Actions
    el.btnValidar.addEventListener("click", validar);
    el.btnGerar.addEventListener("click", gerar);
  }

  // ── Load categorias ────────────────────────────────────────────
  async function loadCategorias() {
    try {
      const res = await fetch("/api/categorias");
      const data = await res.json();
      state.categorias = data.categorias;
      state.constantes = {
        coProjeto: data.co_projeto,
        noPrograma: data.no_programa,
        uf: data.uf,
        tpOrigem: data.tp_origem,
      };
      el.anoRef.value = data.ano_corrente;
      renderCategorias();
      if (state.categorias.length) selectCategoria(state.categorias[0].chave);
    } catch (err) {
      toast("Erro ao carregar categorias", "error");
    }
  }

  function renderCategorias() {
    el.catRadios.innerHTML = state.categorias
      .map(
        (cat) => `
      <label class="radio-option" data-chave="${cat.chave}" id="radio-${cat.chave}">
        <input type="radio" name="categoria" value="${cat.chave}">
        <span class="radio-dot"></span>
        <div>
          <div class="radio-label">${cat.rotulo}</div>
          <div class="radio-sublabel">${cat.anexo} · ${cat.campos.length} campos</div>
        </div>
      </label>`
      )
      .join("");

    el.catRadios.querySelectorAll(".radio-option").forEach((opt) => {
      opt.addEventListener("click", () => selectCategoria(opt.dataset.chave));
    });
  }

  function selectCategoria(chave) {
    const cat = state.categorias.find((c) => c.chave === chave);
    if (!cat) return;
    state.categoriaAtual = cat;
    state.validado = false;
    state.resultado = null;

    // UI update
    el.catRadios.querySelectorAll(".radio-option").forEach((o) => {
      o.classList.toggle("active", o.dataset.chave === chave);
      o.querySelector("input").checked = o.dataset.chave === chave;
    });
    el.catInfo.textContent = CAT_DESC[chave] || "";
    el.subtitle.textContent = `${cat.rotulo} · ${cat.anexo} · Identificador: ${cat.identificacao_arquivo}`;

    clearResults();
    updatePreviewNome();
    updateButtons();
  }

  // ── File handling ──────────────────────────────────────────────
  function handleFileSelect() {
    const file = el.fileInput.files[0];
    if (!file) return;
    if (!EXT_PLANILHA.test(file.name)) {
      el.fileInput.value = "";
      toast("Formato não suportado. Use .xlsx ou .csv", "error");
      return;
    }
    state.arquivo = file;
    state.validado = false;
    state.resultado = null;
    clearResults();

    el.uploadArea.classList.add("hidden");
    el.fileInfo.classList.remove("hidden");
    el.fileName.textContent = file.name;
    el.fileSize.textContent = formatBytes(file.size);
    updateButtons();
  }

  function removeFile() {
    state.arquivo = null;
    state.validado = false;
    state.resultado = null;
    el.fileInput.value = "";
    el.uploadArea.classList.remove("hidden");
    el.fileInfo.classList.add("hidden");
    clearResults();
    updateButtons();
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  // ── Validation ─────────────────────────────────────────────────
  async function validar() {
    if (!state.arquivo || !state.categoriaAtual) return;

    showLoading("Validando planilha...");
    const form = new FormData();
    form.append("arquivo", state.arquivo);
    form.append("categoria", state.categoriaAtual.chave);
    form.append("ano_referencia", el.anoRef.value);

    try {
      const res = await fetch("/api/validar", { method: "POST", body: form });
      const data = await res.json();
      hideLoading();

      if (data.erro) {
        toast(data.erro, "error");
        return;
      }

      state.resultado = data;
      state.validado = true;

      // Fill course code
      if (data.co_curso_detectado) el.coCurso.value = data.co_curso_detectado;
      updatePreviewNome();

      renderTable(data);
      renderLog(data);
      updateButtons();

      if (data.valido) {
        toast(`Validação OK — ${data.linhas_validas} linha(s) prontas`, "success");
      } else {
        toast(`Validação com ${data.erros} erro(s)`, "error");
      }
    } catch (err) {
      hideLoading();
      toast("Falha de comunicação com o servidor", "error");
    }
  }

  // ── Generation ─────────────────────────────────────────────────
  async function gerar() {
    if (!state.arquivo || !state.categoriaAtual || !state.validado) return;

    const coCurso = el.coCurso.value.trim();
    if (!coCurso || !/^\d+$/.test(coCurso)) {
      toast("CO_CURSO deve conter apenas dígitos", "error");
      return;
    }
    const dataVal = el.dataGeracao.value.trim();
    if (!/^\d{8}$/.test(dataVal)) {
      toast("Data inválida — use formato DDMMAAAA", "error");
      return;
    }

    showLoading("Gerando arquivo .ZIP...");
    const form = new FormData();
    form.append("arquivo", state.arquivo);
    form.append("categoria", state.categoriaAtual.chave);
    form.append("ano_referencia", el.anoRef.value);
    form.append("data", dataVal);
    form.append("sequencia", el.sequencia.value);
    form.append("co_curso", coCurso);
    form.append("encoding", el.encoding.value);

    try {
      const res = await fetch("/api/gerar", { method: "POST", body: form });
      hideLoading();

      if (!res.ok) {
        const err = await res.json();
        toast(err.erro || "Erro ao gerar arquivo", "error");
        return;
      }

      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const match = cd.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : "arquivo_enade.ZIP";

      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      toast(`Arquivo ${filename} gerado com sucesso!`, "success");

      // Add success entry to log
      addLogEntry("SUCESSO", `Arquivo gerado: ${filename} — ${state.resultado?.linhas_validas || "?"} registro(s)`);
    } catch (err) {
      hideLoading();
      toast("Falha de comunicação com o servidor", "error");
    }
  }

  // ── Render Table ───────────────────────────────────────────────
  function renderTable(data) {
    if (!data.campos || !data.dados_preview.length) {
      el.emptyTable.classList.remove("hidden");
      el.dataTable.classList.add("hidden");
      el.resumoBadge.textContent = "Nenhuma linha válida";
      el.resumoBadge.className = "badge badge--error";
      return;
    }

    el.emptyTable.classList.add("hidden");
    el.dataTable.classList.remove("hidden");

    el.tableHead.innerHTML = `<tr>${data.campos.map((c) => `<th>${c}</th>`).join("")}</tr>`;
    el.tableBody.innerHTML = data.dados_preview
      .map(
        (row) =>
          `<tr>${data.campos.map((c) => `<td>${escapeHtml(row[c] || "")}</td>`).join("")}</tr>`
      )
      .join("");

    el.resumoBadge.textContent = `${data.linhas_validas} linha(s) válida(s)`;
    el.resumoBadge.className = data.valido ? "badge badge--success" : "badge badge--warning";
  }

  // ── Render Log ─────────────────────────────────────────────────
  function renderLog(data) {
    el.emptyLog.classList.add("hidden");
    el.logEntries.classList.remove("hidden");
    el.logEntries.innerHTML = "";

    if (!data.mensagens.length) {
      addLogEntry("SUCESSO", `Sem alertas. Todas as ${data.total_linhas} linhas passaram.`);
    } else {
      data.mensagens.forEach((m) => {
        const txt = `Linha ${m.linha} — ${m.campo}: ${m.mensagem}`;
        addLogEntry(m.nivel, txt);
      });
    }

    // Badges
    el.badgeGroup.innerHTML = "";
    if (data.erros > 0) el.badgeGroup.innerHTML += `<span class="badge badge--error">Erros: ${data.erros}</span>`;
    if (data.avisos > 0) el.badgeGroup.innerHTML += `<span class="badge badge--warning">Avisos: ${data.avisos}</span>`;
    if (data.info > 0) el.badgeGroup.innerHTML += `<span class="badge badge--info">Info: ${data.info}</span>`;
    if (!data.erros && !data.avisos && !data.info) el.badgeGroup.innerHTML = `<span class="badge badge--success">Tudo OK</span>`;

    // Multi-course warning
    if (data.cursos_distintos && data.cursos_distintos.length > 1) {
      addLogEntry("AVISO", `Planilha contém múltiplos cursos (${data.cursos_distintos.join(", ")}). Gere um arquivo por curso.`);
    }
  }

  function addLogEntry(nivel, text) {
    const entry = document.createElement("div");
    entry.className = `log-entry log-entry--${nivel}`;
    entry.innerHTML = `<span class="log-entry__tag">${nivel}</span><span class="log-entry__text">${escapeHtml(text)}</span>`;
    el.logEntries.appendChild(entry);
    el.logEntries.scrollTop = el.logEntries.scrollHeight;
  }

  // ── Preview nome ───────────────────────────────────────────────
  function updatePreviewNome() {
    if (!state.categoriaAtual) {
      el.previewNome.textContent = "—";
      return;
    }
    const coCurso = el.coCurso.value.trim() || "[CO_CURSO]";
    const dataVal = el.dataGeracao.value.trim() || "DDMMAAAA";
    let seq = parseInt(el.sequencia.value, 10);
    if (isNaN(seq) || seq < 1) seq = 1;
    const seqStr = String(seq).padStart(3, "0");
    const idArq = state.categoriaAtual.identificacao_arquivo;
    const { coProjeto, noPrograma, uf, tpOrigem } = state.constantes;
    const base = `${noPrograma}${coProjeto}_${idArq}_${uf}_${dataVal}_${coCurso}_${tpOrigem}${seqStr}`;
    el.previewNome.textContent = `${base}.ZIP (${base}.TXT dentro)`;
  }

  // ── Helpers ────────────────────────────────────────────────────
  function clearResults() {
    el.emptyTable.classList.remove("hidden");
    el.dataTable.classList.add("hidden");
    el.tableHead.innerHTML = "";
    el.tableBody.innerHTML = "";
    el.resumoBadge.textContent = "Aguardando planilha...";
    el.resumoBadge.className = "badge badge--neutral";

    el.emptyLog.classList.remove("hidden");
    el.logEntries.classList.add("hidden");
    el.logEntries.innerHTML = "";
    el.badgeGroup.innerHTML = "";
  }

  function updateButtons() {
    el.btnValidar.disabled = !state.arquivo;
    el.btnGerar.disabled = !state.validado || !state.resultado || state.resultado.linhas_validas === 0;
  }

  function showLoading(text) {
    el.loadingText.textContent = text || "Processando...";
    el.loadingOverlay.classList.remove("hidden");
  }
  function hideLoading() {
    el.loadingOverlay.classList.add("hidden");
  }

  function toast(message, type) {
    const t = document.createElement("div");
    t.className = `toast toast--${type || "info"}`;
    t.textContent = message;
    el.toastContainer.appendChild(t);
    setTimeout(() => {
      t.style.opacity = "0";
      t.style.transform = "translateX(100%)";
      t.style.transition = "all 0.3s ease";
      setTimeout(() => t.remove(), 300);
    }, 4000);
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Boot ───────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", init);
})();
