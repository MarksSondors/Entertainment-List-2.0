/* Movie network graph — Cytoscape.js interaction layer.
   Depends on cytoscape-graph-config.js (style/layout) and network-graph-insights.js
   (colour scales, cluster profiles, placement maths) being loaded first. */

(function () {
    const DATA_URL = '/movies/network-graph/data/';
    const EXPAND_URL = '/movies/network-graph/expand/';
    const TASTE_URL = '/movies/network-graph/taste/';
    const SEARCH_URL = '/movies/search-local/';
    const TASTE_POLL_MS = 4000;
    const TASTE_POLL_MAX = 60; // ~4 minutes of "building" before giving up
    const SETTINGS_KEY = 'ng-settings-v1';
    const I = window.NGInsights;

    const COLOR_BY_LABELS = {
        region: 'Region',
        genre: 'Genre',
        decade: 'Decade',
        tmdb: 'TMDB rating',
        mine: 'My rating',
        predicted: 'Predicted for me',
    };

    let cy = null;
    let latestAnalytics = {};
    let commMap = new Map();
    // the map's regions (Louvain communities of the active layout): node id -> region key,
    // the listed regions [{ id, name, size, nodes }] and their map colours
    let regionOf = new Map();
    let regionList = [];
    let regionColors = new Map();
    // movie node id -> { id, label } of the collection it belongs to (e.g. a trilogy)
    let collectionMap = new Map();
    // outline boxes currently drawn: [{ hull, members }] - kept so they can follow drags/layouts
    let hulls = [];
    let statusMessage = 'Ready';
    let loadingCount = 0;

    // taste map state: `me` is always the latest payload's user part (ratings/watchlist are
    // live even while the model data is still building); `taste` is set once it's ready
    let me = null;
    let taste = null;
    let tastePolls = 0;
    let appliedLayout = 'relations';
    // "you" / other-user markers: [{ node, kind, anchors }]
    let markers = [];

    const settings = loadSettings();

    function loadSettings() {
        const defaults = { layout: 'relations', view: 'dots', colorBy: 'region', highlight: false, users: false };
        let saved = {};
        try {
            saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
        } catch (err) {
            // blocked storage - defaults it is
        }
        const merged = Object.assign(defaults, saved);
        if (!COLOR_BY_LABELS[merged.colorBy]) merged.colorBy = 'region'; // e.g. the retired 'community'
        return merged;
    }

    function saveSettings() {
        try {
            localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
        } catch (err) {
            // private mode / blocked storage - settings just won't persist
        }
    }

    function el(id) {
        return document.getElementById(id);
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str === null || str === undefined ? '' : String(str);
        return div.innerHTML;
    }

    function fmt(value, digits = 1) {
        return typeof value === 'number' ? value.toFixed(digits) : '—';
    }

    function showLoading(show, message = 'Building graph…') {
        loadingCount = Math.max(0, loadingCount + (show ? 1 : -1));
        el('ng-loading').classList.toggle('hidden', loadingCount === 0);
        setStatus(show ? message : statusMessage);
    }

    function setStatus(text, persist = false) {
        if (persist) statusMessage = text;
        el('ng-status-text').textContent = text;
    }

    function plural(n, word, pluralWord = `${word}s`) {
        return `${n.toLocaleString()} ${n === 1 ? word : pluralWord}`;
    }

    function relationshipEdges() {
        return cy.edges().not('.taste, .ng-you-link');
    }

    function graphNodes() {
        return cy.nodes().not('.ng-hull, .ng-marker, .ng-group-label, .ng-collection-label');
    }

    function updateStatusCounts() {
        if (!cy) return;
        el('ng-status-movies').textContent = plural(cy.nodes('.movie').length, 'movie');
        el('ng-status-edges').textContent = plural(relationshipEdges().length, 'connection');
        el('ng-status-communities').textContent = plural(regionList.length, 'region');
    }

    function buildParams() {
        const params = new URLSearchParams();
        // no rating/count filters by design - every movie is loaded, always
        params.set('rating_threshold', '0');
        params.set('movie_limit', '0');
        // users + predictions now come from the recommender via the taste endpoint; the
        // builder's old collaborative-filter social layer stays off
        params.set('social', '0');
        params.set('predictions', '0');
        params.set('people', el('ng-show-people').checked ? '1' : '0');
        return params;
    }

    function communityLookup(analytics) {
        const map = new Map();
        const communities = analytics && analytics.communities && analytics.communities.communities;
        if (!communities) return map;
        for (const [commId, data] of Object.entries(communities)) {
            for (const nodeId of data.nodes || []) {
                map.set(nodeId, { id: commId, name: data.name, size: data.size });
            }
        }
        return map;
    }

    function edgeElementFrom(edge) {
        const source = edge.source || edge.from;
        const target = edge.target || edge.to;
        return {
            group: 'edges',
            data: {
                id: `${source}__${target}`,
                source,
                target,
                type: edge.type,
                weight: edge.weight,
                reasons: edge.reasons || [],
            },
            classes: edge.type,
        };
    }

    function movieElementFrom(node, sizeScale = 1) {
        const comm = commMap.get(node.id);
        const data = {
            id: node.id,
            label: node.label,
            type: node.type,
            rating: node.rating,
            user_rating: node.user_rating,
            year: node.year,
            tmdb_id: node.tmdb_id,
            person_id: node.person_id,
            studio: node.studio,
            genres: node.genres,
            size: (node.size || 24) * sizeScale,
            raw: node,
            community: comm ? comm.id : undefined,
            // placeholder until applyColors() runs, so the data(vizColor) mapping always resolves
            vizColor: I.OTHER,
        };
        // omit (not just blank) so the cytoscape [poster]/[profile_picture] existence
        // selectors correctly skip nodes without one instead of trying background-image: ''
        if (node.poster) data.poster = node.poster;
        if (node.profile_picture) data.profile_picture = node.profile_picture;
        return { group: 'nodes', data, classes: node.type };
    }

    /** Plain movie object for the insights helpers. */
    function movieInfo(node) {
        const raw = node.data('raw') || {};
        return {
            id: node.id(),
            label: node.data('label'),
            genres: raw.genres || [],
            keywords: raw.keywords || [],
            director_names: raw.director_names || [],
            year: raw.year,
            rating: raw.rating,
            user_rating: raw.user_rating,
            collection: I.shortCollectionName(raw.collection_name),
            studio: raw.studio,
            community: regionOf.get(node.id()),
        };
    }

    function allMovieInfos() {
        return cy.nodes('.movie').map(movieInfo);
    }

    async function loadGraph() {
        showLoading(true);
        try {
            const res = await fetch(`${DATA_URL}?${buildParams().toString()}`);
            const data = await res.json();
            renderGraph(data);
        } catch (err) {
            console.error('Failed to load network graph', err);
            statusMessage = 'Could not load the graph — try Refresh';
        } finally {
            showLoading(false);
        }
    }

    function renderGraph(data) {
        const nodes = data.nodes || [];
        const edges = data.edges || [];
        latestAnalytics = data.analytics || {};
        commMap = communityLookup(latestAnalytics);
        collectionMap = new Map();
        addCollections(data.compound_groups);
        // large graphs (e.g. 500 movies) overlap badly at full poster size - shrink them down
        const sizeScale = nodes.length > 300 ? 0.5 : nodes.length > 150 ? 0.7 : 1;

        el('ng-empty-state').classList.toggle('visible', nodes.length === 0);

        // No compound boxes on the canvas: communities/collections nested as Cytoscape
        // parents cluttered the view and squeezed everything toward the middle. Community
        // membership is kept as plain node data and only drives layout edge lengths plus the
        // on-demand outline drawn when a community is picked in the sidebar.
        const elements = [];
        for (const node of nodes) {
            // the old social layer's user nodes are superseded by the taste-map markers
            if (node.type === 'user') continue;
            elements.push(movieElementFrom(node, sizeScale));
        }
        for (const edge of edges) {
            if (edge.type === 'review' || edge.type === 'prediction') continue;
            elements.push(edgeElementFrom(edge));
        }

        if (!cy) {
            initCytoscape();
        } else {
            clearFocus();
            removeMarkers();
            cy.elements().remove();
        }
        cy.add(elements);
        addTasteEdges();
        markBackbone();
        applyDensityStyling();
        runLayout();

        refreshPersonalLayers();
        updateStatusCounts();
        setStatus('Ready', true);
    }

    function isCompound(node) {
        return node.hasClass('ng-hull');
    }

    function isMarker(node) {
        return node.hasClass('ng-marker');
    }

    function isMapLabel(node) {
        return node.hasClass('ng-group-label') || node.hasClass('ng-collection-label');
    }

    const PERSON_EDGE_TYPES = new Set(['directed_by', 'acted_in']);

    /**
     * Mark the "backbone": each movie's few strongest movie-movie links. Only these drive
     * the Relationships layout and are drawn by default - with every movie loaded and up to
     * 8 links per movie from the backend, laying out on the full edge set is what collapsed
     * everything into one unreadable ball in the centre. The remaining links stay in the
     * graph as .ng-weak (hidden) and reappear when a movie is focused.
     */
    function markBackbone() {
        const movies = cy.nodes('.movie');
        const perNode = movies.length > 300 ? 2 : 3;
        const backbone = new Set();
        movies.forEach((node) => {
            node.connectedEdges()
                .filter((e) => !PERSON_EDGE_TYPES.has(e.data('type')) && !e.hasClass('taste') && !e.hasClass('ng-you-link'))
                .sort((a, b) => (b.data('weight') || 0) - (a.data('weight') || 0))
                .slice(0, perNode)
                .forEach((e) => backbone.add(e.id()));
        });
        cy.batch(() => {
            relationshipEdges().forEach((e) => {
                // person overlay edges stay visible: they're capped server-side and are the
                // whole point of turning the overlay on
                const strong = backbone.has(e.id()) || PERSON_EDGE_TYPES.has(e.data('type'));
                e.toggleClass('ng-weak', !strong);
                e.data('sameCommunity', sameCommunity(e));
            });
        });
    }

    function sameCommunity(edge) {
        const a = edge.source().data('community');
        return a !== undefined && a === edge.target().data('community');
    }

    function addCollections(groups) {
        for (const group of groups || []) {
            for (const mid of group.movie_ids) collectionMap.set(mid, { id: group.id, label: group.label });
        }
    }

    // ---- On-demand outline boxes (community picked in sidebar / selected movie's collection) ----

    function hullGeometry(members, pad) {
        const bb = members.boundingBox({ includeLabels: false, includeOverlays: false });
        return {
            position: { x: bb.x1 + bb.w / 2, y: bb.y1 + bb.h / 2 },
            w: bb.w + pad * 2,
            h: bb.h + pad * 2,
        };
    }

    /**
     * Draw a plain (non-compound) rectangle behind `members`. Deliberately not a Cytoscape
     * compound parent: re-parenting would feed back into the layout, which is exactly what
     * used to squash the graph. The box is inert (no events) and tracks its members.
     */
    function drawHull(members, { label, color, kind }) {
        if (!members.length) return;
        const pad = kind === 'community' ? 26 : 14;
        const geo = hullGeometry(members, pad);
        const hull = cy.add({
            group: 'nodes',
            data: { id: `hull_${kind}_${hulls.length}`, label, color, w: geo.w, h: geo.h },
            position: geo.position,
            classes: `ng-hull ng-hull--${kind}`,
            selectable: false,
            grabbable: false,
        });
        hulls.push({ hull, members, pad });
    }

    function updateHulls() {
        for (const { hull, members, pad } of hulls) {
            const geo = hullGeometry(members, pad);
            hull.position(geo.position);
            hull.data({ w: geo.w, h: geo.h });
        }
    }

    function removeHulls() {
        for (const { hull } of hulls) hull.remove();
        hulls = [];
        cy.edges('.ng-you-link').remove();
    }

    function showCollectionHulls(nodes) {
        const seen = new Set();
        nodes.forEach((node) => {
            const coll = collectionMap.get(node.id());
            if (!coll || seen.has(coll.id)) return;
            seen.add(coll.id);
            const members = cy.nodes('.movie').filter((n) => {
                const c = collectionMap.get(n.id());
                return c && c.id === coll.id;
            });
            if (members.length < 2) return;
            members.removeClass('ng-faded').addClass('ng-hood');
            drawHull(members, { label: coll.label, color: '#F56565', kind: 'collection' });
        });
    }

    function initCytoscape() {
        cy = cytoscape({
            container: el('ng-cytoscape'),
            style: window.NG_CYTOSCAPE_STYLE,
            wheelSensitivity: 0.25,
            minZoom: 0.1,
            maxZoom: 4,
            // keep large graphs responsive while panning/zooming instead of redrawing every frame
            hideEdgesOnViewport: true,
            textureOnViewport: true,
            pixelRatio: 'auto',
        });

        cy.on('tap', 'node', (evt) => {
            const node = evt.target;
            if (isCompound(node)) return;
            if (node.hasClass('ng-group-label')) {
                focusRegion(node.data('region'));
                return;
            }
            if (node.hasClass('ng-collection-label')) {
                focusCollection(node.data('collection'));
                return;
            }
            if (isMarker(node)) {
                if (node.data('kind') === 'you') focusYou();
                else focusUser(node);
                return;
            }
            focusOn(node);
            showNodeDetail(node);
        });

        cy.on('dbltap', 'node', (evt) => {
            const node = evt.target;
            if (isCompound(node) || isMarker(node) || isMapLabel(node)) return;
            expandNode(node);
        });

        // tapping empty canvas leaves focus mode
        cy.on('tap', (evt) => {
            if (evt.target === cy) clearFocus();
        });

        cy.on('mouseover', 'node', (evt) => {
            const node = evt.target;
            if (isCompound(node)) return;
            if (isMapLabel(node)) {
                el('ng-cytoscape').style.cursor = 'pointer';
                return;
            }
            node.addClass('ng-hover');
            el('ng-cytoscape').style.cursor = 'pointer';
            showTooltip(node, evt.renderedPosition);
        });

        cy.on('mouseout', 'node', (evt) => {
            evt.target.removeClass('ng-hover');
            el('ng-cytoscape').style.cursor = '';
            hideTooltip();
        });

        cy.on('viewport', hideTooltip);

        // keep outline boxes and markers wrapped around / placed by their movies as nodes move
        cy.on('drag', 'node', (evt) => {
            if (isMarker(evt.target)) return;
            if (hulls.length) updateHulls();
            positionMarkers();
        });
        cy.on('layoutstop', () => {
            if (hulls.length) updateHulls();
            positionMarkers();
        });
    }

    // Only backbone edges are drawn now, but big graphs still get them faded a little, and
    // the per-node drop shadow (it doubles draw calls) goes past the point where it's no
    // longer visible anyway.
    function applyDensityStyling() {
        if (!cy) return;
        const count = graphNodes().length;
        const edgeOpacity = count > 600 ? 0.25 : count > 300 ? 0.35 : count > 150 ? 0.45 : 0.6;
        cy.style().fromJson(window.NG_BUILD_STYLE({
            edgeOpacity,
            heavy: count <= 300,
            dots: settings.view === 'dots',
        })).update();
    }

    /**
     * Focus mode: dim everything except the given nodes and their direct neighbours (hidden
     * weak links and taste links included), so one movie's relationships read clearly in a
     * dense graph. Focusing a single movie also outlines the collection it belongs to, if any.
     */
    function focusOn(nodes, { collections = nodes.length === 1, neighbors = true } = {}) {
        if (!cy) return;
        removeHulls();
        // a community focus shows just its members and the links between them
        const hood = neighbors ? nodes.closedNeighborhood() : nodes.union(nodes.edgesWith(nodes));
        cy.batch(() => {
            cy.elements().not('.ng-marker').removeClass('ng-hood').addClass('ng-faded');
            hood.removeClass('ng-faded').addClass('ng-hood');
        });
        if (collections) showCollectionHulls(nodes);
        cy.elements().unselect();
        nodes.select();
    }

    function clearFocus() {
        if (!cy) return;
        removeHulls();
        cy.elements().removeClass('ng-faded ng-hood').unselect();
        setActiveCommunity(null);
    }

    function showTooltip(node, pos) {
        const tip = el('ng-tooltip');
        let html;
        if (isMarker(node)) {
            html = node.data('kind') === 'you'
                ? 'You <span class="ng-tooltip-meta">click for your taste map</span>'
                : `${escapeHtml(node.data('label'))} <span class="ng-tooltip-meta">another user</span>`;
        } else {
            const meta = [node.data('year'), node.data('rating') ? `★ ${node.data('rating')}` : ''];
            const mine = me && me.ratings[node.id()];
            if (typeof mine === 'number') meta.push(`you: ${mine}`);
            html = `${escapeHtml(node.data('label'))} <span class="ng-tooltip-meta">${escapeHtml(meta.filter(Boolean).join(' · '))}</span>`;
        }
        tip.innerHTML = html;
        // offset down-right of the cursor like a native tooltip, flipped near the right edge
        const wrap = el('ng-cytoscape');
        const x = Math.min(pos.x + 14, wrap.clientWidth - tip.offsetWidth - 6);
        tip.style.left = `${Math.max(4, x)}px`;
        tip.style.top = `${pos.y + 20}px`;
        tip.classList.add('visible');
    }

    function hideTooltip() {
        el('ng-tooltip').classList.remove('visible');
    }

    function zoomBy(factor) {
        if (!cy) return;
        const center = { x: cy.width() / 2, y: cy.height() / 2 };
        cy.animate({
            zoom: { level: cy.zoom() * factor, renderedPosition: center },
        }, { duration: 160 });
    }

    // ---- Layouts ----

    function effectiveLayout() {
        return settings.layout === 'taste' && taste ? 'taste' : 'relations';
    }

    function graphLibs() {
        const lib = window.graphologyLibrary;
        if (!window.graphology || !lib || !lib.layoutForceAtlas2) return null;
        return {
            Graph: window.graphology,
            forceAtlas2: lib.layoutForceAtlas2,
            noverlap: lib.layoutNoverlap,
            louvain: lib.communitiesLouvain,
        };
    }

    /** On-screen diameter of a node in the current view (mirrors the size mappings in the style). */
    function nodeDiameter(node) {
        if (!node.hasClass('movie')) return 26;
        const t = Math.max(0, Math.min(1, ((node.data('size') || 24) - 9) / 51));
        return settings.view === 'dots' ? 9 + t * 21 : 27 + t * 90;
    }

    /** Full re-layout of the map in the active mode. Heavy (~1-2 s), so the overlay is shown first. */
    function runLayout() {
        if (!cy) return;
        showLoading(true, 'Arranging the map…');
        // let the overlay paint before the synchronous layout blocks the main thread
        setTimeout(() => {
            try {
                doLayout();
            } catch (err) {
                console.error('Map layout failed', err);
            } finally {
                showLoading(false);
            }
        }, 30);
    }

    /**
     * The "Wikipedia map" layout (see NGInsights.mapLayout): Louvain regions + ForceAtlas2
     * with community-weighted attraction, over the active mode's links -
     * Relationships -> all relationship edges (hidden weak ones included);
     * Taste space -> the recommender's taste-neighbour links.
     * Then regions get names, map colours, sidebar entries and floating labels.
     */
    function doLayout() {
        appliedLayout = effectiveLayout();
        removeHulls();
        removeGroupLabels();
        const nodes = graphNodes();
        if (!nodes.length) return;
        const tasteMode = appliedLayout === 'taste';
        const layoutEdges = tasteMode
            ? cy.edges().filter((e) => e.hasClass('taste') || PERSON_EDGE_TYPES.has(e.data('type')))
            : relationshipEdges();
        cy.edges('.taste').toggleClass('ng-taste-on', tasteMode);

        const libs = graphLibs();
        let positions = null;
        let regionsById = new Map();
        if (libs) {
            const result = I.mapLayout(
                nodes.map((n) => ({ id: n.id(), size: nodeDiameter(n), label: String(n.data('label') || '') })),
                layoutEdges.map((e) => [e.source().id(), e.target().id(), tasteMode ? e.data('sim') || 0.5 : e.data('weight') || 1]),
                libs,
                // franchises stay together in both layouts, taste space included
                { groups: [...collectionsOnMap().values()].map((c) => c.ids) },
            );
            positions = result.positions;
            regionsById = result.regions;
        } else {
            // CDN failed: fall back to the server's communities as regions + plain fcose
            cy.nodes('.movie').forEach((n) => {
                if (n.data('community') !== undefined) regionsById.set(n.id(), `c:${n.data('community')}`);
            });
        }
        buildRegions(regionsById, layoutEdges);
        applyColors();
        renderRegionList();
        updateStatusCounts();

        if (positions) {
            nodes.layout({
                name: 'preset',
                positions: (n) => positions.get(n.id()) || n.position(),
                fit: true,
                padding: 40,
                animate: nodes.length <= 700,
                animationDuration: 700,
                animationEasing: 'ease-out-cubic',
            }).run();
            addRegionLabels(positions);
        } else {
            nodes.union(layoutEdges.not('.ng-weak')).layout(window.NG_CYTOSCAPE_LAYOUT(nodes.length)).run();
        }
    }

    /** Regions from the layout's communities: names, sizes, map colours. Tiny ones stay unlisted. */
    function buildRegions(regionsById, layoutEdges) {
        const movieIds = cy.nodes('.movie').map((n) => n.id());
        const groups = I.buildGroups(movieIds, regionsById, { minSize: 3 });
        groups.delete('__misc');
        regionOf = new Map();
        regionList = [...groups.entries()]
            .sort((a, b) => b[1].length - a[1].length)
            .map(([key, ids]) => {
                ids.forEach((id) => regionOf.set(id, key));
                return { id: key, name: '', size: ids.length, nodes: ids };
            });
        // named from their own movies (franchise > director > distinctive theme) - the
        // server's community names are not used: they absorb unrelated leftover movies
        const names = I.nameRegions(regionList, allMovieInfos());
        regionList.forEach((r) => { r.name = names.get(r.id) || 'Assorted'; });

        // map colouring: neighbouring regions (sharing links) never share a colour
        const adjacency = new Map();
        layoutEdges.forEach((e) => {
            const a = regionOf.get(e.source().id());
            const b = regionOf.get(e.target().id());
            if (!a || !b || a === b) return;
            const k = a < b ? `${a}|${b}` : `${b}|${a}`;
            adjacency.set(k, (adjacency.get(k) || 0) + 1);
        });
        regionColors = I.mapColoring(regionList.map((r) => r.id), adjacency);
    }

    /**
     * Collections (franchises) with 2+ movies on the map, keyed by collection id:
     * { id, name (short, e.g. "Demon Slayer"), ids }. Read off every movie node's own
     * collection fields, so expanded/searched movies count too.
     */
    function collectionsOnMap() {
        const out = new Map();
        cy.nodes('.movie').forEach((n) => {
            const raw = n.data('raw') || {};
            if (raw.collection_id === null || raw.collection_id === undefined) return;
            const key = String(raw.collection_id);
            if (!out.has(key)) out.set(key, { id: key, name: I.shortCollectionName(raw.collection_name) || 'Collection', ids: [] });
            out.get(key).ids.push(n.id());
        });
        for (const [key, c] of out) if (c.ids.length < 2) out.delete(key);
        return out;
    }

    function removeGroupLabels() {
        cy.nodes('.ng-group-label, .ng-collection-label').remove();
    }

    /** Floating, clickable region names at each region's median point (robust to outliers). */
    function addRegionLabels(positions) {
        const median = (xs) => {
            const sorted = [...xs].sort((a, b) => a - b);
            return sorted[Math.floor(sorted.length / 2)];
        };
        const labels = regionList.filter((r) => r.size >= 8).map((r, i) => {
            const pts = r.nodes.map((id) => positions.get(id)).filter(Boolean);
            return {
                group: 'nodes',
                data: {
                    id: `ng-glabel-${i}`,
                    label: r.name,
                    region: r.id,
                    fontSize: Math.round(Math.min(28, 12 + Math.sqrt(r.size) * 1.2)),
                },
                position: { x: median(pts.map((p) => p.x)), y: median(pts.map((p) => p.y)) },
                classes: 'ng-group-label',
                selectable: false,
                grabbable: false,
            };
        });
        // franchise tabs inside the regions - small, so they only appear once zoomed in
        let i = 0;
        for (const c of collectionsOnMap().values()) {
            const pts = c.ids.map((id) => positions.get(id)).filter(Boolean);
            if (pts.length < 2) continue;
            const x = pts.reduce((s, p) => s + p.x, 0) / pts.length;
            const y = Math.min(...pts.map((p) => p.y)) - 14; // just above the franchise's movies
            labels.push({
                group: 'nodes',
                data: { id: `ng-clabel-${i++}`, label: c.name, collection: c.id },
                position: { x, y },
                classes: 'ng-collection-label',
                selectable: false,
                grabbable: false,
            });
        }
        if (labels.length) cy.add(labels);
    }

    /** Focus one franchise: highlight its movies, outline them and list them in Details. */
    function focusCollection(collectionId) {
        const coll = collectionsOnMap().get(String(collectionId));
        if (!coll) return;
        const ids = new Set(coll.ids);
        const members = cy.nodes('.movie').filter((n) => ids.has(n.id()));
        focusOn(members, { collections: false, neighbors: false });
        drawHull(members, { label: coll.name, color: '#F56565', kind: 'collection' });
        setActiveCommunity(null);
        cy.animate({ fit: { eles: members, padding: 80 } }, { duration: 400 });
        const items = members
            .sort((a, b) => (a.data('year') || 0) - (b.data('year') || 0))
            .map((n) => `<li><span class="ng-reason-dot" style="background:#F56565"></span><span>${movieLink(n)}${n.data('year') ? ` <span class="ng-detail-meta">(${escapeHtml(n.data('year'))})</span>` : ''}</span></li>`)
            .join('');
        const panel = el('ng-detail-body');
        panel.innerHTML = `
            <div class="ng-detail-title">${escapeHtml(coll.name)}</div>
            <div class="ng-insight-lead">Franchise · ${plural(members.length, 'movie')} on the map</div>
            <ul class="ng-reason-list">${items}</ul>
        `;
        bindReasonLinks(panel);
        setStatus(`${coll.name} — ${plural(members.length, 'movie')}`);
    }

    /**
     * After an expand: ease only the newly added nodes out around the node they came from,
     * with that node pinned, so the rest of the cluster map doesn't move.
     */
    function runIncrementalLayout(added, originNode) {
        const layoutEdges = appliedLayout === 'taste'
            ? cy.edges().filter((e) => e.hasClass('taste') || PERSON_EDGE_TYPES.has(e.data('type')))
            : relationshipEdges();
        let eles = added.nodes();
        if (originNode) eles = eles.union(originNode);
        eles = eles.union(eles.edgesWith(eles).intersection(layoutEdges));
        const opts = Object.assign(window.NG_CYTOSCAPE_LAYOUT(eles.nodes().length), {
            randomize: false,
            fit: false,
            animate: true,
            quality: 'default',
            packComponents: false,
            tile: false,
            gravity: 0.5,
        });
        if (originNode) opts.fixedNodeConstraint = [{ nodeId: originNode.id(), position: { ...originNode.position() } }];
        eles.layout(opts).run();
    }

    // ---- Taste map: data, personal layers, markers ----

    async function loadTaste() {
        try {
            const res = await fetch(TASTE_URL);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            applyTaste(await res.json());
        } catch (err) {
            console.error('Failed to load taste map', err);
            setTasteStatus('unavailable');
        }
    }

    function applyTaste(data) {
        me = data.me || null;
        if (data.status === 'ready') {
            taste = data;
            setTasteStatus('ready');
        } else if (data.status === 'pending' && tastePolls < TASTE_POLL_MAX) {
            tastePolls += 1;
            setTasteStatus('pending');
            setTimeout(loadTaste, TASTE_POLL_MS);
        } else {
            setTasteStatus('unavailable');
        }
        if (!cy) return; // renderGraph applies everything once the graph arrives
        const hadTasteEdges = cy.edges('.taste').nonempty();
        addTasteEdges();
        refreshPersonalLayers();
        renderRegionList();
        // the saved layout was Taste space but the data only just arrived: switch now
        if (taste && settings.layout === 'taste' && (appliedLayout !== 'taste' || !hadTasteEdges)) runLayout();
    }

    function setTasteStatus(state) {
        const text = {
            ready: 'Taste map: ready',
            pending: 'Taste map: building…',
            unavailable: 'Taste map: unavailable',
        }[state];
        el('ng-status-taste').textContent = text;
        el('ng-status-taste').title = state === 'unavailable'
            ? 'The recommender model isn’t available - relationship features still work'
            : '';
    }

    /** Add the recommender's taste-neighbour links between movies that are on the map. */
    function addTasteEdges() {
        if (!cy || !taste || !taste.neighbors) return;
        const els = [];
        const seen = new Set(cy.edges('.taste').map((e) => e.id()));
        for (const [source, list] of Object.entries(taste.neighbors)) {
            if (cy.getElementById(source).empty()) continue;
            for (const [target, sim] of list) {
                if (cy.getElementById(target).empty()) continue;
                const [a, b] = source < target ? [source, target] : [target, source];
                const id = `taste__${a}__${b}`;
                if (seen.has(id)) continue;
                seen.add(id);
                els.push({
                    group: 'edges',
                    data: { id, source: a, target: b, type: 'taste', sim, weight: sim * 4, ecolor: I.OTHER },
                    classes: appliedLayout === 'taste' ? 'taste ng-taste-on' : 'taste',
                });
            }
        }
        if (els.length) cy.add(els);
    }

    function refreshPersonalLayers() {
        if (!cy) return;
        applyPersonalClasses();
        applyColors();
        buildMarkers();
        updateControls();
    }

    /** ratings / watchlist / picks / highlight classes on movie nodes. */
    function applyPersonalClasses() {
        const ratings = (me && me.ratings) || {};
        const watchlist = new Set((me && me.watchlist) || []);
        const picks = new Set((me && me.picks) || []);
        cy.batch(() => {
            cy.nodes('.movie').forEach((node) => {
                const id = node.id();
                const rating = ratings[id];
                const mine = typeof rating === 'number';
                node.toggleClass('ng-mine', mine);
                if (mine) node.data('mineColor', I.ratingColor(rating, me));
                node.toggleClass('ng-watchlist', watchlist.has(id));
                node.toggleClass('ng-pick', picks.has(id) && !mine);
                node.toggleClass('ng-highlight', settings.highlight);
                node.toggleClass('ng-muted', settings.highlight && !mine && !watchlist.has(id));
            });
        });
    }

    function applyColors() {
        if (!cy) return;
        const scale = I.colorScale(settings.colorBy, allMovieInfos(), {
            me: me || {},
            regions: regionList,
            regionColors,
        });
        cy.batch(() => {
            cy.nodes('.movie').forEach((node) => {
                const color = scale.colorOf(movieInfo(node));
                node.data('vizColor', color || I.OTHER);
                node.toggleClass('ng-novalue', !color);
            });
            // Wikipedia-map look: links take their source movie's colour (used in dots view)
            cy.edges().not('.ng-you-link').forEach((edge) => {
                edge.data('ecolor', edge.source().data('vizColor') || I.OTHER);
            });
        });
        renderLegend(scale);
    }

    function renderLegend(scale) {
        el('ng-legend-title').textContent = COLOR_BY_LABELS[settings.colorBy] || '';
        el('ng-legend-note').textContent = scale.note || '';
        el('ng-legend-scale').innerHTML = scale.legend.map((entry) => `
            <div class="ng-legend-item" title="${escapeHtml(entry.label)}">
                <span class="ng-legend-swatch" style="background:${entry.color}"></span>
                <span>${escapeHtml(entry.label)}</span>
            </div>
        `).join('');
    }

    function removeMarkers() {
        for (const m of markers) m.node.remove();
        markers = [];
    }

    function buildMarkers() {
        removeMarkers();
        if (!me) return;
        const hasFootprint = Object.keys(me.ratings || {}).length || (me.anchors || []).length;
        if (hasFootprint) addMarker({ id: 'ng-marker-you', label: 'YOU', avatar: me.avatar, kind: 'you' });
        if (settings.users && taste) {
            for (const [userNodeId, user] of Object.entries(taste.users || {})) {
                addMarker({
                    id: `ng-marker-${userNodeId}`, label: user.username, avatar: user.avatar,
                    kind: 'other', anchors: user.anchors,
                });
            }
        }
        positionMarkers();
    }

    function addMarker({ id, label, avatar, kind, anchors = [] }) {
        const data = { id, label, kind };
        if (avatar) data.avatar = avatar;
        const node = cy.add({
            group: 'nodes',
            data,
            classes: `ng-marker ng-marker--${kind}`,
            grabbable: false,
            position: { x: 0, y: 0 },
        });
        markers.push({ node, kind, anchors });
    }

    function nodePosition(id) {
        const node = cy.getElementById(id);
        return node.nonempty() ? node.position() : null;
    }

    /**
     * Where a marker sits:
     * - you, Relationships layout: the pull of the movies you actually rated highly;
     * - you, Taste space: the model's own view - the movies nearest your learned taste vector;
     * - other users: the movies nearest their taste vector (their anchors), in both layouts.
     */
    function markerPosition(marker) {
        if (marker.kind === 'you') {
            const liked = I.weightedCentroid(I.likedWeights(me.ratings || {}), nodePosition);
            const model = I.weightedCentroid(me.anchors || [], nodePosition);
            return appliedLayout === 'taste' ? model || liked : liked || model;
        }
        return I.weightedCentroid(marker.anchors, nodePosition);
    }

    function positionMarkers() {
        if (!cy || !markers.length) return;
        cy.batch(() => {
            for (const marker of markers) {
                const pos = markerPosition(marker);
                marker.node.style('display', pos ? 'element' : 'none');
                if (pos) marker.node.position(pos);
            }
        });
    }

    function youMarker() {
        const m = markers.find((x) => x.kind === 'you');
        return m ? m.node : null;
    }

    function focusYou() {
        const you = youMarker();
        const rated = cy.nodes('.movie').filter((n) => typeof (me.ratings || {})[n.id()] === 'number');
        if (rated.length) focusOn(rated, { collections: false, neighbors: false });
        else clearFocus();
        // dashed "where to go next" lines to the model's picks that are on the map
        if (you) {
            const picks = (me.picks || []).filter((id) => cy.getElementById(id).nonempty()).slice(0, 8);
            cy.add(picks.map((id) => ({
                group: 'edges',
                data: { id: `you-link__${id}`, source: you.id(), target: id },
                classes: 'ng-you-link',
            })));
            picks.forEach((id) => cy.getElementById(id).removeClass('ng-faded').addClass('ng-hood'));
            cy.animate({ center: { eles: you }, zoom: Math.max(cy.zoom(), 0.9) }, { duration: 400 });
        }
        showYourMap();
    }

    function focusUser(markerNode) {
        const marker = markers.find((m) => m.node.id() === markerNode.id());
        if (!marker) return;
        const anchorIds = new Set(marker.anchors.map(([id]) => id));
        const anchors = cy.nodes('.movie').filter((n) => anchorIds.has(n.id()));
        if (anchors.length) focusOn(anchors, { collections: false, neighbors: false });
        const items = marker.anchors
            .map(([id]) => cy.getElementById(id))
            .filter((n) => n.nonempty())
            .map((n) => `<li><span class="ng-reason-link" data-node-id="${escapeHtml(n.id())}">${escapeHtml(n.data('label'))}</span></li>`)
            .join('');
        const panel = el('ng-detail-body');
        panel.innerHTML = `
            <div class="ng-detail-title">${escapeHtml(markerNode.data('label'))}</div>
            <div class="ng-insight-lead">Another user, placed by the recommender at the movies closest to their taste.</div>
            <div class="ng-detail-section-title">Closest movies to their taste</div>
            <ul class="ng-reason-list">${items || '<li>None of them are on the map.</li>'}</ul>
        `;
        bindReasonLinks(panel);
        setStatus(`User: ${markerNode.data('label')}`);
    }

    // ---- Controls ----

    function updateControls() {
        document.querySelectorAll('[data-setting]').forEach((btn) => {
            btn.setAttribute('aria-pressed', String(settings[btn.dataset.setting] === btn.dataset.value));
        });
        const tasteBtn = document.querySelector('[data-setting="layout"][data-value="taste"]');
        tasteBtn.disabled = !taste;
        tasteBtn.title = taste
            ? 'Movies placed by the recommender: close together = liked by the same people'
            : 'Needs the recommender’s taste map (see status bar)';
        // until the data is ready the Taste space button can't be the pressed one
        if (!taste && settings.layout === 'taste') {
            document.querySelector('[data-setting="layout"][data-value="relations"]').setAttribute('aria-pressed', 'true');
            tasteBtn.setAttribute('aria-pressed', 'false');
        }
        const select = el('ng-color-by');
        select.value = settings.colorBy;
        select.querySelector('option[value="predicted"]').disabled = !(me && me.predicted);
        el('ng-highlight-mine').checked = settings.highlight;
        el('ng-show-users').checked = settings.users;
        el('ng-show-users').disabled = !taste;
        el('ng-find-me').disabled = !youMarker();
    }

    function changeSetting(key, value) {
        if (settings[key] === value) return;
        settings[key] = value;
        saveSettings();
        if (!cy) return updateControls();
        if (key === 'layout') {
            clearFocus();
            runLayout();
        } else if (key === 'view') {
            // dots and posters have very different footprints, so islands are re-packed
            applyDensityStyling();
            runLayout();
        } else if (key === 'colorBy') {
            applyColors();
        } else if (key === 'highlight') {
            applyPersonalClasses();
        } else if (key === 'users') {
            buildMarkers();
        }
        updateControls();
    }

    // ---- Detail panels ----

    function showNodeDetail(node) {
        const type = node.data('type');
        if (type === 'director' || type === 'actor') {
            showPersonDetail(node);
        } else {
            showMovieDetail(node);
        }
    }

    function reasonDot(edge) {
        const color = window.NG_EDGE_COLORS[edge.data('type')] || '#808080';
        return `<span class="ng-reason-dot" style="background:${color}"></span>`;
    }

    function movieLink(node) {
        return `<span class="ng-reason-link" data-node-id="${escapeHtml(node.id())}">${escapeHtml(node.data('label'))}</span>`;
    }

    // clicking a movie name in any panel jumps focus to that node
    function bindReasonLinks(panel) {
        panel.querySelectorAll('.ng-reason-link').forEach((link) => {
            link.addEventListener('click', () => {
                const target = cy.getElementById(link.dataset.nodeId);
                if (target.nonempty()) selectAndCenter(target);
            });
        });
    }

    function showMovieDetail(node) {
        const raw = node.data('raw') || {};
        const panel = el('ng-detail-body');
        // strongest relationships first so the most meaningful ones are visible without scrolling
        const connectedEdges = node.connectedEdges().not('.taste, .ng-you-link')
            .sort((a, b) => (b.data('weight') || 0) - (a.data('weight') || 0));
        let reasonsHtml = '';
        connectedEdges.forEach((edge) => {
            const other = edge.source().id() === node.id() ? edge.target() : edge.source();
            if (isCompound(other) || isMarker(other)) return;
            const otherType = other.data('type');
            const reasons = edge.data('reasons') || [];
            if (reasons.length) {
                reasonsHtml += `<li>${reasonDot(edge)}<span>${movieLink(other)}: ${escapeHtml(reasons.join('; '))}</span></li>`;
            } else if (otherType === 'director' || otherType === 'actor') {
                const roleLabel = otherType === 'director' ? 'Directed by' : 'Starring';
                reasonsHtml += `<li>${reasonDot(edge)}<span>${roleLabel} ${movieLink(other)}</span></li>`;
            }
        });

        // "liked by the same people": the recommender's nearest movies in taste space
        const tasteHtml = node.connectedEdges('.taste')
            .sort((a, b) => b.data('sim') - a.data('sim'))
            .map((edge) => {
                const other = edge.source().id() === node.id() ? edge.target() : edge.source();
                return `<li><span class="ng-reason-dot" style="background:#cde2fb"></span><span>${movieLink(other)} <span class="ng-detail-meta">${Math.round(edge.data('sim') * 100)}% taste match</span></span></li>`;
            })
            .join('');

        const chips = [];
        if (raw.year) chips.push(`<span class="ng-chip">${escapeHtml(raw.year)}</span>`);
        if (raw.rating) chips.push(`<span class="ng-chip" title="TMDB rating">★ ${escapeHtml(raw.rating)}</span>`);
        if (raw.user_rating) chips.push(`<span class="ng-chip" title="Community rating">☺ ${escapeHtml(raw.user_rating)}</span>`);
        const mine = me && me.ratings[node.id()];
        if (typeof mine === 'number') {
            chips.push(`<span class="ng-chip" title="Your rating"><span class="ng-chip-dot" style="background:${I.ratingColor(mine, me)}"></span>You: ${escapeHtml(mine)}</span>`);
        } else if (me && me.predicted && typeof me.predicted[node.id()] === 'number') {
            chips.push(`<span class="ng-chip" title="What the recommender predicts you'd rate it">Predicted: ${escapeHtml(me.predicted[node.id()])}</span>`);
        }
        if (node.hasClass('ng-pick')) chips.push('<span class="ng-chip ng-chip--gold" title="One of the recommender’s top picks for you">Pick for you</span>');
        if (node.hasClass('ng-watchlist')) chips.push('<span class="ng-chip">On watchlist</span>');
        const region = regionList.find((r) => r.id === regionOf.get(node.id()));
        if (region) {
            chips.push(`<span class="ng-chip" title="Region on the map">
                <span class="ng-chip-dot" style="background:${regionColors.get(region.id) || I.OTHER}"></span>${escapeHtml(region.name)}
            </span>`);
        }

        panel.innerHTML = `
            <div class="ng-detail-header">
                <img class="ng-detail-poster" src="${escapeHtml(raw.poster || '')}" alt="" onerror="this.style.visibility='hidden'">
                <div class="ng-detail-info">
                    <div class="ng-detail-title">${escapeHtml(raw.label)}</div>
                    <div class="ng-chips">${chips.join('')}</div>
                    ${raw.studio ? `<div class="ng-detail-meta">${escapeHtml(raw.studio)}</div>` : ''}
                </div>
            </div>
            <div class="ng-detail-section-title">Connections (${connectedEdges.length})</div>
            <ul class="ng-reason-list">${reasonsHtml || '<li>No direct relationships loaded yet — try expanding.</li>'}</ul>
            ${tasteHtml ? `<div class="ng-detail-section-title">Liked by the same people</div><ul class="ng-reason-list">${tasteHtml}</ul>` : ''}
            <div class="ng-detail-actions">
                ${raw.tmdb_id ? `<a class="ng-btn" href="/movies/${encodeURIComponent(raw.tmdb_id)}/">Open page</a>` : ''}
                <button class="ng-btn" id="ng-expand-current" type="button">Expand neighbors</button>
            </div>
        `;
        bindReasonLinks(panel);
        const btn = el('ng-expand-current');
        if (btn) btn.onclick = () => expandNode(node);
        setStatus(`Selected: ${raw.label || node.data('label')}`);
    }

    function showPersonDetail(node) {
        const raw = node.data('raw') || {};
        const panel = el('ng-detail-body');
        const type = node.data('type');
        const roleLabel = type === 'director' ? 'Director' : 'Actor';
        const movieEdges = node.connectedEdges().filter((edge) => {
            const other = edge.source().id() === node.id() ? edge.target() : edge.source();
            return other.data('type') === 'movie';
        });
        const moviesHtml = movieEdges.map((edge) => {
            const other = edge.source().id() === node.id() ? edge.target() : edge.source();
            const year = other.data('year') ? ` (${escapeHtml(other.data('year'))})` : '';
            return `<li>${reasonDot(edge)}<span>${movieLink(other)}${year}</span></li>`;
        }).join('');

        panel.innerHTML = `
            <div class="ng-detail-header">
                <img class="ng-detail-poster round" src="${escapeHtml(raw.profile_picture || '')}" alt="" onerror="this.style.visibility='hidden'">
                <div class="ng-detail-info">
                    <div class="ng-detail-title">${escapeHtml(raw.label)}</div>
                    <div class="ng-chips"><span class="ng-chip">
                        <span class="ng-chip-dot" style="background:${window.NG_EDGE_COLORS[type === 'director' ? 'directed_by' : 'acted_in']}"></span>${roleLabel}
                    </span></div>
                </div>
            </div>
            <div class="ng-detail-section-title">Movies in view (${movieEdges.length})</div>
            <ul class="ng-reason-list">${moviesHtml || '<li>No movies loaded for this person yet.</li>'}</ul>
            <button class="ng-btn ng-expand-btn" id="ng-expand-current" type="button">Expand filmography</button>
        `;
        bindReasonLinks(panel);
        const btn = el('ng-expand-current');
        if (btn) btn.onclick = () => expandNode(node);
        setStatus(`Selected: ${raw.label || node.data('label')}`);
    }

    function barRows(items, { labelOf = (x) => x.key, valueOf = (x) => `${Math.round(x.share * 100)}%` } = {}) {
        const max = Math.max(...items.map((x) => x.share), 0.0001);
        return `<div class="ng-bars">${items.map((x) => `
            <span class="ng-bar-label" title="${escapeHtml(labelOf(x))}">${escapeHtml(labelOf(x))}</span>
            <span class="ng-bar-track"><span class="ng-bar-fill" style="display:block;width:${Math.round((x.share / max) * 100)}%"></span></span>
            <span class="ng-bar-value">${escapeHtml(valueOf(x))}</span>
        `).join('')}</div>`;
    }

    function deltaHtml(delta, suffix = 'vs everyone') {
        if (typeof delta !== 'number') return '<span class="ng-detail-meta">not enough ratings</span>';
        const up = delta >= 0;
        return `<span class="${up ? 'ng-delta-up' : 'ng-delta-down'}">${up ? '▲' : '▼'} ${up ? '+' : ''}${delta.toFixed(1)}</span> <span class="ng-detail-meta">${suffix}</span>`;
    }

    const TRAIT_KIND_LABELS = { genre: 'genre', keyword: 'theme', decade: 'era', director: 'director' };

    /** "What makes this cluster": distinctive traits, composition and how you relate to it. */
    function showRegionProfile(regionId) {
        const community = regionList.find((r) => r.id === regionId);
        if (!community) return;
        const ids = new Set(community.nodes);
        const members = cy.nodes('.movie').filter((n) => ids.has(n.id())).map(movieInfo);
        const profile = I.clusterProfile(members, allMovieInfos(), me || {});
        const color = regionColors.get(regionId) || I.OTHER;

        const traits = profile.traits.map((t) => `
            <span class="ng-trait" title="${escapeHtml(`${t.count} of ${profile.size} movies — ${t.lift}× more common here than across the whole map`)}">
                <span class="ng-trait-kind">${TRAIT_KIND_LABELS[t.kind]}</span>${escapeHtml(t.key)}<span class="ng-trait-lift">${t.lift}×</span>
            </span>`).join('');

        // franchises in this region (clickable) and its recurring directors
        const franchises = [...collectionsOnMap().values()]
            .map((c) => ({ ...c, here: c.ids.filter((id) => ids.has(id)).length }))
            .filter((c) => c.here >= 2)
            .sort((a, b) => b.here - a.here);
        const franchisesHtml = franchises.length ? `
            <div class="ng-detail-section-title">Franchises here</div>
            <ul class="ng-reason-list">${franchises.slice(0, 8).map((c) => `
                <li><span class="ng-reason-dot" style="background:#F56565"></span><span><span class="ng-reason-link" data-collection-id="${escapeHtml(c.id)}">${escapeHtml(c.name)}</span> <span class="ng-detail-meta">${c.here} movies</span></span></li>`).join('')}
            </ul>` : '';
        const directorCounts = new Map();
        members.forEach((m) => (m.director_names || []).forEach((d) => directorCounts.set(d, (directorCounts.get(d) || 0) + 1)));
        const directors = [...directorCounts.entries()].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 5);
        const directorsHtml = directors.length ? `
            <div class="ng-detail-section-title">Directors</div>
            ${barRows(directors.map(([key, count]) => ({ key, count, share: count / profile.size })), { valueOf: (x) => String(x.count) })}` : '';

        const you = profile.you;
        let youHtml = '';
        if (me) {
            const top = you.topPredicted.map((m) => {
                const node = cy.getElementById(m.id);
                return `<li><span>${movieLink(node)} <span class="ng-detail-meta">predicted ${fmt(m.predicted)}</span></span></li>`;
            }).join('');
            youHtml = `
                <div class="ng-detail-section-title">You &times; this region</div>
                <div class="ng-stats">
                    <div class="ng-stat"><div class="ng-stat-value">${you.watched}/${profile.size}</div><div class="ng-stat-label">movies you rated</div></div>
                    <div class="ng-stat"><div class="ng-stat-value">${fmt(you.yourAvg)}</div><div class="ng-stat-label">your average</div></div>
                </div>
                <div class="ng-detail-meta" style="margin-top:4px">${you.watched ? deltaHtml(you.delta, 'vs everyone on the same movies') : 'You haven’t rated any of these yet.'}</div>
                ${typeof you.predictedAvg === 'number' ? `<div class="ng-detail-meta" style="margin-top:2px">Predicted fit for the rest: <strong>${fmt(you.predictedAvg)}</strong></div>` : ''}
                ${top ? `<ul class="ng-reason-list" style="margin-top:4px">${top}</ul>` : ''}
            `;
        }

        el('ng-detail-body').innerHTML = `
            <div class="ng-detail-title"><span class="ng-community-swatch" style="display:inline-block;background:${color};margin-right:6px"></span>${escapeHtml(community.name)}</div>
            <div class="ng-insight-lead">${plural(profile.size, 'movie')} · region profile</div>
            <div class="ng-detail-section-title">What makes it distinctive</div>
            ${traits ? `<div class="ng-traits">${traits}</div>` : '<div class="ng-detail-meta">Nothing stands out — a mixed bag.</div>'}
            ${franchisesHtml}
            <div class="ng-detail-section-title">Genres</div>
            ${barRows(profile.genres)}
            ${directorsHtml}
            <div class="ng-detail-section-title">Decades</div>
            ${barRows(profile.decades, { valueOf: (x) => String(x.count) })}
            <div class="ng-detail-section-title">Ratings</div>
            <div class="ng-stats">
                <div class="ng-stat"><div class="ng-stat-value">${fmt(profile.avgTmdb)}</div><div class="ng-stat-label">avg TMDB</div></div>
                <div class="ng-stat"><div class="ng-stat-value">${fmt(profile.avgCommunity)}</div><div class="ng-stat-label">avg community</div></div>
            </div>
            ${youHtml}
        `;
        bindReasonLinks(el('ng-detail-body'));
        el('ng-detail-body').querySelectorAll('[data-collection-id]').forEach((link) => {
            link.addEventListener('click', () => focusCollection(link.dataset.collectionId));
        });
    }

    const LANGUAGE_NAMES = (() => {
        try {
            return new Intl.DisplayNames(['en'], { type: 'language' });
        } catch (err) {
            return null;
        }
    })();

    function tasteLabel(section, key) {
        if (section === 'languages' && LANGUAGE_NAMES) {
            try {
                return LANGUAGE_NAMES.of(key) || key;
            } catch (err) {
                return key;
            }
        }
        if (section === 'decades') return `${key}s`;
        if (section === 'runtime') return `${key} runtime`;
        return key;
    }

    /** Diverging bars for one taste-profile section: likes grow right, dislikes grow left. */
    function dnaRows(section, values) {
        const dna = I.tasteDna(values, section === 'genres' ? {} : { likes: 2, dislikes: 2 });
        const rows = [...dna.likes, ...dna.dislikes];
        if (!rows.length) return '';
        const maxAbs = Math.max(...rows.map(([, v]) => Math.abs(v)));
        return rows.map(([key, v]) => {
            const width = Math.round((Math.abs(v) / maxAbs) * 50);
            const label = tasteLabel(section, key);
            return `
                <span class="ng-bar-label" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
                <span class="ng-bar-track ng-bar-track--diverging"><span class="ng-bar-fill ${v > 0 ? 'ng-bar-fill--like' : 'ng-bar-fill--dislike'}" style="width:${width}%"></span></span>
                <span class="ng-bar-value">${v > 0 ? '+' : ''}${v.toFixed(1)}</span>`;
        }).join('');
    }

    /** "Your map": where you are, your taste DNA, your clusters, picks and taste neighbours. */
    function showYourMap() {
        if (!me) return;
        const ratedCount = Object.keys(me.ratings || {}).length;
        const clusters = I.yourClusters(allMovieInfos(), regionList, me);
        const home = clusters.filter((c) => c.pull > 0).sort((a, b) => b.pull - a.pull)[0];
        const yours = clusters.filter((c) => c.watched).sort((a, b) => b.watched - a.watched).slice(0, 5);

        const profile = me.profile || {};
        const dnaSections = ['genres', 'decades', 'languages', 'runtime']
            .map((section) => [section, dnaRows(section, profile[section])])
            .filter(([, html]) => html);
        const dnaHtml = dnaSections.length
            ? `<div class="ng-bars">${dnaSections.map(([, html]) => html).join('')}</div>
               <div class="ng-detail-meta" style="margin-top:3px">Points above/below what the model expects you to give — learned from your ratings.</div>`
            : `<div class="ng-detail-meta">${taste ? 'Rate a few more movies and the model will learn your taste.' : 'Still being computed…'}</div>`;

        const yourClustersHtml = yours.length
            ? `<div class="ng-bars">${yours.map((c) => `
                <span class="ng-bar-label"><span class="ng-reason-link" data-comm-id="${escapeHtml(c.id)}" title="${escapeHtml(c.name)}">${escapeHtml(c.name)}</span></span>
                <span class="ng-bar-track"><span class="ng-bar-fill" style="display:block;width:${Math.round((c.watched / c.size) * 100)}%;background:${regionColors.get(c.id) || I.OTHER}"></span></span>
                <span class="ng-bar-value">${c.watched}/${c.size}${typeof c.delta === 'number' ? ` <span class="${c.delta >= 0 ? 'ng-delta-up' : 'ng-delta-down'}">${c.delta >= 0 ? '+' : ''}${c.delta.toFixed(1)}</span>` : ''}</span>
            `).join('')}</div>
            <div class="ng-detail-meta" style="margin-top:3px">Seen / region size, and how you rate it vs everyone.</div>`
            : '<div class="ng-detail-meta">None of the regions contain movies you’ve rated.</div>';

        const picks = (me.picks || [])
            .map((id) => cy.getElementById(id))
            .filter((n) => n.nonempty() && typeof (me.ratings || {})[n.id()] !== 'number')
            .slice(0, 8)
            .map((n) => `<li><span class="ng-reason-dot" style="background:#FFD700"></span><span>${movieLink(n)}${me.predicted && typeof me.predicted[n.id()] === 'number' ? ` <span class="ng-detail-meta">predicted ${fmt(me.predicted[n.id()])}</span>` : ''}</span></li>`)
            .join('');

        // taste neighbours: other users whose placement lands closest to yours
        let neighboursHtml = '';
        const you = youMarker();
        if (taste && you && Object.keys(taste.users || {}).length) {
            const youPos = you.position();
            const near = Object.values(taste.users)
                .map((u) => ({ name: u.username, pos: I.weightedCentroid(u.anchors, nodePosition) }))
                .filter((u) => u.pos)
                .sort((a, b) => I.distance(youPos, a.pos) - I.distance(youPos, b.pos))
                .slice(0, 3);
            if (near.length) {
                neighboursHtml = `
                    <div class="ng-detail-section-title">Taste neighbours</div>
                    <ul class="ng-reason-list">${near.map((u) => `<li><span>${escapeHtml(u.name)}</span></li>`).join('')}</ul>
                    <div class="ng-detail-meta">Users who land closest to you on the map.</div>`;
            }
        }

        const panel = el('ng-detail-body');
        panel.innerHTML = `
            <div class="ng-detail-header">
                ${me.avatar ? `<img class="ng-detail-poster round" src="${escapeHtml(me.avatar)}" alt="" onerror="this.style.visibility='hidden'">` : ''}
                <div class="ng-detail-info">
                    <div class="ng-detail-title">Your map</div>
                    <div class="ng-chips">
                        <span class="ng-chip">${plural(ratedCount, 'movie')} rated</span>
                        <span class="ng-chip">${plural((me.watchlist || []).length, 'movie')} on watchlist</span>
                    </div>
                    ${home ? `<div class="ng-detail-meta">Home region: <span class="ng-reason-link" data-comm-id="${escapeHtml(home.id)}">${escapeHtml(home.name)}</span></div>` : ''}
                </div>
            </div>
            <div class="ng-detail-section-title">Taste DNA</div>
            ${dnaHtml}
            <div class="ng-detail-section-title">Your regions</div>
            ${yourClustersHtml}
            ${picks ? `<div class="ng-detail-section-title">Picks for you</div><ul class="ng-reason-list">${picks}</ul>` : ''}
            ${neighboursHtml}
        `;
        bindReasonLinks(panel);
        panel.querySelectorAll('[data-comm-id]').forEach((link) => {
            link.addEventListener('click', () => focusRegion(link.dataset.commId));
        });
        setStatus('Your map');
    }

    async function expandNode(node) {
        const type = node.data('type');
        showLoading(true);
        try {
            let data;
            if (type === 'director' || type === 'actor') {
                const personId = node.data('person_id');
                if (!personId) return;
                const res = await fetch(`${EXPAND_URL}?person_id=${personId}&limit=12`);
                data = await res.json();
            } else {
                const tmdbId = node.data('tmdb_id');
                if (!tmdbId) return;
                const showPeople = el('ng-show-people').checked ? '1' : '0';
                const res = await fetch(`${EXPAND_URL}?tmdb_id=${tmdbId}&limit=15&people=${showPeople}`);
                data = await res.json();
            }
            mergeExpandedData(data, node);
        } catch (err) {
            console.error('Failed to expand node', err);
        } finally {
            showLoading(false);
        }
    }

    function mergeExpandedData(data, originNode) {
        const newEls = [];
        addCollections(data.compound_groups);
        for (const node of data.nodes || []) {
            if (node.type === 'user' || !cy.getElementById(node.id).empty()) continue;
            newEls.push(movieElementFrom(node));
        }
        for (const edge of data.edges || []) {
            const source = edge.source || edge.from;
            const target = edge.target || edge.to;
            if (!cy.getElementById(`${source}__${target}`).empty()) continue;
            if (cy.getElementById(source).empty() || cy.getElementById(target).empty()) {
                // one endpoint isn't loaded yet (edge references a node outside the expand radius) - skip
                if (!newEls.some((e) => e.data && e.data.id === source) && !newEls.some((e) => e.data && e.data.id === target)) {
                    continue;
                }
            }
            newEls.push(edgeElementFrom(edge));
        }

        const added = cy.add(newEls);
        if (added.length) {
            removeHulls();
            // new neighbours start on top of the node they came from, then the incremental
            // (non-randomized) layout eases them outward without reshuffling the whole graph
            // search results have no origin node - drop them at the centre of the current view
            const pan = cy.pan();
            const origin = originNode ? originNode.position() : {
                x: (cy.width() / 2 - pan.x) / cy.zoom(),
                y: (cy.height() / 2 - pan.y) / cy.zoom(),
            };
            // small jitter so the physics has a direction to push them apart in
            added.nodes().positions(() => ({
                x: origin.x + (Math.random() - 0.5) * 40,
                y: origin.y + (Math.random() - 0.5) * 40,
            }));
            addTasteEdges();
            markBackbone();
            applyPersonalClasses();
            applyColors();
            runIncrementalLayout(added, originNode);
            updateStatusCounts();
            setStatus(`Added ${plural(added.nodes().length, 'node')}`, true);
            if (originNode) focusOn(originNode);
        }
        if (originNode) {
            cy.animate({ center: { eles: originNode }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 300 });
        }
    }

    function focusRegion(regionId) {
        const region = regionList.find((r) => r.id === regionId);
        if (!region) return;
        const ids = new Set(region.nodes);
        const eles = cy.nodes('.movie').filter((n) => ids.has(n.id()));
        if (!eles.length) return;
        focusOn(eles, { collections: false, neighbors: false });
        drawHull(eles, { label: region.name, color: regionColors.get(regionId) || I.OTHER, kind: 'community' });
        setActiveCommunity(regionId);
        setStatus(`${region.name} — ${plural(eles.length, 'movie')}`);
        cy.animate({ fit: { eles, padding: 60 } }, { duration: 400 });
        showRegionProfile(regionId);
    }

    /** Sidebar list of the map's regions, biggest first, with how many you've seen. */
    function renderRegionList() {
        const container = el('ng-community-list');
        const countEl = el('ng-community-count');
        if (!regionList.length) {
            container.innerHTML = '<div class="ng-detail-empty">No regions yet.</div>';
            countEl.textContent = '';
            return;
        }
        const ratings = (me && me.ratings) || {};
        countEl.textContent = String(regionList.length);
        container.innerHTML = regionList.map((r) => {
            const seen = r.nodes.filter((nid) => typeof ratings[nid] === 'number').length;
            return `
                <div class="ng-community-item" data-comm-id="${escapeHtml(r.id)}" title="${escapeHtml(r.name)}">
                    <span class="ng-community-swatch" style="background:${regionColors.get(r.id) || I.OTHER}"></span>
                    <span class="ng-community-name">${escapeHtml(r.name)}</span>
                    ${seen ? `<span class="seen" title="Movies you've rated">${seen} seen ·</span>` : ''}
                    <span class="count">${r.size}</span>
                </div>`;
        }).join('');
        container.querySelectorAll('.ng-community-item').forEach((itemEl) => {
            itemEl.addEventListener('click', () => focusRegion(itemEl.dataset.commId));
        });
    }

    function setActiveCommunity(commId) {
        el('ng-community-list').querySelectorAll('.ng-community-item').forEach((itemEl) => {
            itemEl.classList.toggle('active', itemEl.dataset.commId === commId);
        });
    }

    let searchTimer = null;

    function initSearch() {
        const input = el('ng-search-input');
        const results = el('ng-search-results');
        input.addEventListener('input', () => {
            clearTimeout(searchTimer);
            const query = input.value.trim();
            if (query.length < 2) {
                results.classList.remove('open');
                return;
            }
            searchTimer = setTimeout(() => runSearch(query), 250);
        });
        document.addEventListener('click', (evt) => {
            if (!el('ng-search').contains(evt.target)) results.classList.remove('open');
        });
    }

    async function runSearch(query) {
        try {
            const res = await fetch(`${SEARCH_URL}?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            renderSearchResults(data.results || []);
        } catch (err) {
            console.error('Movie search failed', err);
        }
    }

    function renderSearchResults(results) {
        const box = el('ng-search-results');
        if (!results.length) {
            box.classList.remove('open');
            box.innerHTML = '';
            return;
        }
        box.innerHTML = results.map((r) => `
            <div class="ng-search-result" data-tmdb-id="${escapeHtml(r.tmdb_id)}">
                <img src="${escapeHtml(r.poster_path || '')}" alt="" onerror="this.style.visibility='hidden'">
                <div>${escapeHtml(r.title)}${r.release_date ? ` <span class="ng-search-year">(${escapeHtml(r.release_date.slice(0, 4))})</span>` : ''}</div>
            </div>
        `).join('');
        box.classList.add('open');
        box.querySelectorAll('.ng-search-result').forEach((itemEl) => {
            itemEl.addEventListener('click', () => {
                box.classList.remove('open');
                el('ng-search-input').value = '';
                focusMovie(parseInt(itemEl.dataset.tmdbId, 10));
            });
        });
    }

    async function focusMovie(tmdbId) {
        const existing = cy.nodes('.movie').filter((n) => n.data('tmdb_id') === tmdbId);
        if (existing.length) {
            selectAndCenter(existing);
            return;
        }
        showLoading(true);
        try {
            const showPeople = el('ng-show-people').checked ? '1' : '0';
            const res = await fetch(`${EXPAND_URL}?tmdb_id=${tmdbId}&limit=15&people=${showPeople}`);
            const data = await res.json();
            mergeExpandedData(data, null);
            const found = cy.nodes('.movie').filter((n) => n.data('tmdb_id') === tmdbId);
            if (found.length) selectAndCenter(found);
        } catch (err) {
            console.error('Failed to focus movie from search', err);
        } finally {
            showLoading(false);
        }
    }

    function selectAndCenter(eles) {
        setActiveCommunity(null);
        focusOn(eles);
        cy.animate({ center: { eles }, zoom: Math.max(cy.zoom(), 1.4) }, { duration: 400 });
        showNodeDetail(eles[0]);
    }

    document.addEventListener('DOMContentLoaded', () => {
        el('ng-zoom-in').addEventListener('click', () => zoomBy(1.3));
        el('ng-zoom-out').addEventListener('click', () => zoomBy(1 / 1.3));
        el('ng-zoom-fit').addEventListener('click', () => {
            if (cy) cy.animate({ fit: { eles: graphNodes(), padding: 30 } }, { duration: 300 });
        });
        document.addEventListener('keydown', (evt) => {
            if (evt.key === 'Escape' && document.activeElement !== el('ng-search-input')) clearFocus();
        });
        document.querySelectorAll('[data-setting]').forEach((btn) => {
            btn.addEventListener('click', () => changeSetting(btn.dataset.setting, btn.dataset.value));
        });
        el('ng-color-by').addEventListener('change', (evt) => changeSetting('colorBy', evt.target.value));
        el('ng-highlight-mine').addEventListener('change', (evt) => changeSetting('highlight', evt.target.checked));
        el('ng-show-users').addEventListener('change', (evt) => changeSetting('users', evt.target.checked));
        el('ng-find-me').addEventListener('click', focusYou);
        el('ng-apply').addEventListener('click', loadGraph);
        el('ng-show-people').addEventListener('change', loadGraph);
        initSearch();
        updateControls();
        // graph and taste map load in parallel; whichever lands second applies the overlay
        loadGraph();
        loadTaste();

        let resizeTimer = null;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                if (cy) cy.fit(graphNodes(), 30);
            }, 200);
        });
    });
})();
