/* Movie network graph — Cytoscape.js interaction layer.
   Depends on cytoscape-graph-config.js being loaded first for style/layout config. */

(function () {
    const DATA_URL = '/movies/network-graph/data/';
    const EXPAND_URL = '/movies/network-graph/expand/';
    const SEARCH_URL = '/movies/search-local/';

    let cy = null;
    let latestAnalytics = {};

    function el(id) {
        return document.getElementById(id);
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str === null || str === undefined ? '' : String(str);
        return div.innerHTML;
    }

    function showLoading(show) {
        el('ng-loading').classList.toggle('hidden', !show);
    }

    function buildParams() {
        const params = new URLSearchParams();
        // no rating/count filters by design - every movie is loaded, always
        params.set('rating_threshold', '0');
        params.set('movie_limit', '0');
        const myTaste = el('ng-my-taste').checked;
        params.set('social', myTaste ? '1' : '0');
        params.set('predictions', myTaste ? '1' : '0');
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
                predicted_rating: edge.predicted_rating,
            },
            classes: edge.type,
        };
    }

    function movieElementFrom(node, parent, sizeScale = 1) {
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
            predicted_score: node.predicted_score,
            size: (node.size || 24) * sizeScale,
            raw: node,
            parent,
        };
        // omit (not just blank) so the cytoscape [poster]/[profile_picture] existence
        // selectors correctly skip nodes without one instead of trying background-image: ''
        if (node.poster) data.poster = node.poster;
        if (node.profile_picture) data.profile_picture = node.profile_picture;
        return { group: 'nodes', data, classes: node.type };
    }

    async function loadGraph() {
        showLoading(true);
        try {
            const res = await fetch(`${DATA_URL}?${buildParams().toString()}`);
            const data = await res.json();
            renderGraph(data);
        } catch (err) {
            console.error('Failed to load network graph', err);
        } finally {
            showLoading(false);
        }
    }

    function renderGraph(data) {
        const nodes = data.nodes || [];
        const edges = data.edges || [];
        const compoundGroups = data.compound_groups || [];
        latestAnalytics = data.analytics || {};
        const commMap = communityLookup(latestAnalytics);
        // large graphs (e.g. 500 movies) overlap badly at full poster size - shrink them down
        const sizeScale = nodes.length > 300 ? 0.5 : nodes.length > 150 ? 0.7 : 1;

        el('ng-empty-state').classList.toggle('visible', nodes.length === 0);

        const elements = [];
        const communityIds = new Set();
        for (const info of commMap.values()) {
            if (info.size >= 3 && !communityIds.has(info.id)) {
                communityIds.add(info.id);
                elements.push({
                    group: 'nodes',
                    data: { id: info.id, label: info.name, isCommunity: true },
                    classes: 'community-parent',
                });
            }
        }

        const movieToCollection = new Map();
        for (const group of compoundGroups) {
            for (const mid of group.movie_ids) movieToCollection.set(mid, group.id);
        }
        for (const group of compoundGroups) {
            const sampleId = group.movie_ids[0];
            const commInfo = commMap.get(sampleId);
            const parent = commInfo && communityIds.has(commInfo.id) ? commInfo.id : undefined;
            elements.push({
                group: 'nodes',
                data: { id: group.id, label: group.label, isCollection: true, parent },
                classes: 'collection-parent',
            });
        }

        for (const node of nodes) {
            let parent = movieToCollection.get(node.id);
            if (!parent) {
                const commInfo = commMap.get(node.id);
                if (commInfo && communityIds.has(commInfo.id)) parent = commInfo.id;
            }
            elements.push(movieElementFrom(node, parent, sizeScale));
        }

        for (const edge of edges) {
            elements.push(edgeElementFrom(edge));
        }

        if (!cy) {
            initCytoscape(elements);
        } else {
            cy.elements().remove();
            cy.add(elements);
            applyDensityStyling();
            runLayout();
        }

        renderCommunityList(latestAnalytics);
    }

    function initCytoscape(elements) {
        cy = cytoscape({
            container: el('ng-cytoscape'),
            elements,
            style: window.NG_CYTOSCAPE_STYLE,
            wheelSensitivity: 0.25,
            minZoom: 0.1,
            maxZoom: 4,
            // keep large graphs responsive while panning/zooming instead of redrawing every frame
            hideEdgesOnViewport: elements.length > 400,
            textureOnViewport: elements.length > 400,
            pixelRatio: 'auto',
        });

        cy.on('tap', 'node', (evt) => {
            const node = evt.target;
            if (node.data('isCommunity') || node.data('isCollection')) return;
            showNodeDetail(node);
        });

        cy.on('dbltap', 'node', (evt) => {
            const node = evt.target;
            if (node.data('isCommunity') || node.data('isCollection')) return;
            expandNode(node);
        });

        applyDensityStyling();
        runLayout();
    }

    // Thousands of crisscrossing edges at once reads as noise more than signal -
    // fade them out (and shrink hairline width) once the graph gets large.
    function applyDensityStyling() {
        if (!cy) return;
        const count = cy.nodes().length;
        const opacity = count > 600 ? 0.12 : count > 300 ? 0.2 : count > 150 ? 0.35 : 0.55;
        cy.style().selector('edge').style({ opacity }).update();
    }

    function runLayout() {
        if (!cy) return;
        const layout = cy.layout(window.NG_CYTOSCAPE_LAYOUT(cy.nodes().length));
        layout.run();
    }

    function showNodeDetail(node) {
        const type = node.data('type');
        if (type === 'director' || type === 'actor') {
            showPersonDetail(node);
        } else {
            showMovieDetail(node);
        }
    }

    function showMovieDetail(node) {
        const raw = node.data('raw') || {};
        const panel = el('ng-detail-body');
        const connectedEdges = node.connectedEdges();
        let reasonsHtml = '';
        connectedEdges.forEach((edge) => {
            const other = edge.source().id() === node.id() ? edge.target() : edge.source();
            if (other.data('isCommunity') || other.data('isCollection')) return;
            const otherType = other.data('type');
            const reasons = edge.data('reasons') || [];
            if (reasons.length) {
                reasonsHtml += `<li><strong>${escapeHtml(other.data('label'))}</strong>: ${escapeHtml(reasons.join('; '))}</li>`;
            } else if (otherType === 'director' || otherType === 'actor') {
                const roleLabel = otherType === 'director' ? 'Directed by' : 'Starring';
                reasonsHtml += `<li><strong>${roleLabel}</strong>: ${escapeHtml(other.data('label'))}</li>`;
            }
        });

        const ratingLine = [
            raw.year || '',
            raw.rating ? `TMDB ${raw.rating}` : '',
            raw.user_rating ? `Community ${raw.user_rating}` : '',
        ].filter(Boolean).join(' \u00b7 ');

        panel.innerHTML = `
            <div class="ng-detail-header">
                <img class="ng-detail-poster" src="${escapeHtml(raw.poster || '')}" onerror="this.style.visibility='hidden'">
                <div>
                    <div class="ng-detail-title">${escapeHtml(raw.label)}</div>
                    <div class="ng-detail-meta">${escapeHtml(ratingLine)}</div>
                    <div class="ng-detail-meta">${escapeHtml(raw.studio || '')}</div>
                </div>
            </div>
            <div class="ng-detail-section-title">Connections (${connectedEdges.length})</div>
            <ul class="ng-reason-list">${reasonsHtml || '<li>No direct relationships loaded yet \u2014 try expanding.</li>'}</ul>
            <button class="ng-btn ng-expand-btn" id="ng-expand-current" type="button">Expand neighbors</button>
        `;
        const btn = el('ng-expand-current');
        if (btn) btn.onclick = () => expandNode(node);
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
            return `<li>${escapeHtml(other.data('label'))}${other.data('year') ? ` (${other.data('year')})` : ''}</li>`;
        }).join('');

        panel.innerHTML = `
            <div class="ng-detail-header">
                <img class="ng-detail-poster" src="${escapeHtml(raw.profile_picture || '')}" onerror="this.style.visibility='hidden'">
                <div>
                    <div class="ng-detail-title">${escapeHtml(raw.label)}</div>
                    <div class="ng-detail-meta">${roleLabel}</div>
                </div>
            </div>
            <div class="ng-detail-section-title">Movies in view (${movieEdges.length})</div>
            <ul class="ng-reason-list">${moviesHtml || '<li>No movies loaded for this person yet.</li>'}</ul>
            <button class="ng-btn ng-expand-btn" id="ng-expand-current" type="button">Expand filmography</button>
        `;
        const btn = el('ng-expand-current');
        if (btn) btn.onclick = () => expandNode(node);
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
        const compoundGroups = data.compound_groups || [];
        for (const group of compoundGroups) {
            if (cy.getElementById(group.id).empty()) {
                newEls.push({
                    group: 'nodes',
                    data: { id: group.id, label: group.label, isCollection: true },
                    classes: 'collection-parent',
                });
            }
        }
        const movieToCollection = new Map();
        for (const group of compoundGroups) {
            for (const mid of group.movie_ids) movieToCollection.set(mid, group.id);
        }
        for (const node of data.nodes || []) {
            if (!cy.getElementById(node.id).empty()) continue;
            newEls.push(movieElementFrom(node, movieToCollection.get(node.id)));
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
            cy.layout({ name: 'fcose', animate: true, fit: false, randomize: false, nodeRepulsion: 6000 }).run();
        }
        if (originNode) {
            cy.animate({ center: { eles: originNode }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 300 });
        }
    }

    function renderCommunityList(analytics) {
        const container = el('ng-community-list');
        const communities = analytics && analytics.communities && analytics.communities.communities;
        if (!communities || !Object.keys(communities).length) {
            container.innerHTML = '<div class="ng-detail-empty">No communities detected yet.</div>';
            return;
        }
        const entries = Object.entries(communities)
            .filter(([, data]) => (data.size || 0) >= 2)
            .sort((a, b) => (b[1].size || 0) - (a[1].size || 0))
            .slice(0, 40);

        container.innerHTML = entries.map(([id, data]) => `
            <div class="ng-community-item" data-comm-id="${id}">
                ${escapeHtml(data.name)} <span class="count">(${data.size})</span>
            </div>
        `).join('') || '<div class="ng-detail-empty">No communities detected yet.</div>';

        container.querySelectorAll('.ng-community-item').forEach((itemEl) => {
            itemEl.addEventListener('click', () => {
                const data = communities[itemEl.dataset.commId];
                const ids = new Set(data.nodes || []);
                const eles = cy.nodes().filter((n) => ids.has(n.id()));
                if (eles.length) {
                    cy.elements().unselect();
                    eles.select();
                    cy.animate({ fit: { eles, padding: 60 } }, { duration: 400 });
                }
            });
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
            <div class="ng-search-result" data-tmdb-id="${r.tmdb_id}">
                <img src="${escapeHtml(r.poster_path || '')}" onerror="this.style.visibility='hidden'">
                <div>${escapeHtml(r.title)}${r.release_date ? ' (' + r.release_date.slice(0, 4) + ')' : ''}</div>
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
        const existing = cy.nodes().filter((n) => n.data('tmdb_id') === tmdbId);
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
            const found = cy.nodes().filter((n) => n.data('tmdb_id') === tmdbId);
            if (found.length) selectAndCenter(found);
        } catch (err) {
            console.error('Failed to focus movie from search', err);
        } finally {
            showLoading(false);
        }
    }

    function selectAndCenter(eles) {
        cy.elements().unselect();
        eles.select();
        cy.animate({ center: { eles }, zoom: 1.4 }, { duration: 400 });
        showNodeDetail(eles[0]);
    }

    document.addEventListener('DOMContentLoaded', () => {
        el('ng-apply').addEventListener('click', loadGraph);
        el('ng-my-taste').addEventListener('change', loadGraph);
        el('ng-show-people').addEventListener('change', loadGraph);
        initSearch();
        loadGraph();

        let resizeTimer = null;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                if (cy) cy.fit(undefined, 30);
            }, 200);
        });
    });
})();
