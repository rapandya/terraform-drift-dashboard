const ENVIRONMENTS = ["dev", "qa", "perf", "prod"];
let currentStatusFilter = "all";

// Cache for popup modal contents
const scanLogCache = {};

document.addEventListener("DOMContentLoaded", () => {
  fetchResults();

  const scanBtn = document.getElementById("scan-btn");
  if (scanBtn) {
    scanBtn.addEventListener("click", triggerScan);
  }

  const filterInput = document.getElementById("table-filter");
  if (filterInput) {
    filterInput.addEventListener("input", filterTable);
  }

  const filterBtns = document.querySelectorAll(".filter-btn");
  filterBtns.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      filterBtns.forEach((b) => b.classList.remove("active"));
      e.target.classList.add("active");
      currentStatusFilter = e.target.dataset.status;
      filterTable();
    });
  });

  const modalOverlay = document.getElementById("output-modal");
  if (modalOverlay) {
    modalOverlay.addEventListener("click", (e) => {
      if (e.target === modalOverlay) closeModal();
    });
  }
});

async function fetchResults() {
  try {
    const response = await fetch("/api/results");
    const data = await response.json();

    updateScanButton(data.is_scanning);
    renderDashboard(data);

    if (data.is_scanning) {
      setTimeout(fetchResults, 2000);
    }
  } catch (err) {
    console.error("Failed to fetch scan results:", err);
  }
}

async function triggerScan() {
  try {
    const scanBtn = document.getElementById("scan-btn");
    if (scanBtn) {
      scanBtn.disabled = true;
      scanBtn.textContent = "Scanning...";
    }

    const response = await fetch("/api/scan/trigger", { method: "POST" });
    if (response.ok) {
      fetchResults();
    } else {
      const err = await response.json();
      alert(err.message || "Failed to trigger scan");
      if (scanBtn) {
        scanBtn.disabled = false;
        scanBtn.textContent = "Run Scan";
      }
    }
  } catch (err) {
    console.error("Error triggering scan:", err);
    const scanBtn = document.getElementById("scan-btn");
    if (scanBtn) {
      scanBtn.disabled = false;
      scanBtn.textContent = "Run Scan";
    }
  }
}

function updateScanButton(isScanning) {
  const scanBtn = document.getElementById("scan-btn");
  const scanStatusText = document.getElementById("scan-status-text");

  if (scanBtn) {
    if (isScanning) {
      scanBtn.disabled = true;
      scanBtn.textContent = "Scanning in background...";
      if (scanStatusText) scanStatusText.textContent = "Scanning...";
    } else {
      scanBtn.disabled = false;
      scanBtn.textContent = "Run Scan";
      if (scanStatusText) scanStatusText.textContent = "Ready";
    }
  }
}

function renderDashboard(data) {
  const tbody = document.getElementById("drift-table-body");
  if (!tbody) return;

  tbody.innerHTML = "";

  // Reset popup log cache
  Object.keys(scanLogCache).forEach((k) => delete scanLogCache[k]);

  const envStats = {
    dev: { total: 0, drift: 0 },
    qa: { total: 0, drift: 0 },
    perf: { total: 0, drift: 0 },
    prod: { total: 0, drift: 0 },
  };

  const statusCounts = {
    all: 0,
    in_sync: 0,
    drift_detected: 0,
    error: 0,
    not_scanned: 0,
  };

  if (data.projects && Object.keys(data.projects).length > 0) {
    let rowIndex = 0;

    for (const [projectName, projData] of Object.entries(data.projects)) {
      const roles = projData.roles || [];
      const envs = projData.environments || {};

      roles.forEach((rolePath) => {
        rowIndex++;
        statusCounts.all++;

        let overallRoleStatus = "In Sync";
        let roleHasScan = false;

        ENVIRONMENTS.forEach((env) => {
          const envInfo = envs[env] || {};
          const roleResults = envInfo.role_results || {};
          const roleRes = roleResults[rolePath] || {};
          const status = roleRes.status || "Not Scanned";

          if (status !== "Not Scanned") {
            roleHasScan = true;
            if (envStats[env]) envStats[env].total++;
          }

          if (status === "Drift Detected") {
            if (envStats[env]) envStats[env].drift++;
            if (overallRoleStatus !== "Error") overallRoleStatus = "Drift Detected";
          } else if (status === "Error") {
            overallRoleStatus = "Error";
          }
        });

        if (!roleHasScan) {
          overallRoleStatus = "Not Scanned";
        }

        const statusKey = normalizeStatusKey(overallRoleStatus);
        if (statusCounts[statusKey] !== undefined) {
          statusCounts[statusKey]++;
        }

        const row = document.createElement("tr");
        row.className = "drift-row hover:bg-slate-50 transition-colors";
        row.dataset.status = statusKey;
        row.dataset.search = rolePath.toLowerCase();

        let rowHtml = `
          <td class="py-3 px-4 font-semibold text-slate-800">
            ${escapeHtml(rolePath)}
          </td>
        `;

        ENVIRONMENTS.forEach((env) => {
          const envInfo = envs[env] || {};
          const roleResults = envInfo.role_results || {};
          const roleRes = roleResults[rolePath] || {};

          const envStatus = roleRes.status || "Not Scanned";
          const cacheKey = `${rolePath}::${env}`;
          scanLogCache[cacheKey] = roleRes.summary || envInfo.summary || "No output captured.";

          // Only render View Output button when scan executed
          const viewOutputBtn = envStatus !== "Not Scanned"
            ? `<button class="btn-sm" onclick="openModal('${escapeJsString(rolePath)}', '${env}')">View Output</button>`
            : "";

          rowHtml += `
            <td class="py-3 px-4 text-center">
              <div class="inline-flex items-center justify-center gap-2">
                ${getStatusBadge(envStatus)}
                ${viewOutputBtn}
              </div>
            </td>
          `;
        });

        row.innerHTML = rowHtml;
        tbody.appendChild(row);
      });
    }
  } else {
    tbody.innerHTML = `<tr><td colspan="5" class="py-8 text-center text-slate-400">No projects configured. Click "Run Scan" to execute.</td></tr>`;
  }

  updateFilterButtonLabels(statusCounts);

  ENVIRONMENTS.forEach((env) => {
    const countEl = document.getElementById(`count-${env}`);
    const statusEl = document.getElementById(`status-${env}`);
    const cardEl = document.getElementById(`card-${env}`);

    if (countEl) countEl.textContent = envStats[env].total;

    if (statusEl && cardEl) {
      cardEl.classList.remove("border-amber-400", "border-green-400");
      if (envStats[env].total === 0) {
        statusEl.textContent = "Not Scanned";
      } else if (envStats[env].drift > 0) {
        statusEl.textContent = `${envStats[env].drift} Drifted`;
        cardEl.classList.add("border-amber-400");
      } else {
        statusEl.textContent = "In Sync";
        cardEl.classList.add("border-green-400");
      }
    }
  });

  const totalEl = document.getElementById("count-total");
  if (totalEl) totalEl.textContent = statusCounts.all;

  filterTable();
}

function openModal(rolePath, env) {
  const modal = document.getElementById("output-modal");
  const titleEl = document.getElementById("modal-title");
  const logEl = document.getElementById("modal-log");

  if (!modal || !titleEl || !logEl) return;

  const cacheKey = `${rolePath}::${env}`;
  const outputText = scanLogCache[cacheKey] || "No output captured.";

  titleEl.textContent = `${rolePath} [${env.toUpperCase()}]`;
  logEl.textContent = outputText;

  modal.style.display = "flex";
}

function closeModal() {
  const modal = document.getElementById("output-modal");
  const logEl = document.getElementById("modal-log");
  if (modal) modal.style.display = "none";
  if (logEl) logEl.textContent = "";
}

function updateFilterButtonLabels(counts) {
  const labels = {
    all: `All (${counts.all})`,
    in_sync: `In Sync (${counts.in_sync})`,
    drift_detected: `Drift Detected (${counts.drift_detected})`,
    error: `Error (${counts.error})`,
    not_scanned: `Not Scanned (${counts.not_scanned})`,
  };

  document.querySelectorAll(".filter-btn").forEach((btn) => {
    const status = btn.dataset.status;
    if (labels[status]) {
      btn.textContent = labels[status];
    }
  });
}

function filterTable() {
  const query = document.getElementById("table-filter")?.value.toLowerCase().trim() || "";
  const rows = document.querySelectorAll("#drift-table-body .drift-row");

  rows.forEach((row) => {
    const searchText = row.dataset.search || "";
    const rowStatus = row.dataset.status || "";

    const textMatch = searchText.includes(query);
    const statusMatch = currentStatusFilter === "all" || rowStatus === currentStatusFilter;

    row.style.display = textMatch && statusMatch ? "" : "none";
  });
}

function getStatusBadge(status) {
  switch (status) {
    case "In Sync":
    case "in_sync":
      return `<span class="bg-green-100 text-green-800 border border-green-300 px-2.5 py-0.5 rounded-full text-xs font-medium inline-block">In Sync</span>`;
    case "Drift Detected":
    case "drift_detected":
      return `<span class="bg-amber-100 text-amber-800 border border-amber-300 px-2.5 py-0.5 rounded-full text-xs font-medium inline-block">Drift Detected</span>`;
    case "Error":
    case "error":
      return `<span class="bg-red-100 text-red-800 border border-red-300 px-2.5 py-0.5 rounded-full text-xs font-medium inline-block">Error</span>`;
    default:
      return `<span class="bg-slate-100 text-slate-600 border border-slate-200 px-2.5 py-0.5 rounded-full text-xs font-medium inline-block">Not Scanned</span>`;
  }
}

function normalizeStatusKey(status) {
  switch (status) {
    case "In Sync":
      return "in_sync";
    case "Drift Detected":
      return "drift_detected";
    case "Error":
      return "error";
    default:
      return "not_scanned";
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeJsString(str) {
  return String(str).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}