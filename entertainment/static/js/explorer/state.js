// URL-hash-backed explorer state.
//
// State shape:
//   { mediaType, folderKey, folderValue, filters, sort, direction,
//     page, view, q }
//
// Serialized as `#/mediaType/folderKey/folderValue?filters&sort=...&view=...`
// so links are shareable and back/forward navigation works.

const LISTENERS = new Set();

const DEFAULT_STATE = {
    mediaType: "movies",
    folderKey: "all",
    folderValue: "",
    filters: {},
    sort: "date_added",
    direction: "desc",
    page: 1,
    view: "large",
    q: "",
};

let current = { ...DEFAULT_STATE };
let ignoreNextHashChange = false;

function encode(state) {
    const parts = [state.mediaType, state.folderKey];
    if (state.folderValue !== "" && state.folderValue !== undefined && state.folderValue !== null) {
        parts.push(encodeURIComponent(state.folderValue));
    }
    const path = parts.join("/");

    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(state.filters || {})) {
        if (v === undefined || v === null || v === "") continue;
        if (Array.isArray(v)) {
            if (v.length === 0) continue;
            sp.set(`f.${k}`, v.join(","));
        } else if (typeof v === "boolean") {
            sp.set(`f.${k}`, v ? "1" : "0");
        } else {
            sp.set(`f.${k}`, String(v));
        }
    }
    if (state.sort && state.sort !== DEFAULT_STATE.sort) sp.set("sort", state.sort);
    if (state.direction && state.direction !== DEFAULT_STATE.direction) sp.set("dir", state.direction);
    if (state.view && state.view !== DEFAULT_STATE.view) sp.set("view", state.view);
    if (state.page && state.page > 1) sp.set("page", String(state.page));
    if (state.q) sp.set("q", state.q);

    const query = sp.toString();
    return `#/${path}${query ? "?" + query : ""}`;
}

function decode(hash) {
    if (!hash || !hash.startsWith("#/")) return { ...DEFAULT_STATE };
    const raw = hash.slice(2);
    const [path, queryStr] = raw.split("?");
    const parts = path.split("/").filter(Boolean);

    const state = { ...DEFAULT_STATE, filters: {} };
    if (parts[0]) state.mediaType = parts[0];
    if (parts[1]) state.folderKey = parts[1];
    if (parts[2]) state.folderValue = decodeURIComponent(parts[2]);

    if (queryStr) {
        const sp = new URLSearchParams(queryStr);
        for (const [k, v] of sp.entries()) {
            if (k.startsWith("f.")) {
                const filterKey = k.slice(2);
                if (v === "1") state.filters[filterKey] = true;
                else if (v === "0") state.filters[filterKey] = false;
                else if (v.includes(",")) state.filters[filterKey] = v.split(",");
                else state.filters[filterKey] = v;
            } else if (k === "sort") state.sort = v;
            else if (k === "dir") state.direction = v;
            else if (k === "view") state.view = v;
            else if (k === "page") state.page = parseInt(v, 10) || 1;
            else if (k === "q") state.q = v;
        }
    }
    return state;
}

export function getState() {
    return { ...current, filters: { ...current.filters } };
}

export function setState(patch, { push = true, notify = true } = {}) {
    // NB: if the caller passes `filters` explicitly, REPLACE (don't merge)
    // so `setState({ filters: {} })` actually clears them. Callers that want
    // to patch a single filter should compose the merged object themselves
    // (see `updateFilter` in filters.js).
    const nextFilters = Object.prototype.hasOwnProperty.call(patch, "filters")
        ? { ...(patch.filters || {}) }
        : { ...current.filters };
    const next = { ...current, ...patch, filters: nextFilters };
    // If media type or folder changes, reset page to 1 unless caller specified
    if ((patch.mediaType && patch.mediaType !== current.mediaType) ||
        (patch.folderKey && patch.folderKey !== current.folderKey) ||
        (patch.folderValue !== undefined && patch.folderValue !== current.folderValue)) {
        if (patch.page === undefined) next.page = 1;
    }
    current = next;
    if (push) {
        ignoreNextHashChange = true;
        const encoded = encode(current);
        if (encoded !== window.location.hash) {
            window.location.hash = encoded;
        } else {
            ignoreNextHashChange = false;
        }
    }
    if (notify) emit();
}

export function subscribe(fn) {
    LISTENERS.add(fn);
    return () => LISTENERS.delete(fn);
}

function emit() {
    for (const fn of LISTENERS) {
        try { fn(getState()); } catch (err) { console.error(err); }
    }
}

export function initState() {
    current = decode(window.location.hash);
    window.addEventListener("hashchange", () => {
        if (ignoreNextHashChange) {
            ignoreNextHashChange = false;
            return;
        }
        current = decode(window.location.hash);
        emit();
    });
    return getState();
}

// Convert state to server query params for the list endpoint.
export function toApiParams(state) {
    const params = {
        page: state.page,
        page_size: 48,
        ordering: (state.direction === "desc" ? "-" : "") + state.sort,
    };
    for (const [k, v] of Object.entries(state.filters || {})) {
        params[k] = v;
    }
    if (state.q) params.q = state.q;

    // Fold folder into filters
    if (state.folderKey === "watchlist") params.on_watchlist = true;
    else if (state.folderKey === "reviewed") params.reviewed_by_me = true;
    else if (state.folderKey === "genres" && state.folderValue) params.genres = [state.folderValue];
    else if (state.folderKey === "countries" && state.folderValue) params.countries = [state.folderValue];
    else if (state.folderKey === "platforms" && state.folderValue) params.platforms = [state.folderValue];
    else if (state.folderKey === "decades" && state.folderValue) {
        const decade = parseInt(state.folderValue, 10);
        params.year_min = decade;
        params.year_max = decade + 9;
    } else if (state.folderKey === "roles" && state.folderValue) {
        params[state.folderValue] = true;
    }
    return params;
}
