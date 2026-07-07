// Thin fetch wrapper with CSRF + JSON handling for explorer endpoints.

function getCookie(name) {
    const parts = document.cookie.split(";");
    for (const part of parts) {
        const [k, v] = part.trim().split("=");
        if (k === name) return decodeURIComponent(v);
    }
    return null;
}

async function jsonGet(url) {
    const res = await fetch(url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
    });
    if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`GET ${url} failed: ${res.status} ${text.slice(0, 200)}`);
    }
    return res.json();
}

function qs(params) {
    const sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) {
        if (v === undefined || v === null || v === "") continue;
        if (Array.isArray(v)) {
            if (v.length === 0) continue;
            sp.set(k, v.join(","));
        } else if (typeof v === "boolean") {
            sp.set(k, v ? "true" : "false");
        } else {
            sp.set(k, String(v));
        }
    }
    const s = sp.toString();
    return s ? `?${s}` : "";
}

export const api = {
    tree: () => jsonGet("/explorer/api/tree/"),

    list: (mediaType, params) =>
        jsonGet(`/explorer/api/${mediaType}/${qs(params)}`),

    all: (params) => jsonGet(`/explorer/api/all/${qs(params)}`),
};

export { getCookie };
