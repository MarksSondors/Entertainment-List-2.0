// Tree pane renderer. Expects the payload from /explorer/api/tree/.

import { getState, setState } from "./state.js";

const ICONS_BASE = document.getElementById("explorer-app").dataset.iconsBase;

// Fallback ordered mapping when a folder doesn't specify its own icon.
const DEFAULT_ICON = "directory_closed";

function iconUrl(name) {
    return `${ICONS_BASE}${name || DEFAULT_ICON}.png`;
}

function makeNode({ label, icon, count, active, hasChildren, onClick, onToggle, expanded }) {
    const node = document.createElement("div");
    node.className = "tree-node" + (active ? " active" : "") + (expanded ? " expanded" : "");

    if (hasChildren) {
        const toggle = document.createElement("span");
        toggle.className = "tree-toggle";
        toggle.textContent = expanded ? "▼" : "▶";
        toggle.addEventListener("click", (e) => {
            e.stopPropagation();
            onToggle?.();
        });
        node.appendChild(toggle);
    } else {
        const spacer = document.createElement("span");
        spacer.className = "tree-toggle";
        spacer.textContent = " ";
        node.appendChild(spacer);
    }

    const img = document.createElement("img");
    img.src = iconUrl(icon);
    img.alt = "";
    node.appendChild(img);

    const labelEl = document.createElement("span");
    labelEl.textContent = label;
    node.appendChild(labelEl);

    if (count !== undefined && count !== null) {
        const countEl = document.createElement("span");
        countEl.className = "tree-count";
        countEl.textContent = `(${count})`;
        node.appendChild(countEl);
    }

    if (onClick) {
        node.addEventListener("click", onClick);
        node.style.cursor = "pointer";
    }
    return node;
}

function renderChildrenList(children, buildRow) {
    const container = document.createElement("div");
    container.className = "tree-children";
    container.style.display = "block";
    for (const child of children) {
        container.appendChild(buildRow(child));
    }
    return container;
}

function renderMediaTypeBranch(root, container, state, expandedSet) {
    const mediaExpanded = expandedSet.has(root.media_type);
    const isActiveType = state.mediaType === root.media_type;

    const node = makeNode({
        label: `${root.label} (${root.total})`,
        icon: root.icon,
        active: isActiveType && state.folderKey === "all",
        hasChildren: true,
        expanded: mediaExpanded,
        onToggle: () => {
            if (mediaExpanded) expandedSet.delete(root.media_type);
            else expandedSet.add(root.media_type);
            rerender();
        },
        onClick: () => {
            setState({ mediaType: root.media_type, folderKey: "all", folderValue: "" });
        },
    });
    container.appendChild(node);

    if (!mediaExpanded) return;

    const childrenEl = document.createElement("div");
    childrenEl.className = "tree-children";
    childrenEl.style.display = "block";

    for (const child of root.children || []) {
        if (Array.isArray(child.children) && child.children.length > 0) {
            const groupKey = `${root.media_type}:${child.key}`;
            const groupExpanded = expandedSet.has(groupKey);
            const groupActive = isActiveType && state.folderKey === child.key && !state.folderValue;
            const groupNode = makeNode({
                label: child.label,
                icon: child.icon,
                active: groupActive,
                hasChildren: true,
                expanded: groupExpanded,
                onToggle: () => {
                    if (groupExpanded) expandedSet.delete(groupKey);
                    else expandedSet.add(groupKey);
                    rerender();
                },
                onClick: () => {
                    if (groupExpanded) expandedSet.delete(groupKey);
                    else expandedSet.add(groupKey);
                    rerender();
                },
            });
            childrenEl.appendChild(groupNode);
            if (groupExpanded) {
                const subEl = document.createElement("div");
                subEl.className = "tree-children";
                subEl.style.display = "block";
                for (const leaf of child.children) {
                    const isLeafActive =
                        isActiveType &&
                        state.folderKey === child.key &&
                        String(state.folderValue) === String(leaf.id ?? leaf.value ?? leaf.key);
                    const leafValue = leaf.id ?? leaf.value ?? leaf.key;
                    const leafNode = makeNode({
                        label: leaf.name ?? leaf.label,
                        icon: DEFAULT_ICON,
                        count: leaf.count,
                        active: isLeafActive,
                        hasChildren: false,
                        onClick: () => {
                            setState({
                                mediaType: root.media_type,
                                folderKey: child.key,
                                folderValue: String(leafValue),
                            });
                        },
                    });
                    subEl.appendChild(leafNode);
                }
                childrenEl.appendChild(subEl);
            }
        } else {
            const leafActive = isActiveType && state.folderKey === child.key;
            const leafNode = makeNode({
                label: child.label,
                icon: child.icon,
                count: child.count,
                active: leafActive,
                hasChildren: false,
                onClick: () => {
                    setState({ mediaType: root.media_type, folderKey: child.key, folderValue: "" });
                },
            });
            childrenEl.appendChild(leafNode);
        }
    }

    container.appendChild(childrenEl);
}

let cachedPayload = null;
let expandedSet = new Set();
let containerEl = null;

function rerender() {
    if (!containerEl || !cachedPayload) return;
    const state = getState();
    containerEl.innerHTML = "";

    // Root "My Library" node — always expanded.
    const rootNode = makeNode({
        label: "My Library",
        icon: "computer",
        hasChildren: false,
    });
    containerEl.appendChild(rootNode);

    for (const branch of cachedPayload.media_types) {
        renderMediaTypeBranch(branch, containerEl, state, expandedSet);
    }
}

export function renderTree(payload, container) {
    cachedPayload = payload;
    containerEl = container;
    // Auto-expand the media type of current state
    const state = getState();
    if (state.mediaType) expandedSet.add(state.mediaType);
    rerender();
}

export function refreshTree() {
    rerender();
}
