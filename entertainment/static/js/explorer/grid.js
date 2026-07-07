// Result grid renderer — Large Icons / Small Icons / List / Details.

import { DETAIL_COLUMNS } from "./filters.js";
import { getState, setState } from "./state.js";

const ICONS_BASE = document.getElementById("explorer-app").dataset.iconsBase;

const FALLBACK_THUMB = {
    movies: "camera_vid.png",
    tvshows: "network_television.png",
    books: "document.png",
    games: "solitaire.png",
    people: "user_card.png",
};

function fallbackFor(mediaType) {
    return ICONS_BASE + (FALLBACK_THUMB[mediaType] || "document.png");
}

function detailPath(item) {
    switch (item.media_type) {
        case "movies": return `/movies/${item.tmdb_id || item.id}/`;
        case "tvshows": return `/tvshows/${item.tmdb_id || item.id}/`;
        case "books": return `/books/${item.id}/`;
        case "games": return `/games/${item.id}/`;
        case "people": return `/people/${item.id}/`;
        default: return "#";
    }
}

function cardTitle(item) {
    return item.title || item.name || "Untitled";
}

// Prefer the aggregated user-review rating; fall back to the external
// (TMDB / Hardcover / RAWG) rating if no user has reviewed it yet.
function effectiveRating(item) {
    if (item.user_rating != null) return Number(item.user_rating);
    if (item.rating != null) return Number(item.rating);
    return null;
}

// Border colour signals *engagement*, matching the existing convention on
// the all_movies / watchlist pages:
//   reviewed-by-me     → blue    (#2196F3)
//   on-my-watchlist    → green   (#4CAF50)
//   reviewed-by-others → purple  (#9C27B0)
//   undiscovered       → orange  (#FF9800)
// People (no watchlist/review) fall through to the neutral tier.
function engagementTier(item) {
    if (item.reviewed_by_me) return "tier-reviewed";
    if (item.on_my_watchlist) return "tier-watchlist";
    if (item.user_rating_count && item.user_rating_count > 0) return "tier-others";
    if (item.media_type === "people") return "tier-neutral";
    return "tier-undiscovered";
}

function ratingSource(item) {
    // Small marker so the UI hints whether the shown rating is from users
    // (★) or from the external API (☆).
    if (item.user_rating != null) return "★";
    if (item.rating != null) return "☆";
    return "";
}

function metaText(item) {
    const parts = [];
    if (item.year) parts.push(item.year);
    const rating = effectiveRating(item);
    if (rating != null) {
        let label = `${ratingSource(item)} ${rating.toFixed(1)}`;
        if (item.user_rating_count) label += ` (${item.user_rating_count})`;
        parts.push(label);
    }
    return parts.join(" · ");
}

function renderCards(container, items, viewMode) {
    const grid = document.createElement("div");
    grid.className = `results-grid view-${viewMode}`;

    for (const item of items) {
        const card = document.createElement("a");
        card.className = `card ${engagementTier(item)}`;
        card.href = detailPath(item);
        card.title = cardTitle(item);

        const thumb = document.createElement("img");
        thumb.className = "card-thumb";
        thumb.loading = "lazy";
        thumb.src = item.thumbnail || fallbackFor(item.media_type);
        thumb.alt = "";
        thumb.addEventListener("error", () => {
            thumb.src = fallbackFor(item.media_type);
        });
        card.appendChild(thumb);

        const title = document.createElement("div");
        title.className = "card-title";
        title.textContent = cardTitle(item);
        card.appendChild(title);

        const meta = document.createElement("div");
        meta.className = "card-meta";
        meta.textContent = metaText(item);
        card.appendChild(meta);

        grid.appendChild(card);
    }
    container.appendChild(grid);
}

function renderDetails(container, items, mediaType) {
    const columns = DETAIL_COLUMNS[mediaType] || [];
    const state = getState();
    const table = document.createElement("table");
    table.className = "results-table";

    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const col of columns) {
        const th = document.createElement("th");
        th.textContent = col.label;
        if (col.sort) {
            th.addEventListener("click", () => {
                const cur = getState();
                if (cur.sort === col.sort) {
                    setState({ direction: cur.direction === "desc" ? "asc" : "desc", page: 1 });
                } else {
                    setState({ sort: col.sort, direction: "desc", page: 1 });
                }
            });
            if (state.sort === col.sort) {
                const arrow = document.createElement("span");
                arrow.className = "sort-arrow";
                arrow.textContent = state.direction === "desc" ? "▼" : "▲";
                th.appendChild(arrow);
            }
        }
        headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (const item of items) {
        const tr = document.createElement("tr");
        tr.className = engagementTier(item);
        tr.addEventListener("click", () => { window.location.href = detailPath(item); });
        for (const col of columns) {
            const td = document.createElement("td");
            const val = item[col.key];
            if (col.type === "title") {
                td.className = "col-title";
                const img = document.createElement("img");
                img.loading = "lazy";
                img.src = item.thumbnail || fallbackFor(item.media_type);
                img.alt = "";
                img.addEventListener("error", () => { img.src = fallbackFor(item.media_type); });
                td.appendChild(img);
                td.appendChild(document.createTextNode(cardTitle(item)));
            } else if (col.key === "rating") {
                // Prefer user rating; show source glyph + optional count.
                const eff = effectiveRating(item);
                if (eff == null) {
                    td.textContent = "";
                } else {
                    const src = ratingSource(item);
                    const count = item.user_rating_count
                        ? ` (${item.user_rating_count})`
                        : "";
                    td.textContent = `${src} ${eff.toFixed(1)}${count}`;
                }
            } else if (Array.isArray(val)) {
                td.textContent = val.join(", ");
            } else if (val === null || val === undefined) {
                td.textContent = "";
            } else if (col.key === "date_added" && typeof val === "string") {
                td.textContent = val.slice(0, 10);
            } else {
                td.textContent = String(val);
            }
            tr.appendChild(td);
        }
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    container.appendChild(table);
}

function renderPagination(container, state, response) {
    const totalPages = response.total_pages
        || response.num_pages
        || Math.max(1, Math.ceil((response.count || 0) / (response.page_size || 48)));

    if (totalPages <= 1) return;

    const wrap = document.createElement("div");
    wrap.className = "explorer-pagination";

    const prev = document.createElement("button");
    prev.textContent = "‹ Prev";
    prev.disabled = state.page <= 1;
    prev.addEventListener("click", () => setState({ page: state.page - 1 }));

    const info = document.createElement("span");
    info.textContent = `Page ${state.page} of ${totalPages}`;

    const next = document.createElement("button");
    next.textContent = "Next ›";
    next.disabled = state.page >= totalPages;
    next.addEventListener("click", () => setState({ page: state.page + 1 }));

    wrap.appendChild(prev);
    wrap.appendChild(info);
    wrap.appendChild(next);
    container.appendChild(wrap);
}

export function renderResults(container, response, mediaType) {
    container.innerHTML = "";
    const state = getState();
    const items = response.results || [];

    if (items.length === 0) {
        const empty = document.createElement("div");
        empty.className = "pane-loading";
        empty.textContent = "No results.";
        container.appendChild(empty);
        return;
    }

    if (state.view === "details") {
        renderDetails(container, items, mediaType);
    } else {
        renderCards(container, items, state.view);
    }

    renderPagination(container, state, response);
}
