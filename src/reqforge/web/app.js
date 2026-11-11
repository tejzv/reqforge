const SAMPLE_CURL = `curl 'https://api.example.com/v2/users/550e8400-e29b-41d4-a716-446655440000/orders?limit=20&utm_source=chrome' \\
  -H 'accept: application/json' \\
  -H 'accept-language: en-US,en;q=0.9' \\
  -H 'authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyXzEifQ.sig' \\
  -H 'sec-ch-ua: "Chromium";v="124"' \\
  -H 'sec-ch-ua-mobile: ?0' \\
  -H 'sec-fetch-dest: empty' \\
  -H 'sec-fetch-mode: cors' \\
  -H 'sec-fetch-site: same-origin' \\
  -H 'user-agent: Mozilla/5.0 Chrome/124.0.0.0' \\
  -b '_ga=GA1.2.123; session_id=sess_secret_987; _fbp=fb.1.999' \\
  --compressed`;

const SAMPLE_HAR = JSON.stringify(
  {
    log: {
      version: "1.2",
      entries: [
        {
          _resourceType: "stylesheet",
          request: { method: "GET", url: "https://cdn.example.com/app.css", headers: [] },
          response: { status: 200, content: { mimeType: "text/css", text: "body{}" } }
        },
        {
          _resourceType: "ping",
          request: { method: "POST", url: "https://www.google-analytics.com/g/collect", headers: [] },
          response: { status: 204, content: { mimeType: "text/plain", text: "" } }
        },
        {
          _resourceType: "fetch",
          request: {
            method: "GET",
            url: "https://api.example.com/v1/projects/42/tasks?status=open&utm_medium=web",
            headers: [
              { name: "Authorization", value: "Bearer demo_tok_123456" },
              { name: "sec-fetch-mode", value: "cors" },
              { name: "user-agent", value: "Mozilla/5.0" }
            ],
            cookies: [
              { name: "_ga", value: "GA1.1.123" },
              { name: "sid", value: "s_998877" }
            ]
          },
          response: {
            status: 200,
            content: {
              mimeType: "application/json",
              text: JSON.stringify({
                items: [{ id: 101, title: "Ship ReqForge", done: true }],
                nextCursor: "cur_2"
              })
            }
          }
        },
        {
          _resourceType: "fetch",
          request: {
            method: "POST",
            url: "https://api.example.com/v1/projects/42/tasks",
            headers: [
              { name: "Authorization", value: "Bearer demo_tok_123456" },
              { name: "Content-Type", value: "application/json" },
              { name: "priority", value: "u=1, i" }
            ],
            postData: {
              mimeType: "application/json",
              text: JSON.stringify({ title: "Add tests", priority: 1 })
            }
          },
          response: {
            status: 201,
            content: {
              mimeType: "application/json",
              text: JSON.stringify({ id: 102, title: "Add tests", done: false })
            }
          }
        }
      ]
    }
  },
  null,
  2
);

const state = {
  mode: "detecting",
  pyodide: null,
  sourceName: "",
  flatFiles: [],
  activeFileIdx: 0,
  analysis: null
};

const els = {
  runtimeBadge: document.getElementById("runtime-badge"),
  rawInput: document.getElementById("raw-input"),
  harFileInput: document.getElementById("har-file-input"),
  targetSelect: document.getElementById("target-select"),
  transportSelect: document.getElementById("transport-select"),
  clientName: document.getElementById("client-name"),
  baseUrl: document.getElementById("base-url"),
  dropStatic: document.getElementById("opt-drop-static"),
  dropTrackers: document.getElementById("opt-drop-trackers"),
  dropOptions: document.getElementById("opt-drop-options"),
  dropHtml: document.getElementById("opt-drop-html"),
  singleFile: document.getElementById("opt-single-file"),
  liveProbe: document.getElementById("opt-live-probe"),
  probeQuery: document.getElementById("opt-probe-query"),
  btnForge: document.getElementById("btn-forge"),
  errorBox: document.getElementById("error-box"),
  summaryStats: document.getElementById("summary-stats"),
  fileTabs: document.getElementById("file-tabs"),
  codeOutput: document.getElementById("code-output"),
  btnCopy: document.getElementById("btn-copy"),
  btnDownload: document.getElementById("btn-download"),
  btnDownloadZip: document.getElementById("btn-download-zip"),
  endpointsList: document.getElementById("endpoints-list"),
  auditView: document.getElementById("audit-view")
};

async function detectRuntime() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 1200);
    const resp = await fetch("/api/health", { signal: controller.signal });
    clearTimeout(timer);
    if (resp.ok) {
      const data = await resp.json();
      state.mode = "server";
      const cffiTag = data?.capabilities?.curl_cffi ? " + curl_cffi" : "";
      els.runtimeBadge.textContent = `Local Python Server (Live Probe${cffiTag})`;
      els.runtimeBadge.className = "badge badge-ok";
      els.liveProbe.disabled = false;
      els.probeQuery.disabled = false;
      return;
    }
  } catch {
  }
  state.mode = "pyodide";
  els.runtimeBadge.textContent = "Static Browser Mode (Offline Pyodide)";
  els.runtimeBadge.className = "badge badge-static";
  els.liveProbe.checked = false;
  els.liveProbe.disabled = true;
  els.probeQuery.checked = false;
  els.probeQuery.disabled = true;
}

async function ensurePyodideEngine() {
  if (state.pyodide) return state.pyodide;
  els.runtimeBadge.textContent = "Loading Pyodide engine...";
  if (!window.loadPyodide) {
    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/pyodide.js";
      s.onload = resolve;
      s.onerror = () => reject(new Error("Failed to load Pyodide CDN"));
      document.head.appendChild(s);
    });
  }
  const pyodide = await window.loadPyodide();
  const zipResp = await fetch("reqforge_core.zip");
  if (!zipResp.ok) {
    throw new Error("reqforge_core.zip missing for static mode");
  }
  const buf = await zipResp.arrayBuffer();
  pyodide.unpackArchive(buf, "zip");
  state.pyodide = pyodide;
  els.runtimeBadge.textContent = "Static Browser Mode (Ready)";
  return pyodide;
}

function collectPayload() {
  return {
    input: els.rawInput.value,
    sourceName: state.sourceName,
    target: els.targetSelect.value,
    transport: els.transportSelect.value,
    clientName: els.clientName.value.trim(),
    baseUrl: els.baseUrl.value.trim(),
    singleFile: els.singleFile.checked,
    probe: state.mode === "server" && els.liveProbe.checked,
    probeConfig: {
      minimizeQuery: state.mode === "server" && els.probeQuery.checked
    },
    filter: {
      dropStatic: els.dropStatic.checked,
      dropTrackers: els.dropTrackers.checked,
      dropOptions: els.dropOptions.checked,
      dropHtml: els.dropHtml.checked
    }
  };
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderResult(data) {
  state.analysis = data.analysis;
  const flat = [];
  const targetEntries = Object.entries(data.files || {});
  const multi = targetEntries.length > 1;
  for (const [targetName, files] of targetEntries) {
    for (const f of files) {
      flat.push({
        label: multi ? `${targetName}/${f.path}` : f.path,
        path: f.path,
        content: f.content
      });
    }
  }
  state.flatFiles = flat;
  state.activeFileIdx = 0;

  const a = data.analysis;
  els.summaryStats.textContent =
    `${a.total_ingested} ingested · ${a.kept_endpoints} endpoints · ${a.filtered_count} filtered · ${a.recommended_transport}`;

  renderCodeTab();
  renderEndpointsTab();
  renderAuditTab();
}

function renderCodeTab() {
  els.fileTabs.innerHTML = "";
  if (!state.flatFiles.length) {
    els.codeOutput.textContent = "No files generated.";
    els.btnCopy.disabled = true;
    els.btnDownload.disabled = true;
    els.btnDownloadZip.disabled = true;
    return;
  }

  state.flatFiles.forEach((file, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = idx === state.activeFileIdx ? "file-pill active" : "file-pill";
    btn.textContent = file.label;
    btn.addEventListener("click", () => {
      state.activeFileIdx = idx;
      renderCodeTab();
    });
    els.fileTabs.appendChild(btn);
  });

  const active = state.flatFiles[state.activeFileIdx];
  els.codeOutput.textContent = active.content;
  els.btnCopy.disabled = false;
  els.btnDownload.disabled = false;
  els.btnDownloadZip.disabled = false;
}

function summarizeSchemaFields(schema) {
  if (!schema) return "";
  if (schema.kind === "object" && schema.fields) {
    const entries = Object.values(schema.fields).slice(0, 12);
    const chips = entries
      .map((f) => {
        const opt = f.required ? "" : "?";
        const nul = f.nullable ? " | null" : "";
        const typ = f.schema?.model_name || f.schema?.kind || "any";
        return `<span class="chip">${escapeHtml(f.wire_name)}${opt}: ${escapeHtml(typ)}${nul}</span>`;
      })
      .join("");
    return `<div class="chip-row">${chips}</div>`;
  }
  if (schema.kind === "array" && schema.items) {
    return summarizeSchemaFields(schema.items);
  }
  return "";
}

function renderEndpointsTab() {
  const endpoints = state.analysis?.endpoints || [];
  if (!endpoints.length) {
    els.endpointsList.innerHTML = `<div class="card"><p class="meta-line">No endpoints kept.</p></div>`;
    return;
  }

  els.endpointsList.innerHTML = endpoints
    .map((ep) => {
      const pathParams = ep.path_params.map((p) => `${p.name}: ${p.inferred_type}`).join(", ");
      const queryParams = ep.query_params.map((p) => `${p.wire_name}: ${p.inferred_type}`).join(", ");
      const reqFields = summarizeSchemaFields(ep.request_schema);
      const respFields = summarizeSchemaFields(ep.response_schema);
      return `
        <div class="card">
          <h3><span class="method-pill">${escapeHtml(ep.method)}</span> ${escapeHtml(ep.path_template)}</h3>
          <div class="meta-line">Method: <code>${escapeHtml(ep.operation_id)}</code> &middot; Samples: ${ep.sample_count}</div>
          ${pathParams ? `<div class="meta-line">Path params: <code>${escapeHtml(pathParams)}</code></div>` : ""}
          ${queryParams ? `<div class="meta-line">Query params: <code>${escapeHtml(queryParams)}</code></div>` : ""}
          <div class="meta-line">Models: req=<code>${escapeHtml(ep.request_model_name || "-")}</code> &middot; resp=<code>${escapeHtml(ep.response_model_name || "-")}</code></div>
          ${reqFields ? `<div class="meta-line" style="margin-top:6px;">Request fields</div>${reqFields}` : ""}
          ${respFields ? `<div class="meta-line" style="margin-top:6px;">Response fields</div>${respFields}` : ""}
        </div>
      `;
    })
    .join("");
}

function renderAuditTab() {
  const a = state.analysis;
  if (!a) {
    els.auditView.innerHTML = "";
    return;
  }

  const warningsHtml = a.warnings?.length
    ? `<div class="card"><h3>Warnings</h3>${a.warnings.map((w) => `<div class="meta-line" style="color:#fcd34d;">&bull; ${escapeHtml(w)}</div>`).join("")}</div>`
    : "";

  const filteredSummary = {};
  for (const item of a.filtered_out || []) {
    filteredSummary[item.reason] = (filteredSummary[item.reason] || 0) + 1;
  }
  const filteredChips = Object.entries(filteredSummary)
    .map(([reason, count]) => `<span class="chip chip-dropped">${escapeHtml(reason)}: ${count}</span>`)
    .join("");
  const filteredHtml = filteredChips
    ? `<div class="card"><h3>Filtered Noise (${a.filtered_count})</h3><div class="chip-row">${filteredChips}</div></div>`
    : "";

  const authHtml = a.auth_findings.length
    ? a.auth_findings
        .map(
          (af) =>
            `<span class="chip chip-auth">[${escapeHtml(af.kind)}] ${escapeHtml(af.location)}:${escapeHtml(af.name)} &rarr; ${escapeHtml(af.env_var)} (${escapeHtml(af.masked_preview)})</span>`
        )
        .join("")
    : `<span class="meta-line">No auth credentials detected.</span>`;

  const endpointAudit = a.endpoints
    .map((ep) => {
      const keptH = Object.keys(ep.required_headers)
        .map((h) => `<span class="chip chip-kept">${escapeHtml(h)}</span>`)
        .join("");
      const dropH = ep.dropped_headers
        .map((h) => `<span class="chip chip-dropped">${escapeHtml(h)}</span>`)
        .join("");
      const keptC = Object.keys(ep.required_cookies)
        .map((c) => `<span class="chip chip-kept">${escapeHtml(c)}</span>`)
        .join("");
      const dropC = ep.dropped_cookies
        .map((c) => `<span class="chip chip-dropped">${escapeHtml(c)}</span>`)
        .join("");
      const probeMeta = ep.probe_report
        ? `<div class="meta-line">Live Probe: status ${ep.probe_report.baseline_status} &middot; ${ep.probe_report.total_probes} request(s) &middot; recommended <code>${escapeHtml(ep.probe_report.recommended_transport)}</code></div>`
        : "";
      const droppedQ = ep.probe_report?.removed_query_params?.length
        ? `<div class="meta-line" style="margin-top:8px;">Stripped Query Params</div><div class="chip-row">${ep.probe_report.removed_query_params.map((q) => `<span class="chip chip-dropped">${escapeHtml(q)}</span>`).join("")}</div>`
        : "";
      return `
        <div class="card">
          <h3>${escapeHtml(ep.operation_id)} (${escapeHtml(ep.method)} ${escapeHtml(ep.path_template)})</h3>
          ${probeMeta}
          <div class="meta-line">Headers</div>
          <div class="chip-row">${keptH || `<span class="meta-line">None kept</span>`}${dropH}</div>
          <div class="meta-line" style="margin-top:8px;">Cookies</div>
          <div class="chip-row">${keptC || `<span class="meta-line">None kept</span>`}${dropC}</div>
          ${droppedQ}
        </div>
      `;
    })
    .join("");

  els.auditView.innerHTML = `
    ${warningsHtml}
    ${filteredHtml}
    <div class="card">
      <h3>Detected Auth &amp; Secrets</h3>
      <div class="chip-row">${authHtml}</div>
    </div>
    ${endpointAudit}
  `;
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) {
    crc ^= bytes[i];
    for (let j = 0; j < 8; j++) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function buildZipBlob(files) {
  const encoder = new TextEncoder();
  const localChunks = [];
  const centralChunks = [];
  let offset = 0;

  for (const f of files) {
    const nameBytes = encoder.encode(f.label);
    const dataBytes = encoder.encode(f.content);
    const crc = crc32(dataBytes);

    const localHeader = new ArrayBuffer(30);
    const lv = new DataView(localHeader);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, 0x0800, true);
    lv.setUint16(8, 0, true);
    lv.setUint16(10, 0, true);
    lv.setUint16(12, 0, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, dataBytes.length, true);
    lv.setUint32(22, dataBytes.length, true);
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);

    localChunks.push(new Uint8Array(localHeader), nameBytes, dataBytes);

    const centralHeader = new ArrayBuffer(46);
    const cv = new DataView(centralHeader);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x0800, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, 0, true);
    cv.setUint16(14, 0, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, dataBytes.length, true);
    cv.setUint32(24, dataBytes.length, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true);
    cv.setUint16(32, 0, true);
    cv.setUint16(34, 0, true);
    cv.setUint16(36, 0, true);
    cv.setUint32(38, 0, true);
    cv.setUint32(42, offset, true);

    centralChunks.push(new Uint8Array(centralHeader), nameBytes);
    offset += 30 + nameBytes.length + dataBytes.length;
  }

  let centralSize = 0;
  for (const c of centralChunks) centralSize += c.length;

  const endRecord = new ArrayBuffer(22);
  const ev = new DataView(endRecord);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(4, 0, true);
  ev.setUint16(6, 0, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true);
  ev.setUint16(20, 0, true);

  return new Blob([...localChunks, ...centralChunks, new Uint8Array(endRecord)], {
    type: "application/zip"
  });
}

async function runForge() {
  els.errorBox.hidden = true;
  if (!els.rawInput.value.trim()) {
    els.errorBox.textContent = "Paste cURL or HAR input first.";
    els.errorBox.hidden = false;
    return;
  }

  els.btnForge.disabled = true;
  els.btnForge.textContent = "Forging...";
  try {
    const payload = collectPayload();
    if (state.mode === "server") {
      const resp = await fetch("/api/forge", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Forge failed");
      renderResult(data);
    } else {
      const pyodide = await ensurePyodideEngine();
      pyodide.globals.set("REQFORGE_PAYLOAD_JSON", JSON.stringify(payload));
      const outJson = await pyodide.runPythonAsync(
        "from reqforge.pipeline import forge_json_sync\nforge_json_sync(REQFORGE_PAYLOAD_JSON)"
      );
      renderResult(JSON.parse(outJson));
    }
  } catch (err) {
    els.errorBox.textContent = err?.message || String(err);
    els.errorBox.hidden = false;
  } finally {
    els.btnForge.disabled = false;
    els.btnForge.textContent = "Forge SDK";
  }
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

document.getElementById("btn-sample-curl").addEventListener("click", () => {
  state.sourceName = "sample.curl";
  els.rawInput.value = SAMPLE_CURL;
});

document.getElementById("btn-sample-har").addEventListener("click", () => {
  state.sourceName = "sample.har";
  els.rawInput.value = SAMPLE_HAR;
});

els.harFileInput.addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  state.sourceName = file.name;
  els.rawInput.value = await file.text();
});

els.rawInput.addEventListener("dragover", (e) => {
  e.preventDefault();
  els.rawInput.classList.add("drag-over");
});

els.rawInput.addEventListener("dragleave", () => {
  els.rawInput.classList.remove("drag-over");
});

els.rawInput.addEventListener("drop", async (e) => {
  e.preventDefault();
  els.rawInput.classList.remove("drag-over");
  const file = e.dataTransfer?.files?.[0];
  if (!file) return;
  state.sourceName = file.name;
  els.rawInput.value = await file.text();
});

els.btnCopy.addEventListener("click", async () => {
  const active = state.flatFiles[state.activeFileIdx];
  if (!active) return;
  await navigator.clipboard.writeText(active.content);
  els.btnCopy.textContent = "Copied!";
  setTimeout(() => {
    els.btnCopy.textContent = "Copy";
  }, 1200);
});

els.btnDownload.addEventListener("click", () => {
  const active = state.flatFiles[state.activeFileIdx];
  if (!active) return;
  const blob = new Blob([active.content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = active.path.split("/").pop() || "sdk.txt";
  a.click();
  URL.revokeObjectURL(url);
});

els.btnDownloadZip.addEventListener("click", () => {
  if (!state.flatFiles.length) return;
  const blob = buildZipBlob(state.flatFiles);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const name = (state.analysis?.client_name || "reqforge").toLowerCase();
  a.href = url;
  a.download = `${name}-sdk.zip`;
  a.click();
  URL.revokeObjectURL(url);
});

els.rawInput.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    e.preventDefault();
    runForge();
  }
});

els.btnForge.addEventListener("click", runForge);

detectRuntime();
