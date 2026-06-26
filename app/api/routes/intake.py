from __future__ import annotations

import json

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
    source_evidence_json: str | None = Form(default=None),
) -> dict:
    content = await file.read()
    try:
        return IntakeOrchestrator(db).preview_upload(
            filename=file.filename or "source-upload",
            content=content,
            content_type=file.content_type,
            requested_source_type=requested_source_type,
            created_by=created_by,
            source_evidence=_parse_source_evidence(source_evidence_json),
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
    source_evidence_json: str | None = Form(default=None),
):
    content = await file.read()
    try:
        return IntakeOrchestrator(db).save_upload_job(
            filename=file.filename or "source-upload",
            content=content,
            content_type=file.content_type,
            requested_source_type=requested_source_type,
            created_by=created_by,
            source_evidence=_parse_source_evidence(source_evidence_json),
        )
    except IntakeError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": exc.fatal_errors}) from exc


def _parse_source_evidence(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": ["source_evidence_json_invalid"]}) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail={"fatal_errors": ["source_evidence_json_must_be_object"]})
    return parsed


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
    :root { color-scheme: light; --ink: #1d252c; --muted: #5d6b78; --line: #d7dde3; --panel: #ffffff; --bg: #f4f6f8; --accent: #0f6b5f; --danger: #b42318; --warn: #9a6700; --ok: #157347; }
    * { box-sizing: border-box; }
    body { margin: 0; font: 14px system-ui, -apple-system, Segoe UI, sans-serif; color: var(--ink); background: var(--bg); }
    header { display: flex; justify-content: space-between; align-items: center; padding: 16px 22px; background: #122027; color: #fff; }
    main { display: grid; grid-template-columns: minmax(360px, 520px) 1fr; gap: 16px; padding: 16px; align-items: start; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
    h1 { margin: 0; font-size: 18px; letter-spacing: 0; }
    h2 { margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }
    h3 { margin: 16px 0 8px; font-size: 14px; letter-spacing: 0; }
    .tabs { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 6px; margin-bottom: 12px; }
    button { border: 1px solid #8da2ad; border-radius: 6px; background: #fff; color: var(--ink); padding: 8px 10px; cursor: pointer; }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button.active { background: #1d252c; border-color: #1d252c; color: #fff; }
    button.danger { border-color: var(--danger); color: var(--danger); }
    button.small { padding: 4px 6px; font-size: 12px; }
    label { display: grid; gap: 5px; margin: 10px 0; color: var(--muted); }
    input, textarea, select { width: 100%; border: 1px solid #b9c5cf; border-radius: 6px; padding: 8px; font: inherit; color: var(--ink); background: #fff; }
    textarea { min-height: 96px; resize: vertical; }
    details { border-top: 1px solid var(--line); margin-top: 12px; padding-top: 10px; }
    summary { cursor: pointer; font-weight: 650; color: #30424a; }
    .two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-bottom: 12px; }
    .metric { border: 1px solid var(--line); border-radius: 6px; padding: 8px; min-height: 54px; }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .status-flow { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin-bottom: 12px; }
    .step { border: 1px solid var(--line); border-radius: 6px; padding: 6px; display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .badge { display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; padding: 2px 7px; font-size: 12px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
    .badge.done { color: var(--ok); border-color: #a8d5bd; background: #eefaf3; }
    .badge.failed { color: var(--danger); border-color: #f2aaa3; background: #fff2f0; }
    .badge.pending { color: var(--muted); background: #f7f9fa; }
    .badge.warn { color: var(--warn); border-color: #f1d28a; background: #fff8e6; }
    .table-scroll { overflow: auto; border: 1px solid var(--line); border-radius: 6px; margin-bottom: 12px; }
    .candidate-table { width: 100%; border-collapse: collapse; table-layout: fixed; min-width: 1050px; }
    .candidate-table th, .candidate-table td { border: 1px solid var(--line); padding: 7px 8px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
    .candidate-table th { background: #edf3f2; color: #30424a; font-size: 12px; }
    .candidate-table td { background: #fff; }
    .candidate-table .address { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
    .empty-state { border: 1px dashed var(--line); border-radius: 6px; color: var(--muted); padding: 10px; margin-bottom: 12px; background: #fafbfc; }
    .json-preview { min-height: 150px; max-height: 260px; overflow: auto; margin: 8px 0 0; padding: 10px; border-radius: 8px; background: #101820; color: #e8eef3; white-space: pre-wrap; font: 12px ui-monospace, SFMono-Regular, Consolas, monospace; }
    pre { min-height: 280px; max-height: 70vh; overflow: auto; margin: 0; padding: 12px; border-radius: 8px; background: #101820; color: #e8eef3; white-space: pre-wrap; }
    .warning-list { color: var(--warn); margin: 8px 0; padding-left: 18px; }
    [hidden] { display: none !important; }
    @media (max-width: 960px) { main { grid-template-columns: 1fr; } .tabs, .grid, .status-flow, .two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
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

      <details open>
        <summary>Advanced Source Metadata</summary>
        <div class="actions">
          <button type="button" class="small" data-preset="cmc_reserves">CMC Exchange Reserves</button>
          <button type="button" class="small" data-preset="cmc_excel">Processed CMC Excel</button>
          <button type="button" class="small" data-preset="official_docs">Official Docs</button>
          <button type="button" class="small" data-preset="official_github">Official GitHub</button>
          <button type="button" class="small" data-preset="audit_pdf">Audit / PoR PDF</button>
        </div>
        <label>Source URL<input id="meta_source_url" placeholder="https://coinmarketcap.com/exchanges/indodax/"></label>
        <div class="two">
          <label>Entity Hint<input id="entity_hint" placeholder="Indodax"></label>
          <label>Source Origin<input id="source_origin" placeholder="CoinMarketCap"></label>
        </div>
        <label>Official Referrer URL<input id="official_referrer_url" placeholder="Only if official entity links to this source"></label>
        <div class="two">
          <label>Provenance Type<input id="provenance_type" placeholder="third_party_reserve_snapshot"></label>
          <label>Evidence Shape<input id="evidence_shape" placeholder="processed_excel_wallet_list"></label>
        </div>
        <label>Operator Note<textarea id="operator_note" placeholder="Generated from CMC API script for Indodax reserve capture."></textarea></label>
        <div class="two">
          <label>Created By<input id="created_by" placeholder="CRAY"></label>
          <label>Requested Source Type<input id="requested_source_type" placeholder="optional hint"></label>
        </div>
        <ul id="provenanceWarnings" class="warning-list" hidden></ul>
        <h3>Source Evidence JSON</h3>
        <pre id="sourceEvidencePreview" class="json-preview">{}</pre>
      </details>

      <div class="actions">
        <button type="button" class="primary" id="preview">Analyze / Preview</button>
        <button type="button" id="save">Save Source Job</button>
        <button type="button" id="run">Run Extraction</button>
        <button type="button" id="candidates">View Candidates</button>
        <button type="button" id="evidence">View Evidence</button>
        <button type="button" id="documents">View Documents</button>
      </div>
      <label>Preview ID<input id="preview_id"></label>
      <label>Staged Artifact ID<input id="staged_artifact_id"></label>
      <label>Source Job ID<input id="source_job_id" type="number"></label>
    </section>
    <section>
      <h2>Workflow</h2>
      <div id="statusFlow" class="status-flow"></div>
      <div class="grid">
        <div class="metric"><span>Final Type</span><strong id="final_source_type">-</strong></div>
        <div class="metric"><span>Adapter</span><strong id="adapter_name">-</strong></div>
        <div class="metric"><span>Status</span><strong id="status">-</strong></div>
        <div class="metric"><span>Extracted</span><strong id="extracted_candidates">-</strong></div>
      </div>

      <div id="candidateTableWrap" hidden>
        <h3>Candidates</h3>
        <div class="table-scroll"><table class="candidate-table" aria-label="Candidate table">
          <thead><tr><th>Candidate ID</th><th>Entity</th><th>Network</th><th>Chain</th><th>Address</th><th>Role</th><th>Evidence Type</th><th>Source Input Type</th><th>Confidence</th><th>Status</th><th>Warnings</th></tr></thead>
          <tbody id="candidateRows"></tbody>
        </table></div>
      </div>
      <div id="evidenceTableWrap" hidden>
        <h3>Evidence</h3>
        <div class="table-scroll"><table class="candidate-table" aria-label="Evidence table">
          <thead><tr><th>Evidence ID</th><th>Candidate ID</th><th>Evidence Type</th><th>Source Type</th><th>Final Source Type</th><th>Adapter</th><th>Source URL</th><th>File Path</th><th>Confidence Reason</th><th>Payload preview</th></tr></thead>
          <tbody id="evidenceRows"></tbody>
        </table></div>
      </div>
      <div id="documentTableWrap" hidden>
        <h3>Documents</h3>
        <div class="table-scroll"><table class="candidate-table" aria-label="Document table">
          <thead><tr><th>Document ID</th><th>Source Job ID</th><th>Canonical Source URL</th><th>File Path</th><th>Content Type</th><th>Document Title</th><th>Metadata JSON preview</th></tr></thead>
          <tbody id="documentRows"></tbody>
        </table></div>
      </div>
      <div id="emptyState" class="empty-state" hidden></div>

      <h2>Source Verification</h2>
      <div class="two">
        <label>Source Trust<select id="verification_source_trust">
          <option>third_party_exchange_reported</option><option>official_verified</option><option>official_likely</option><option>third_party_officially_referenced</option><option>third_party_audit</option><option>third_party_unverified</option><option>manual_verified</option><option>manual_unverified</option><option>unknown</option><option>rejected</option>
        </select></label>
        <label>Verification Status<select id="verification_status">
          <option>verified</option><option>approved</option><option>active</option><option>pending</option><option>rejected</option>
        </select></label>
      </div>
      <div class="two">
        <label>Verified By<input id="verified_by" placeholder="CRAY"></label>
        <label>Verification Scope<select id="verification_scope">
          <option>source_job</option><option>source_document</option><option>candidate_group</option><option>candidate</option><option>domain</option><option>github_repo</option><option>manual_upload</option>
        </select></label>
      </div>
      <label>Verification Reason<textarea id="verification_reason"></textarea></label>
      <label>Verification Evidence JSON<textarea id="verification_evidence_json">{}</textarea></label>
      <div class="actions">
        <button type="button" id="verifySource" class="primary">Verify Source</button>
        <button type="button" id="rejectSource" class="danger">Reject Source</button>
        <button type="button" data-trust="third_party_exchange_reported">Mark Third-Party Exchange Reported</button>
        <button type="button" data-trust="third_party_audit">Mark Third-Party Audit</button>
        <button type="button" data-trust="official_verified">Mark Official Verified</button>
      </div>

      <h2>Review / Approval</h2>
      <div class="actions">
        <button type="button" id="auditJob">Audit Source Job</button>
        <button type="button" id="candidateGroups">Get Candidate Groups</button>
        <button type="button" id="approveReadyDry">Approve Ready Groups Dry Run</button>
        <button type="button" id="approveReadyApply" class="primary">Approve Ready Groups Apply</button>
        <button type="button" id="approveLowDry">Approve Low Confidence Override Dry Run</button>
        <button type="button" id="approveLowApply" class="primary">Approve Low Confidence Override Apply</button>
        <button type="button" id="approveHotColdDry">Approve Hot/Cold Override Dry Run</button>
        <button type="button" id="approveHotColdApply" class="primary">Approve Hot/Cold Override Apply</button>
      </div>

      <h2>Raw JSON Output</h2>
      <pre id="output">{}</pre>
    </section>
  </main>
  <script>
    let mode = "upload";
    const workflow = {
      preview: "pending",
      job: "pending",
      run: "pending",
      candidates: "pending",
      evidence: "pending",
      documents: "pending",
      verified: "pending",
      approved: "pending"
    };
    const workflowLabels = {
      preview: "Preview Created",
      job: "Source Job Saved",
      run: "Extraction Run",
      candidates: "Candidates Loaded",
      evidence: "Evidence Loaded",
      documents: "Documents Loaded",
      verified: "Source Verified",
      approved: "Candidate Group Approved"
    };
    const output = document.getElementById("output");
    const val = id => document.getElementById(id).value.trim();
    const setVal = (id, value) => { document.getElementById(id).value = value || ""; };
    const candidatesFrom = data => Array.isArray(data) ? data : (Array.isArray(data.candidates_preview) ? data.candidates_preview : []);
    const cell = value => String(value ?? "-").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    const compactJson = value => JSON.stringify(value ?? {}, null, 0).slice(0, 360);
    const copyButton = value => `<button type="button" class="small" data-copy="${cell(value)}">Copy</button>`;
    const mark = (key, status) => { workflow[key] = status; renderWorkflow(); };
    const renderWorkflow = () => {
      document.getElementById("statusFlow").innerHTML = Object.entries(workflowLabels).map(([key, label]) => `
        <div class="step"><span>${label}</span><span class="badge ${workflow[key]}">${workflow[key]}</span></div>
      `).join("");
    };
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
          <td>${cell(item.id)} ${item.id ? copyButton(item.id) : ""}</td>
          <td>${cell(item.entity_name)}</td>
          <td>${cell(item.source_network)}</td>
          <td>${cell(item.chain_slug || item.chain_guess || item.chain_id)}</td>
          <td class="address">${cell(item.address)} ${copyButton(item.address)}</td>
          <td>${cell(item.suggested_role)}</td>
          <td>${cell(item.evidence_type)}</td>
          <td>${cell(item.source_input_type)}</td>
          <td>${cell(item.confidence_initial)}</td>
          <td>${cell(item.status)}</td>
          <td>${cell((item.warnings || []).join(", "))}</td>
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
          <td>${cell(item.id)} ${copyButton(item.id)}</td>
          <td>${cell(item.candidate_id)} ${copyButton(item.candidate_id)}</td>
          <td>${cell(item.evidence_type)}</td>
          <td>${cell(item.source_type)}</td>
          <td>${cell(item.final_source_type)}</td>
          <td>${cell(item.adapter_name)}</td>
          <td>${cell(item.source_url)} ${item.source_url ? copyButton(item.source_url) : ""}</td>
          <td>${cell(item.file_path)}</td>
          <td>${cell(item.confidence_reason)}</td>
          <td>${cell(compactJson(item.payload))}</td>
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
          <td>${cell(item.id)} ${copyButton(item.id)}</td>
          <td>${cell(item.source_job_id)} ${copyButton(item.source_job_id)}</td>
          <td>${cell(item.canonical_source_url)} ${item.canonical_source_url ? copyButton(item.canonical_source_url) : ""}</td>
          <td>${cell(item.file_path)}</td>
          <td>${cell(item.content_type)}</td>
          <td>${cell(item.document_title)}</td>
          <td>${cell(compactJson(item.metadata_json))}</td>
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
    const nonEmpty = value => value === "" ? null : value;
    const buildSourceEvidence = () => {
      const sourceUrl = val("meta_source_url") || (["url", "github"].includes(mode) ? val("source_url") : "");
      const evidence = {
        entity_hint: nonEmpty(val("entity_hint")),
        source_origin: nonEmpty(val("source_origin")),
        source_url: nonEmpty(sourceUrl),
        official_referrer_url: nonEmpty(val("official_referrer_url")),
        provenance_type: nonEmpty(val("provenance_type")),
        evidence_shape: nonEmpty(val("evidence_shape")),
        operator_note: nonEmpty(val("operator_note"))
      };
      Object.keys(evidence).forEach(key => evidence[key] == null && delete evidence[key]);
      return evidence;
    };
    const metadataWarnings = evidence => {
      const warnings = [];
      const filename = document.getElementById("file").files[0]?.name?.toLowerCase() || "";
      const entity = (evidence.entity_hint || "").toLowerCase();
      const origin = (evidence.source_origin || "").toLowerCase();
      const host = (() => { try { return new URL(evidence.source_url || "http://local").hostname.replace(/^www\\./, "").toLowerCase(); } catch { return ""; } })();
      if (filename.includes("binance") && entity && !entity.includes("binance")) warnings.push("filename_entity_may_conflict_with_entity_hint");
      if (origin.includes("binance") && host && !host.includes("binance")) warnings.push("claimed_official_origin_does_not_match_source_url");
      if (host.includes("indodax.com") && entity && !entity.includes("indodax")) warnings.push("source_url_identity_may_conflict_with_entity_hint");
      return warnings;
    };
    const renderSourceEvidence = () => {
      const evidence = buildSourceEvidence();
      document.getElementById("sourceEvidencePreview").textContent = JSON.stringify(evidence, null, 2);
      const warnings = metadataWarnings(evidence);
      const list = document.getElementById("provenanceWarnings");
      list.hidden = warnings.length === 0;
      list.innerHTML = warnings.map(warning => `<li>${cell(warning)}</li>`).join("");
    };
    const applyPreset = preset => {
      const values = {
        cmc_reserves: { source_origin: "CoinMarketCap", provenance_type: "third_party_reserve_snapshot", evidence_shape: "third_party_reserve_page", requested_source_type: "web_docs" },
        cmc_excel: { source_origin: "CoinMarketCap", provenance_type: "third_party_reserve_snapshot", evidence_shape: "processed_excel_wallet_list", requested_source_type: "excel_upload" },
        official_docs: { provenance_type: "official_docs_source", evidence_shape: "docs_deployment_source" },
        official_github: { provenance_type: "official_github_source", evidence_shape: "github_deployment_source" },
        audit_pdf: { provenance_type: "third_party_audit", evidence_shape: "pdf_por_document" }
      }[preset] || {};
      Object.entries(values).forEach(([key, value]) => setVal(key, value));
      renderSourceEvidence();
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
      requested_source_type: val("requested_source_type") || null,
      created_by: val("created_by") || null,
      source_evidence: buildSourceEvidence()
    });
    document.querySelectorAll("[data-mode]").forEach(btn => btn.onclick = () => {
      mode = btn.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach(item => item.classList.toggle("active", item === btn));
      document.getElementById("fileField").hidden = mode !== "upload";
      document.getElementById("urlField").hidden = !["url", "github"].includes(mode);
      document.getElementById("pasteField").hidden = !["paste", "onchain_root"].includes(mode);
      renderSourceEvidence();
    });
    document.querySelectorAll("[data-preset]").forEach(btn => btn.onclick = () => applyPreset(btn.dataset.preset));
    document.querySelectorAll("input, textarea, select").forEach(item => item.addEventListener("input", renderSourceEvidence));
    document.addEventListener("click", event => {
      const target = event.target.closest("[data-copy]");
      if (target) navigator.clipboard?.writeText(target.dataset.copy || "");
    });
    document.getElementById("preview").onclick = async () => {
      try {
        if (mode === "upload") {
          const file = document.getElementById("file").files[0];
          if (!file) return show({ error: "No file selected" });
          const fd = new FormData();
          fd.append("file", file);
          if (val("requested_source_type")) fd.append("requested_source_type", val("requested_source_type"));
          if (val("created_by")) fd.append("created_by", val("created_by"));
          fd.append("source_evidence_json", JSON.stringify(buildSourceEvidence()));
          show(await requestJson("/api/intake/upload/preview", { method: "POST", body: fd }));
          mark("preview", "done");
          return;
        }
        show(await requestJson("/api/intake/preview", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload()) }));
        mark("preview", "done");
      } catch (e) { show({ error: e.message }); }
    };
    document.getElementById("save").onclick = async () => {
      try {
        if (mode === "upload" && !val("preview_id")) {
          const file = document.getElementById("file").files[0];
          if (!file) return show({ error: "No file selected" });
          const fd = new FormData();
          fd.append("file", file);
          if (val("requested_source_type")) fd.append("requested_source_type", val("requested_source_type"));
          if (val("created_by")) fd.append("created_by", val("created_by"));
          fd.append("source_evidence_json", JSON.stringify(buildSourceEvidence()));
          show(await requestJson("/api/intake/upload/jobs", { method: "POST", body: fd }));
        } else {
          show(await requestJson("/api/intake/jobs", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ preview_id: val("preview_id") || null, staged_artifact_id: val("staged_artifact_id") || null, created_by: val("created_by") || null }) }));
        }
        mark("job", "done");
      } catch (e) { show({ error: e.message }); }
    };
    document.getElementById("run").onclick = async () => {
      try { show(await requestJson(`/api/intake/jobs/${val("source_job_id")}/run`, { method: "POST" })); mark("run", "done"); } catch (e) { mark("run", "failed"); show({ error: e.message }); }
    };
    document.getElementById("candidates").onclick = async () => {
      try { show(await requestJson(`/api/intake/jobs/${val("source_job_id")}/candidates`)); mark("candidates", "done"); } catch (e) { mark("candidates", "failed"); show({ error: e.message }); }
    };
    document.getElementById("evidence").onclick = async () => {
      try { show(await requestJson(`/api/intake/jobs/${val("source_job_id")}/evidence`), "evidence"); mark("evidence", "done"); } catch (e) { mark("evidence", "failed"); show({ error: e.message }); }
    };
    document.getElementById("documents").onclick = async () => {
      try { show(await requestJson(`/api/intake/jobs/${val("source_job_id")}/documents`), "documents"); mark("documents", "done"); } catch (e) { mark("documents", "failed"); show({ error: e.message }); }
    };
    const verificationPayload = () => {
      let evidenceJson = {};
      try { evidenceJson = JSON.parse(val("verification_evidence_json") || "{}"); } catch { throw new Error("verification_evidence_json_invalid"); }
      const evidence = buildSourceEvidence();
      return {
        source_job_id: Number(val("source_job_id")) || null,
        entity_name: val("entity_hint") || null,
        source_url: evidence.source_url || null,
        source_origin: evidence.source_origin || null,
        official_referrer_url: evidence.official_referrer_url || null,
        input_method: mode === "upload" ? "upload_file" : mode,
        evidence_shape: evidence.evidence_shape || null,
        verification_scope: val("verification_scope"),
        verification_status: val("verification_status"),
        source_trust: val("verification_source_trust"),
        verified_by: val("verified_by") || null,
        verification_reason: val("verification_reason") || null,
        verification_evidence_json: evidenceJson
      };
    };
    const createVerification = async () => {
      const data = await requestJson("/api/review/source-verifications", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(verificationPayload()) });
      show(data, "none");
      mark("verified", "done");
    };
    document.getElementById("verifySource").onclick = async () => { try { await createVerification(); } catch (e) { mark("verified", "failed"); show({ error: e.message }); } };
    document.getElementById("rejectSource").onclick = async () => {
      setVal("verification_source_trust", "rejected"); setVal("verification_status", "rejected");
      try { await createVerification(); } catch (e) { mark("verified", "failed"); show({ error: e.message }); }
    };
    document.querySelectorAll("[data-trust]").forEach(btn => btn.onclick = async () => {
      setVal("verification_source_trust", btn.dataset.trust); setVal("verification_status", "verified");
      try { await createVerification(); } catch (e) { mark("verified", "failed"); show({ error: e.message }); }
    });
    const reviewPost = async body => requestJson(body.path, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body.payload) });
    document.getElementById("auditJob").onclick = async () => { try { show(await reviewPost({ path: "/api/review/candidate-audit", payload: { source_job_id: Number(val("source_job_id")) || null } })); } catch (e) { show({ error: e.message }); } };
    document.getElementById("candidateGroups").onclick = async () => { try { show(await reviewPost({ path: "/api/review/candidate-groups", payload: { source_job_id: Number(val("source_job_id")) || null } })); } catch (e) { show({ error: e.message }); } };
    const approve = async (dryRun, override) => {
      const data = await reviewPost({ path: "/api/review/approve-candidate-groups", payload: { source_job_id: Number(val("source_job_id")) || null, dry_run: dryRun, allow_review_readiness: override || null, actor: val("verified_by") || "console" } });
      show(data, "none");
      if (!dryRun && Number(data.groups_approved || 0) > 0) mark("approved", "done");
    };
    document.getElementById("approveReadyDry").onclick = async () => { try { await approve(true, null); } catch (e) { show({ error: e.message }); } };
    document.getElementById("approveReadyApply").onclick = async () => { try { await approve(false, null); } catch (e) { mark("approved", "failed"); show({ error: e.message }); } };
    document.getElementById("approveLowDry").onclick = async () => { try { await approve(true, "needs_review_official_low_confidence"); } catch (e) { show({ error: e.message }); } };
    document.getElementById("approveLowApply").onclick = async () => { try { await approve(false, "needs_review_official_low_confidence"); } catch (e) { mark("approved", "failed"); show({ error: e.message }); } };
    document.getElementById("approveHotColdDry").onclick = async () => { try { await approve(true, "needs_review_hot_cold_wallet"); } catch (e) { show({ error: e.message }); } };
    document.getElementById("approveHotColdApply").onclick = async () => { try { await approve(false, "needs_review_hot_cold_wallet"); } catch (e) { mark("approved", "failed"); show({ error: e.message }); } };
    renderWorkflow();
    renderSourceEvidence();
  </script>
</body>
</html>
"""
