from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import DBSession
from app.ingestion.intake_orchestrator import IntakeError, IntakeOrchestrator
from app.schemas.intake import (
    CandidateRead,
    EvidenceRead,
    IntakePreviewRead,
    PreviewRequest,
    RunExtractionResponse,
    SaveJobRequest,
    SourceDocumentRead,
    SourceJobRead,
)

router = APIRouter(tags=["intake"])
api_router = APIRouter(prefix="/intake", tags=["intake"])


@router.get("/intake-console", response_class=HTMLResponse)
def intake_console() -> str:
    return INTAKE_CONSOLE_HTML


@router.get("/input-window")
def input_window_redirect() -> RedirectResponse:
    return RedirectResponse(url="/intake-console", status_code=307)


@api_router.post("/preview", response_model=IntakePreviewRead)
async def preview_source(payload: PreviewRequest, db: DBSession) -> dict:
    try:
        return await IntakeOrchestrator(db).preview_input(**payload.model_dump())
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": exc.fatal_errors}) from exc


@api_router.post("/upload/preview", response_model=IntakePreviewRead)
async def preview_upload(
    db: DBSession,
    file: UploadFile = File(...),
    requested_source_type: str | None = Form(default=None),
    created_by: str | None = Form(default=None),
) -> dict:
    content = await file.read()
    try:
        return IntakeOrchestrator(db).preview_upload(
            filename=file.filename or "source-upload",
            content=content,
            content_type=file.content_type,
            requested_source_type=requested_source_type,
            created_by=created_by,
        )
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": exc.fatal_errors}) from exc


@api_router.post("/jobs", response_model=SourceJobRead)
def save_job(payload: SaveJobRequest, db: DBSession):
    try:
        return IntakeOrchestrator(db).save_job_from_preview(
            preview_id=payload.preview_id,
            staged_artifact_id=payload.staged_artifact_id,
            created_by=payload.created_by,
        )
    except IntakeError as exc:
        raise HTTPException(status_code=409, detail={"fatal_errors": exc.fatal_errors}) from exc


@api_router.post("/upload/jobs", response_model=SourceJobRead)
async def save_upload_job(
    db: DBSession,
    file: UploadFile = File(...),
    requested_source_type: str | None = Form(default=None),
    created_by: str | None = Form(default=None),
):
    content = await file.read()
    try:
        return IntakeOrchestrator(db).save_upload_job(
            filename=file.filename or "source-upload",
            content=content,
            content_type=file.content_type,
            requested_source_type=requested_source_type,
            created_by=created_by,
        )
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": exc.fatal_errors}) from exc


@api_router.post("/jobs/{source_job_id}/run", response_model=RunExtractionResponse)
async def run_job(source_job_id: int, db: DBSession) -> RunExtractionResponse:
    try:
        job, count, reused_existing = await IntakeOrchestrator(db).run_extraction(source_job_id)
        return RunExtractionResponse(
            source_job_id=job.id,
            extracted_candidates=count,
            status=job.status,
            final_source_type=job.final_source_type,
            adapter_name=job.adapter_name,
            reused_existing=reused_existing,
            fatal_errors=[],
        )
    except IntakeError as exc:
        raise HTTPException(status_code=409, detail={"fatal_errors": exc.fatal_errors}) from exc


@api_router.get("/jobs/{source_job_id}/candidates", response_model=list[CandidateRead])
def candidates(source_job_id: int, db: DBSession):
    return IntakeOrchestrator(db).candidates_for_job(source_job_id)


@api_router.get("/jobs/{source_job_id}/evidence", response_model=list[EvidenceRead])
def evidence(source_job_id: int, db: DBSession):
    return IntakeOrchestrator(db).evidence_for_job(source_job_id)


@api_router.get("/jobs/{source_job_id}/documents", response_model=list[SourceDocumentRead])
def documents(source_job_id: int, db: DBSession):
    return IntakeOrchestrator(db).documents_for_job(source_job_id)


INTAKE_CONSOLE_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MQCHAIN Intake Console</title>
  <style>
    :root { color-scheme: light; --ink: #1d252c; --muted: #5d6b78; --line: #d7dde3; --panel: #ffffff; --bg: #f4f6f8; --accent: #0f6b5f; }
    * { box-sizing: border-box; }
    body { margin: 0; font: 14px system-ui, -apple-system, Segoe UI, sans-serif; color: var(--ink); background: var(--bg); }
    header { display: flex; justify-content: space-between; align-items: center; padding: 16px 22px; background: #122027; color: #fff; }
    main { display: grid; grid-template-columns: minmax(320px, 480px) 1fr; gap: 16px; padding: 16px; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
    h1 { margin: 0; font-size: 18px; }
    h2 { margin: 0 0 10px; font-size: 15px; }
    .tabs { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; margin-bottom: 12px; }
    button { border: 1px solid #8da2ad; border-radius: 6px; background: #fff; color: var(--ink); padding: 8px 10px; cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.active { background: #1d252c; border-color: #1d252c; color: #fff; }
    label { display: grid; gap: 5px; margin: 10px 0; color: var(--muted); }
    input, textarea { width: 100%; border: 1px solid #b9c5cf; border-radius: 6px; padding: 8px; font: inherit; color: var(--ink); background: #fff; }
    textarea { min-height: 140px; resize: vertical; }
    details { border-top: 1px solid var(--line); margin-top: 12px; padding-top: 10px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
    .metric { border: 1px solid var(--line); border-radius: 6px; padding: 8px; min-height: 54px; }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .candidate-table { width: 100%; border-collapse: collapse; margin-bottom: 12px; table-layout: fixed; }
    .candidate-table th, .candidate-table td { border: 1px solid var(--line); padding: 7px 8px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
    .candidate-table th { background: #edf3f2; color: #30424a; font-size: 12px; }
    .candidate-table td { background: #fff; }
    .candidate-table .address { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
    .empty-state { border: 1px dashed var(--line); border-radius: 6px; color: var(--muted); padding: 10px; margin-bottom: 12px; background: #fafbfc; }
    pre { min-height: 360px; max-height: 70vh; overflow: auto; margin: 0; padding: 12px; border-radius: 8px; background: #101820; color: #e8eef3; white-space: pre-wrap; }
    [hidden] { display: none !important; }
    @media (max-width: 860px) { main { grid-template-columns: 1fr; } .tabs, .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  </style>
</head>
<body>
  <header>
    <h1>MQCHAIN Intake Console</h1>
    <span>v1</span>
  </header>
  <main>
    <section>
      <h2>Add Source</h2>
      <div class="tabs">
        <button type="button" data-mode="upload" class="active">Upload File</button>
        <button type="button" data-mode="url">Source URL</button>
        <button type="button" data-mode="github">GitHub URL</button>
        <button type="button" data-mode="paste">Paste Text</button>
        <button type="button" data-mode="onchain_root">On-chain Root</button>
      </div>
      <label id="fileField">File<input id="file" type="file" accept=".pdf,.csv,.xlsx,.xls,.txt,.md,.json,.yaml,.yml"></label>
      <label id="urlField" hidden>URL<input id="source_url" placeholder="https://"></label>
      <label id="pasteField" hidden>Text<textarea id="pasted_text"></textarea></label>
      <details>
        <summary>Advanced</summary>
        <label>Source type hint<input id="requested_source_type" placeholder="optional hint"></label>
      </details>
      <div class="actions">
        <button type="button" class="primary" id="preview">Analyze / Preview</button>
        <button type="button" id="save">Save Source Job</button>
        <button type="button" id="run">Run Extraction</button>
        <button type="button" id="candidates">View Candidates</button>
        <button type="button" id="evidence">View Evidence</button>
      </div>
      <label>Preview ID<input id="preview_id"></label>
      <label>Staged Artifact ID<input id="staged_artifact_id"></label>
      <label>Source Job ID<input id="source_job_id" type="number"></label>
    </section>
    <section>
      <div class="grid">
        <div class="metric"><span>Final Type</span><strong id="final_source_type">-</strong></div>
        <div class="metric"><span>Adapter</span><strong id="adapter_name">-</strong></div>
        <div class="metric"><span>Status</span><strong id="status">-</strong></div>
        <div class="metric"><span>Extracted</span><strong id="extracted_candidates">-</strong></div>
      </div>
      <div id="candidateTableWrap" hidden>
        <table class="candidate-table" aria-label="Candidate preview table">
          <thead>
            <tr>
              <th>Entity</th>
              <th>Network</th>
              <th>Chain</th>
              <th>Address</th>
              <th>Role</th>
              <th>Confidence</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="candidateRows"></tbody>
        </table>
      </div>
      <div id="evidenceTableWrap" hidden>
        <table class="candidate-table" aria-label="Evidence table">
          <thead>
            <tr>
              <th>Evidence ID</th>
              <th>Candidate ID</th>
              <th>Evidence Type</th>
              <th>Source Type</th>
              <th>Adapter</th>
              <th>File / URL</th>
              <th>Row</th>
              <th>Page</th>
              <th>Confidence Reason</th>
            </tr>
          </thead>
          <tbody id="evidenceRows"></tbody>
        </table>
      </div>
      <div id="documentTableWrap" hidden>
        <table class="candidate-table" aria-label="Document table">
          <thead>
            <tr>
              <th>Document ID</th>
              <th>Source Job</th>
              <th>Title</th>
              <th>Content Type</th>
              <th>File / URL</th>
            </tr>
          </thead>
          <tbody id="documentRows"></tbody>
        </table>
      </div>
      <div id="emptyState" class="empty-state" hidden></div>
      <pre id="output">{}</pre>
    </section>
  </main>
  <script>
    let mode = "upload";
    const output = document.getElementById("output");
    const val = id => document.getElementById(id).value.trim();
    const setVal = (id, value) => { document.getElementById(id).value = value || ""; };
    const candidatesFrom = data => Array.isArray(data) ? data : (Array.isArray(data.candidates_preview) ? data.candidates_preview : []);
    const cell = value => String(value ?? "-").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    const hideTables = () => {
      document.getElementById("candidateTableWrap").hidden = true;
      document.getElementById("evidenceTableWrap").hidden = true;
      document.getElementById("documentTableWrap").hidden = true;
      document.getElementById("emptyState").hidden = true;
    };
    const renderCandidateTable = data => {
      hideTables();
      const rows = candidatesFrom(data);
      const wrap = document.getElementById("candidateTableWrap");
      const body = document.getElementById("candidateRows");
      wrap.hidden = rows.length === 0;
      body.innerHTML = rows.map(item => `
        <tr>
          <td>${cell(item.entity_name)}</td>
          <td>${cell(item.source_network)}</td>
          <td>${cell(item.chain_slug || item.chain_guess || item.chain_id)}</td>
          <td class="address">${cell(item.address)}</td>
          <td>${cell(item.suggested_role)}</td>
          <td>${cell(item.confidence_initial)}</td>
          <td>${cell(item.status)}</td>
        </tr>
      `).join("");
    };
    const evidenceRowValue = (item, key) => item.payload?.[key] ?? item.payload?.raw_reference?.[key] ?? "-";
    const renderEvidenceTable = data => {
      hideTables();
      const rows = Array.isArray(data) ? data : [];
      const wrap = document.getElementById("evidenceTableWrap");
      const body = document.getElementById("evidenceRows");
      const empty = document.getElementById("emptyState");
      wrap.hidden = rows.length === 0;
      empty.hidden = rows.length !== 0;
      empty.textContent = rows.length ? "" : "No evidence rows found for this source job. Check Source Job ID or run extraction first.";
      body.innerHTML = rows.map(item => `
        <tr>
          <td>${cell(item.id)}</td>
          <td>${cell(item.candidate_id)}</td>
          <td>${cell(item.evidence_type)}</td>
          <td>${cell(item.final_source_type || item.source_type)}</td>
          <td>${cell(item.adapter_name)}</td>
          <td>${cell(item.file_path || item.source_url)}</td>
          <td>${cell(evidenceRowValue(item, "row_number"))}</td>
          <td>${cell(evidenceRowValue(item, "page_number"))}</td>
          <td>${cell(item.confidence_reason)}</td>
        </tr>
      `).join("");
    };
    const renderDocumentTable = data => {
      hideTables();
      const rows = Array.isArray(data) ? data : [];
      const wrap = document.getElementById("documentTableWrap");
      const body = document.getElementById("documentRows");
      wrap.hidden = rows.length === 0;
      body.innerHTML = rows.map(item => `
        <tr>
          <td>${cell(item.id)}</td>
          <td>${cell(item.source_job_id)}</td>
          <td>${cell(item.document_title)}</td>
          <td>${cell(item.content_type)}</td>
          <td>${cell(item.file_path || item.canonical_source_url)}</td>
        </tr>
      `).join("");
    };
    const show = (data, view = "candidates") => {
      if (view === "evidence") renderEvidenceTable(data);
      else if (view === "documents") renderDocumentTable(data);
      else renderCandidateTable(data);
      output.textContent = JSON.stringify(data, null, 2);
      document.getElementById("final_source_type").textContent = data.final_source_type || "-";
      document.getElementById("adapter_name").textContent = data.adapter_name || "-";
      document.getElementById("status").textContent = data.status || (data.reused_existing ? "reused existing" : "-");
      document.getElementById("extracted_candidates").textContent = data.extracted_candidates ?? "-";
      if (data.preview_id) setVal("preview_id", data.preview_id);
      if (data.staged_artifact_id) setVal("staged_artifact_id", data.staged_artifact_id);
      if (data.id) setVal("source_job_id", data.id);
      if (data.source_job_id) setVal("source_job_id", data.source_job_id);
    };
    const requestJson = async (url, options = {}) => {
      const res = await fetch(url, options);
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) throw new Error(JSON.stringify(data.detail || data));
      return data;
    };
    const payload = () => ({
      input_method: mode,
      source_url: ["url", "github"].includes(mode) ? val("source_url") : null,
      pasted_text: ["paste", "onchain_root"].includes(mode) ? val("pasted_text") : null,
      requested_source_type: val("requested_source_type") || null
    });
    document.querySelectorAll("[data-mode]").forEach(btn => btn.onclick = () => {
      mode = btn.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach(item => item.classList.toggle("active", item === btn));
      document.getElementById("fileField").hidden = mode !== "upload";
      document.getElementById("urlField").hidden = !["url", "github"].includes(mode);
      document.getElementById("pasteField").hidden = !["paste", "onchain_root"].includes(mode);
    });
    document.getElementById("preview").onclick = async () => {
      try {
        if (mode === "upload") {
          const file = document.getElementById("file").files[0];
          if (!file) return show({ error: "No file selected" });
          const fd = new FormData();
          fd.append("file", file);
          if (val("requested_source_type")) fd.append("requested_source_type", val("requested_source_type"));
          show(await requestJson("/api/intake/upload/preview", { method: "POST", body: fd }));
          return;
        }
        show(await requestJson("/api/intake/preview", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload()) }));
      } catch (e) { show({ error: e.message }); }
    };
    document.getElementById("save").onclick = async () => {
      try {
        show(await requestJson("/api/intake/jobs", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ preview_id: val("preview_id") || null, staged_artifact_id: val("staged_artifact_id") || null }) }));
      } catch (e) { show({ error: e.message }); }
    };
    document.getElementById("run").onclick = async () => {
      try { show(await requestJson(`/api/intake/jobs/${val("source_job_id")}/run`, { method: "POST" })); } catch (e) { show({ error: e.message }); }
    };
    document.getElementById("candidates").onclick = async () => {
      try { show(await requestJson(`/api/intake/jobs/${val("source_job_id")}/candidates`)); } catch (e) { show({ error: e.message }); }
    };
    document.getElementById("evidence").onclick = async () => {
      try { show(await requestJson(`/api/intake/jobs/${val("source_job_id")}/evidence`), "evidence"); } catch (e) { show({ error: e.message }); }
    };
  </script>
</body>
</html>
"""
