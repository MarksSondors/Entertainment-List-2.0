// Top-level controller: wires state → API → tree/filters/results/status.

import { api } from "./api.js";
import { initState, getState, setState, subscribe, toApiParams } from "./state.js";
import { renderTree } from "./tree.js";
import { renderFilters, SORT_FIELDS } from "./filters.js";
import { renderResults } from "./grid.js";

const ICONS_BASE = document.getElementById("explorer-app").dataset.iconsBase;

// ---- DOM refs ---------------------------------------------------------------
const treeEl = document.querySelector('[data-role="tree"]');
const filtersEl = document.querySelector('[data-role="filters"]');
const resultsEl = document.querySelector('[data-role="results"]');
const breadcrumbsEl = document.querySelector('[data-role="breadcrumbs"]');
const searchInput = document.querySelector('[data-role="search-input"]');
const viewModeSelect = document.querySelector('[data-role="view-mode"]');
const sortFieldSelect = document.querySelector('[data-role="sort-field"]');
const sortDirectionBtn = document.querySelector('[data-role="sort-direction"]');
const sortArrow = document.querySelector('[data-role="sort-arrow"]');
const statusCountEl = document.querySelector('[data-role="status-count"]');
const statusPageEl = document.querySelector('[data-role="status-page"]');
const statusTimingEl = document.querySelector('[data-role="status-timing"]');

// ---- caches -----------------------------------------------------------------
let treePayload = null;

// ---- utils ------------------------------------------------------------------
function findBranch(mediaType) {
    return treePayload?.media_types.find((b) => b.media_type === mediaType) || null;
}

function updateBreadcrumbs() {
    const state = getState();
    breadcrumbsEl.innerHTML = "";

    const rootCrumb = document.createElement("span");
    rootCrumb.className = "crumb";
    const rootImg = document.createElement("img");
    rootImg.src = `${ICONS_BASE}computer.png`;
    rootImg.alt = "";
    rootCrumb.appendChild(rootImg);
    rootCrumb.appendChild(document.createTextNode("My Library"));
    rootCrumb.addEventListener("click", () => setState({ mediaType: "movies", folderKey: "all", folderValue: "" }));
    breadcrumbsEl.appendChild(rootCrumb);

    const branch = findBranch(state.mediaType);
    if (branch) {
        const typeCrumb = document.createElement("span");
        typeCrumb.className = "crumb";
        const img = document.createElement("img");
        img.src = `${ICONS_BASE}${branch.icon || "directory_closed"}.png`;
        img.alt = "";
        typeCrumb.appendChild(img);
        typeCrumb.appendChild(document.createTextNode(branch.label));
        typeCrumb.addEventListener("click", () => setState({ folderKey: "all", folderValue: "" }));
        breadcrumbsEl.appendChild(typeCrumb);
    }

    if (state.folderKey && state.folderKey !== "all") {
        const child = branch?.children.find((c) => c.key === state.folderKey);
        const label = child?.label || state.folderKey;
        const crumb = document.createElement("span");
        crumb.className = "crumb";
        crumb.textContent = label;
        breadcrumbsEl.appendChild(crumb);

        if (state.folderValue) {
            const leaf = (child?.children || []).find(
                (c) => String(c.id ?? c.value ?? c.key) === String(state.folderValue)
            );
            const leafCrumb = document.createElement("span");
            leafCrumb.className = "crumb";
            leafCrumb.textContent = leaf?.name || leaf?.label || state.folderValue;
            breadcrumbsEl.appendChild(leafCrumb);
        }
    }
}

function updateSortDropdown() {
    const state = getState();
    const fields = SORT_FIELDS[state.mediaType] || [];
    sortFieldSelect.innerHTML = "";
    for (const f of fields) {
        const opt = document.createElement("option");
        opt.value = f.value;
        opt.textContent = f.label;
        if (f.value === state.sort) opt.selected = true;
        sortFieldSelect.appendChild(opt);
    }
    if (!fields.find((f) => f.value === state.sort) && fields[0]) {
        // Current sort field isn't valid for this media; snap to the first.
        setState({ sort: fields[0].value }, { push: true, notify: false });
    }
    sortArrow.textContent = state.direction === "desc" ? "▼" : "▲";
    viewModeSelect.value = state.view;
    searchInput.value = state.q || "";
}

function updateStatus(response, elapsedMs) {
    const count = response.count ?? (response.results ? response.results.length : 0);
    statusCountEl.textContent = `${count} item${count === 1 ? "" : "s"}`;

    const pageSize = response.page_size || 48;
    const totalPages = Math.max(1, Math.ceil(count / pageSize));
    statusPageEl.textContent = `Page ${getState().page} of ${totalPages}`;

    if (elapsedMs != null) {
        statusTimingEl.textContent = `${elapsedMs} ms`;
    }
}

// ---- data flow --------------------------------------------------------------
async function loadTree() {
    treePayload = await api.tree();
    renderTree(treePayload, treeEl);
    renderFilters(filtersEl, treePayload);
    updateBreadcrumbs();
}

let inflight = 0;

async function loadResults() {
    const state = getState();
    const seq = ++inflight;
    resultsEl.innerHTML = '<div class="pane-loading">Loading…</div>';
    const started = performance.now();
    try {
        const params = toApiParams(state);
        const response = await api.list(state.mediaType, params);
        if (seq !== inflight) return; // superseded
        const elapsed = Math.round(performance.now() - started);
        renderResults(resultsEl, response, state.mediaType);
        updateStatus(response, elapsed);
    } catch (err) {
        console.error(err);
        resultsEl.innerHTML = `<div class="pane-loading">Error: ${err.message}</div>`;
    }
}

function applyStateChange() {
    updateSortDropdown();
    renderTree(treePayload, treeEl);
    renderFilters(filtersEl, treePayload);
    updateBreadcrumbs();
    loadResults();
}

// ---- toolbar wiring ---------------------------------------------------------
viewModeSelect.addEventListener("change", () => setState({ view: viewModeSelect.value }));

sortFieldSelect.addEventListener("change", () => setState({ sort: sortFieldSelect.value, page: 1 }));

sortDirectionBtn.addEventListener("click", () => {
    const dir = getState().direction === "desc" ? "asc" : "desc";
    setState({ direction: dir, page: 1 });
});

let searchHandle;
searchInput.addEventListener("input", () => {
    clearTimeout(searchHandle);
    searchHandle = setTimeout(() => setState({ q: searchInput.value.trim(), page: 1 }), 250);
});

document.querySelector('[data-action="back"]').addEventListener("click", () => window.history.back());
document.querySelector('[data-action="forward"]').addEventListener("click", () => window.history.forward());
document.querySelector('[data-action="up"]').addEventListener("click", () => {
    const state = getState();
    if (state.folderValue) setState({ folderValue: "" });
    else if (state.folderKey !== "all") setState({ folderKey: "all", folderValue: "" });
});
document.querySelector('[data-action="refresh"]').addEventListener("click", async () => {
    await loadTree();
    loadResults();
});

// ---- boot -------------------------------------------------------------------
initState();
subscribe(applyStateChange);

(async function boot() {
    try {
        await loadTree();
    } catch (err) {
        console.error(err);
        treeEl.innerHTML = `<div class="pane-loading">Failed to load tree: ${err.message}</div>`;
    }
    updateSortDropdown();
    loadResults();
})();
