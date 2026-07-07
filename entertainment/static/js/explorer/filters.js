// Filter panel — dynamic per media type.
//
// Filter definitions are declarative: a list of controls the media type
// exposes. Each control edits `state.filters[<name>]`. The tree payload
// supplies option lists for genre/country/etc so we don't hit the DB twice.

import { getState, setState } from "./state.js";

const USER_ID = document.getElementById("explorer-app").dataset.userId || "";
const IS_LOGGED_IN = USER_ID !== "";

// Sort field lists per media type (label, value). Rating always refers to
// the aggregated *user* rating, not the external (TMDB / Hardcover / RAWG)
// score, so it matches what the card displays and what the border color
// signals.
export const SORT_FIELDS = {
    movies: [
        { value: "date_added", label: "Date added" },
        { value: "title", label: "Title" },
        { value: "release_date", label: "Release date" },
        { value: "user_rating", label: "Rating" },
        { value: "runtime", label: "Runtime" },
    ],
    tvshows: [
        { value: "date_added", label: "Date added" },
        { value: "title", label: "Title" },
        { value: "first_air_date", label: "First air date" },
        { value: "last_air_date", label: "Last air date" },
        { value: "user_rating", label: "Rating" },
    ],
    books: [
        { value: "date_added", label: "Date added" },
        { value: "title", label: "Title" },
        { value: "published_date", label: "Published" },
        { value: "user_rating", label: "Rating" },
        { value: "pages", label: "Pages" },
    ],
    games: [
        { value: "date_added", label: "Date added" },
        { value: "title", label: "Title" },
        { value: "release_date", label: "Release date" },
        { value: "user_rating", label: "Rating" },
        { value: "metacritic", label: "Metacritic" },
    ],
    people: [
        { value: "name", label: "Name" },
        { value: "media_count", label: "Appearances" },
        { value: "user_rating", label: "Rating" },
        { value: "date_of_birth", label: "Born" },
        { value: "date_of_death", label: "Died" },
    ],
};

// Which detail-view columns to show per media type.
export const DETAIL_COLUMNS = {
    movies: [
        { key: "title", label: "Title", type: "title" },
        { key: "year", label: "Year", sort: "release_date" },
        { key: "rating", label: "Rating", sort: "user_rating" },
        { key: "runtime", label: "Runtime", sort: "runtime" },
        { key: "genre_names", label: "Genres" },
        { key: "status", label: "Status" },
        { key: "date_added", label: "Added", sort: "date_added" },
    ],
    tvshows: [
        { key: "title", label: "Title", type: "title" },
        { key: "year", label: "Year", sort: "first_air_date" },
        { key: "rating", label: "Rating", sort: "user_rating" },
        { key: "status", label: "Status" },
        { key: "genre_names", label: "Genres" },
        { key: "date_added", label: "Added", sort: "date_added" },
    ],
    books: [
        { key: "title", label: "Title", type: "title" },
        { key: "author_names", label: "Author(s)" },
        { key: "year", label: "Year", sort: "published_date" },
        { key: "rating", label: "Rating", sort: "user_rating" },
        { key: "pages", label: "Pages", sort: "pages" },
        { key: "genre_names", label: "Genres" },
    ],
    games: [
        { key: "title", label: "Title", type: "title" },
        { key: "year", label: "Year", sort: "release_date" },
        { key: "rating", label: "Rating", sort: "user_rating" },
        { key: "metacritic", label: "Meta", sort: "metacritic" },
        { key: "genre_names", label: "Genres" },
        { key: "platform_names", label: "Platforms" },
    ],
    people: [
        { key: "name", label: "Name", type: "title", sort: "name" },
        { key: "roles", label: "Roles" },
        { key: "rating", label: "Rating", sort: "user_rating" },
        { key: "media_count", label: "Appearances", sort: "media_count" },
        { key: "year", label: "Born", sort: "date_of_birth" },
        { key: "date_of_death", label: "Died", sort: "date_of_death" },
    ],
};

const YEAR_RANGE_FILTER = { type: "range", min: "year_min", max: "year_max", label: "Year" };
// Rating range applies to the aggregated user rating (matches sort + display).
const RATING_RANGE_FILTER = { type: "range", min: "user_rating_min", max: "user_rating_max", label: "Rating", step: 0.1 };
// Total number of user reviews (any user) on the item.
const REVIEWS_COUNT_FILTER = { type: "range", min: "user_rating_count_min", max: "user_rating_count_max", label: "# of reviews" };

// Filter schema per media type. Options for multi-select come from tree payload.
// NB: `q` (search) is intentionally NOT here — the address-bar search box in
// the toolbar owns it.
const FILTER_SCHEMAS = {
    movies: [
        { type: "multi", name: "genres", label: "Genres", optionsFrom: "movies.genres" },
        { type: "multi", name: "countries", label: "Countries", optionsFrom: "movies.countries" },
        YEAR_RANGE_FILTER,
        RATING_RANGE_FILTER,
        REVIEWS_COUNT_FILTER,
        { type: "range", min: "runtime_min", max: "runtime_max", label: "Runtime (min)" },
        { type: "checkbox", name: "is_anime", label: "Anime only" },
        { type: "checkbox", name: "has_poster", label: "Has poster" },
    ],
    tvshows: [
        { type: "multi", name: "genres", label: "Genres", optionsFrom: "tvshows.genres" },
        { type: "multi", name: "countries", label: "Countries", optionsFrom: "tvshows.countries" },
        YEAR_RANGE_FILTER,
        RATING_RANGE_FILTER,
        REVIEWS_COUNT_FILTER,
        { type: "checkbox", name: "is_anime", label: "Anime only" },
    ],
    books: [
        { type: "multi", name: "genres", label: "Genres", optionsFrom: "books.genres" },
        YEAR_RANGE_FILTER,
        RATING_RANGE_FILTER,
        REVIEWS_COUNT_FILTER,
        { type: "range", min: "pages_min", max: "pages_max", label: "Pages" },
    ],
    games: [
        { type: "multi", name: "genres", label: "Genres", optionsFrom: "games.genres" },
        { type: "multi", name: "platforms", label: "Platforms", optionsFrom: "games.platforms" },
        YEAR_RANGE_FILTER,
        RATING_RANGE_FILTER,
        REVIEWS_COUNT_FILTER,
        { type: "range", min: "metacritic_min", max: "metacritic_max", label: "Metacritic" },
    ],
    people: [
        { type: "range", min: "born_year_min", max: "born_year_max", label: "Born year" },
        RATING_RANGE_FILTER,
        REVIEWS_COUNT_FILTER,
        { type: "checkbox", name: "alive", label: "Living only" },
        { type: "checkbox", name: "has_profile_picture", label: "Has photo" },
    ],
};

const USER_CONTEXT_FILTERS = {
    movies: [
        { type: "checkbox", name: "on_watchlist", label: "On my watchlist" },
        { type: "checkbox", name: "reviewed_by_me", label: "I've reviewed" },
        { type: "range", min: "my_rating_min", max: "my_rating_max", label: "My rating", step: 0.5 },
    ],
    tvshows: [
        { type: "checkbox", name: "on_watchlist", label: "On my watchlist" },
        { type: "checkbox", name: "reviewed_by_me", label: "I've reviewed" },
        { type: "range", min: "my_rating_min", max: "my_rating_max", label: "My rating", step: 0.5 },
    ],
    books: [
        { type: "checkbox", name: "on_watchlist", label: "On my watchlist" },
        { type: "checkbox", name: "reviewed_by_me", label: "I've reviewed" },
    ],
    games: [
        { type: "checkbox", name: "on_watchlist", label: "On my watchlist" },
        { type: "checkbox", name: "reviewed_by_me", label: "I've reviewed" },
    ],
    people: [],
};

function optionsFromTree(payload, dotted) {
    if (!payload) return [];
    const [mediaType, kind] = dotted.split(".");
    const branch = payload.media_types.find((b) => b.media_type === mediaType);
    if (!branch) return [];
    const group = (branch.children || []).find((c) => c.key === kind);
    if (!group || !Array.isArray(group.children)) return [];
    return group.children.map((c) => ({ value: c.id ?? c.value ?? c.key, label: c.name ?? c.label, count: c.count }));
}

// Returns whether a control has a non-empty value in the current filter state.
function isControlActive(control, filters) {
    switch (control.type) {
        case "range":
            return filters[control.min] != null || filters[control.max] != null;
        case "checkbox":
            return !!filters[control.name];
        case "multi": {
            const v = filters[control.name];
            return Array.isArray(v) && v.length > 0;
        }
        case "text":
            return !!(filters[control.name] && String(filters[control.name]).trim());
        default:
            return false;
    }
}

// Field names the control writes into `state.filters`. Used to clear a
// filter and to count how many controls are active.
function controlFields(control) {
    switch (control.type) {
        case "range":   return [control.min, control.max];
        case "checkbox":
        case "multi":
        case "text":    return [control.name];
        default:        return [];
    }
}

function countActive(schema, filters) {
    let n = 0;
    for (const c of schema) if (isControlActive(c, filters)) n++;
    return n;
}

function makeGroup(labelText, { active = false, onClear = null, badge = null } = {}) {
    const wrap = document.createElement("div");
    wrap.className = "filter-group" + (active ? " filter-group-active" : "");
    if (labelText) {
        const title = document.createElement("div");
        title.className = "filter-group-title";

        const label = document.createElement("span");
        label.textContent = labelText;
        title.appendChild(label);

        if (badge) {
            const b = document.createElement("span");
            b.className = "filter-badge";
            b.textContent = badge;
            title.appendChild(b);
        }

        if (active && onClear) {
            const x = document.createElement("button");
            x.type = "button";
            x.className = "filter-clear-x";
            x.textContent = "×";
            x.title = "Clear this filter";
            x.setAttribute("aria-label", "Clear this filter");
            x.addEventListener("click", (e) => { e.preventDefault(); onClear(); });
            title.appendChild(x);
        }

        wrap.appendChild(title);
    }
    return wrap;
}

function currentFilter(name) {
    return getState().filters[name];
}

function updateFilter(patch) {
    const filters = { ...getState().filters, ...patch };
    for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) {
            delete filters[k];
        }
    }
    setState({ filters, page: 1 });
}

// Convenience: drop a set of field names from filter state in one shot.
function clearFields(fields) {
    const filters = { ...getState().filters };
    for (const f of fields) delete filters[f];
    setState({ filters, page: 1 });
}

function buildTextControl(control) {
    const filters = getState().filters;
    const active = isControlActive(control, filters);
    const group = makeGroup(control.label, {
        active,
        onClear: () => clearFields(controlFields(control)),
    });
    const input = document.createElement("input");
    input.type = "text";
    input.value = filters[control.name] || "";
    input.style.width = "100%";
    let handle;
    input.addEventListener("input", () => {
        clearTimeout(handle);
        handle = setTimeout(() => updateFilter({ [control.name]: input.value.trim() }), 250);
    });
    group.appendChild(input);
    return group;
}

function buildRangeControl(control) {
    const filters = getState().filters;
    const active = isControlActive(control, filters);
    const group = makeGroup(control.label, {
        active,
        onClear: () => clearFields(controlFields(control)),
    });

    const row = document.createElement("div");
    row.className = "filter-range";

    const minInput = document.createElement("input");
    minInput.type = "number";
    if (control.step) minInput.step = String(control.step);
    minInput.placeholder = "min";
    minInput.value = filters[control.min] ?? "";

    const maxInput = document.createElement("input");
    maxInput.type = "number";
    if (control.step) maxInput.step = String(control.step);
    maxInput.placeholder = "max";
    maxInput.value = filters[control.max] ?? "";

    let handle;
    const commit = () => {
        clearTimeout(handle);
        handle = setTimeout(() => {
            updateFilter({
                [control.min]: minInput.value === "" ? "" : Number(minInput.value),
                [control.max]: maxInput.value === "" ? "" : Number(maxInput.value),
            });
        }, 150);
    };
    minInput.addEventListener("input", commit);
    maxInput.addEventListener("input", commit);

    row.appendChild(minInput);
    row.appendChild(document.createTextNode("–"));
    row.appendChild(maxInput);
    group.appendChild(row);
    return group;
}

function buildCheckboxControl(control) {
    const active = !!currentFilter(control.name);
    // Checkbox controls don't render their own title row — the checkbox
    // itself carries the label. But we still colour the group when active.
    const group = makeGroup("");
    group.className = "filter-group" + (active ? " filter-group-active" : "");

    const label = document.createElement("label");
    label.className = "filter-checkbox-inline";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = active;
    cb.addEventListener("change", () => {
        updateFilter({ [control.name]: cb.checked ? true : "" });
    });
    label.appendChild(cb);
    label.appendChild(document.createTextNode(" " + control.label));
    group.appendChild(label);
    return group;
}

function buildMultiControl(control, treePayload) {
    const filters = getState().filters;
    const selectedIds = new Set(((filters[control.name] || [])).map((x) => String(x)));
    const active = selectedIds.size > 0;
    const group = makeGroup(control.label, {
        active,
        badge: active ? String(selectedIds.size) : null,
        onClear: () => clearFields(controlFields(control)),
    });
    const options = optionsFromTree(treePayload, control.optionsFrom);
    const listBox = document.createElement("div");
    listBox.className = "filter-checkbox-list";

    for (const opt of options) {
        const label = document.createElement("label");
        const isSelected = selectedIds.has(String(opt.value));
        if (isSelected) label.classList.add("filter-option-selected");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = String(opt.value);
        cb.checked = isSelected;
        cb.addEventListener("change", () => {
            const now = new Set(((getState().filters[control.name] || [])).map((x) => String(x)));
            if (cb.checked) now.add(String(opt.value));
            else now.delete(String(opt.value));
            updateFilter({ [control.name]: Array.from(now) });
        });
        label.appendChild(cb);
        label.appendChild(document.createTextNode(` ${opt.label}${opt.count !== undefined ? " (" + opt.count + ")" : ""}`));
        listBox.appendChild(label);
    }
    group.appendChild(listBox);
    return group;
}

function buildControl(control, treePayload) {
    switch (control.type) {
        case "text": return buildTextControl(control);
        case "range": return buildRangeControl(control);
        case "checkbox": return buildCheckboxControl(control);
        case "multi": return buildMultiControl(control, treePayload);
        default: return document.createElement("div");
    }
}

export function renderFilters(container, treePayload) {
    const state = getState();
    const mediaType = state.mediaType;
    const filters = state.filters;
    container.innerHTML = "";

    const schema = FILTER_SCHEMAS[mediaType] || [];
    const userSchema = IS_LOGGED_IN ? (USER_CONTEXT_FILTERS[mediaType] || []) : [];
    const activeCount = countActive(schema, filters) + countActive(userSchema, filters);
    const hasSearch = !!(state.q && state.q.trim());
    const totalActive = activeCount + (hasSearch ? 1 : 0);

    const heading = document.createElement("div");
    heading.className = "filter-heading";
    const hLabel = document.createElement("span");
    hLabel.textContent = `Filters — ${mediaType}`;
    heading.appendChild(hLabel);
    if (totalActive > 0) {
        const badge = document.createElement("span");
        badge.className = "filter-heading-badge";
        badge.textContent = `${totalActive} active`;
        heading.appendChild(badge);
    }
    container.appendChild(heading);

    for (const control of schema) {
        container.appendChild(buildControl(control, treePayload));
    }

    if (userSchema.length > 0) {
        const divider = document.createElement("div");
        divider.className = "filter-heading filter-heading-secondary";
        divider.textContent = "My data";
        container.appendChild(divider);
        for (const control of userSchema) {
            container.appendChild(buildControl(control, treePayload));
        }
    }

    const actions = document.createElement("div");
    actions.className = "filter-actions";
    const clearBtn = document.createElement("button");
    clearBtn.textContent = totalActive > 0 ? `Clear filters (${totalActive})` : "Clear filters";
    clearBtn.disabled = totalActive === 0;
    clearBtn.addEventListener("click", () => {
        // Also clear the toolbar search box + its q state so the button
        // matches user expectation of "reset this pane".
        setState({ filters: {}, q: "", page: 1 });
    });
    actions.appendChild(clearBtn);
    container.appendChild(actions);
}

