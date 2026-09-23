/* Movie network graph — pure data helpers (no DOM, no Cytoscape).
   Colour scales, cluster profiles and "where am I" placement maths live here so they
   can be unit-tested headlessly; network-graph.js wires them to the UI.

   Movies are plain objects: { id, label, genres, keywords, director_names, year,
   rating (TMDB), user_rating (community avg), community }.
   `me` is the /network-graph/taste/ payload's `me`: { ratings, predicted, ... }. */

(function (global) {
    // Palettes validated with the dataviz skill's validator against the graph canvas
    // (#0b0d1f - the canvas is dark in both site themes, so only dark steps are used).
    // Categorical: 8 fixed slots, assigned in order and never cycled - anything past
    // slot 8 folds into OTHER. Position on the map is the secondary encoding for
    // clusters, which is what makes 8 slots legal on a scatter-like view.
    const CATEGORICAL = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
    const OTHER = '#4a4d63';
    // Sequential: one hue (blue), dark -> bright = low -> high on the dark canvas.
    const SEQUENTIAL = ['#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'];
    // Diverging: red arm (below) <- grey midpoint -> blue arm (above), 3 steps each.
    const DIVERGING = ['#f4a9a9', '#e66767', '#a33a3a', '#5c5c58', '#1c5cab', '#5598e7', '#9ec5f4'];
    const DIVERGING_CUTS = [-3, -1.75, -0.5, 0.5, 1.75, 3];

    const DECADE_BINS = [
        { max: 1969, label: 'Before 1970' },
        { max: 1979, label: '1970s' },
        { max: 1989, label: '1980s' },
        { max: 1999, label: '1990s' },
        { max: 2009, label: '2000s' },
        { max: Infinity, label: '2010s +' },
    ];
    const TMDB_BINS = [
        { max: 5, label: 'under 5' },
        { max: 6, label: '5 – 6' },
        { max: 7, label: '6 – 7' },
        { max: 7.5, label: '7 – 7.5' },
        { max: 8, label: '7.5 – 8' },
        { max: Infinity, label: '8 +' },
    ];

    const DEFAULT_CENTER = 6.5;

    function mean(values) {
        const xs = values.filter((v) => typeof v === 'number' && !Number.isNaN(v));
        return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
    }

    function round1(v) {
        return Math.round(v * 10) / 10;
    }

    function binIndex(value, bins) {
        return bins.findIndex((b) => value <= b.max);
    }

    /** Your personal midpoint: the diverging scales read "above/below *your* usual". */
    function ratingCenter(me) {
        const m = mean(Object.values((me && me.ratings) || {}));
        return m === null ? DEFAULT_CENTER : m;
    }

    function divergingIndex(value, center) {
        const diff = value - center;
        const i = DIVERGING_CUTS.findIndex((cut) => diff < cut);
        return i === -1 ? DIVERGING.length - 1 : i;
    }

    function divergingLegend(center) {
        const c = center;
        const f = (v) => round1(Math.max(0, Math.min(10, v)));
        return [
            { color: DIVERGING[6], label: `${f(c + 3)} +` },
            { color: DIVERGING[5], label: `${f(c + 1.75)} – ${f(c + 3)}` },
            { color: DIVERGING[4], label: `${f(c + 0.5)} – ${f(c + 1.75)}` },
            { color: DIVERGING[3], label: `≈ ${f(c)} (your average)` },
            { color: DIVERGING[2], label: `${f(c - 1.75)} – ${f(c - 0.5)}` },
            { color: DIVERGING[1], label: `${f(c - 3)} – ${f(c - 1.75)}` },
            { color: DIVERGING[0], label: `under ${f(c - 3)}` },
        ];
    }

    /**
     * Build a colour scale for a "Colour by" mode.
     * Returns { colorOf(movie) -> hex | null (no value), legend: [{color, label}], note }.
     */
    function colorScale(mode, movies, ctx = {}) {
        const me = ctx.me || {};
        if (mode === 'region') {
            const colors = ctx.regionColors || new Map();
            const regions = (ctx.regions || []).slice(0, 10);
            const legend = regions.map((r) => ({ color: colors.get(r.id) || OTHER, label: r.name }));
            if ((ctx.regions || []).length > regions.length) legend.push({ color: OTHER, label: '… more on the map' });
            return {
                colorOf: (m) => (m.community !== undefined ? colors.get(m.community) || OTHER : OTHER),
                legend,
                note: 'Colours only tell neighbouring regions apart — region names are on the map',
            };
        }
        if (mode === 'genre') {
            const counts = new Map();
            for (const m of movies) {
                const g = (m.genres || [])[0];
                if (g) counts.set(g, (counts.get(g) || 0) + 1);
            }
            const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, CATEGORICAL.length).map(([g]) => g);
            const slot = new Map(top.map((g, i) => [g, CATEGORICAL[i]]));
            const legend = top.map((g) => ({ color: slot.get(g), label: g }));
            if (counts.size > top.length) legend.push({ color: OTHER, label: 'Other genres' });
            return {
                colorOf: (m) => {
                    const g = (m.genres || [])[0];
                    return g ? slot.get(g) || OTHER : null;
                },
                legend,
                note: 'By each movie’s primary genre',
            };
        }
        if (mode === 'decade') {
            return {
                colorOf: (m) => (m.year ? SEQUENTIAL[binIndex(m.year, DECADE_BINS)] : null),
                legend: DECADE_BINS.map((b, i) => ({ color: SEQUENTIAL[i], label: b.label })).reverse(),
            };
        }
        if (mode === 'tmdb') {
            return {
                colorOf: (m) => (m.rating ? SEQUENTIAL[binIndex(m.rating, TMDB_BINS)] : null),
                legend: TMDB_BINS.map((b, i) => ({ color: SEQUENTIAL[i], label: b.label })).reverse(),
                note: 'TMDB average rating',
            };
        }
        if (mode === 'mine' || mode === 'predicted') {
            const center = ratingCenter(me);
            const source = mode === 'mine' ? me.ratings || {} : me.predicted || {};
            return {
                colorOf: (m) => {
                    const v = source[m.id];
                    return typeof v === 'number' ? DIVERGING[divergingIndex(v, center)] : null;
                },
                legend: divergingLegend(center),
                note: mode === 'mine'
                    ? 'Your ratings, relative to your own average — unrated movies are dimmed'
                    : 'What the recommender predicts you’d rate each movie',
            };
        }
        return { colorOf: () => null, legend: [] };
    }

    /** Colour for one rating value on the personal diverging scale (used by "Highlight my movies"). */
    function ratingColor(value, me) {
        return DIVERGING[divergingIndex(value, ratingCenter(me))];
    }

    function decadeLabel(year) {
        return `${Math.floor(year / 10) * 10}s`;
    }

    /** Feature extractors used for lift. Each returns an array of string keys for a movie. */
    const FEATURES = {
        genre: (m) => m.genres || [],
        keyword: (m) => m.keywords || [],
        decade: (m) => (m.year ? [decadeLabel(m.year)] : []),
        director: (m) => m.director_names || [],
    };

    function countFeatures(movies, extract) {
        const counts = new Map();
        for (const m of movies) {
            for (const key of new Set(extract(m))) counts.set(key, (counts.get(key) || 0) + 1);
        }
        return counts;
    }

    /**
     * What makes a cluster a cluster: for every genre/keyword/decade/director, lift =
     * (share of cluster movies that have it) / (share of all movies that have it).
     * Only traits carried by enough cluster members count, so one odd movie can't
     * produce a "40x" trait.
     */
    function distinctiveTraits(members, all, { limit = 8, minLift = 1.3 } = {}) {
        const nIn = members.length;
        const nAll = all.length;
        if (!nIn || !nAll) return [];
        const minSupport = Math.max(2, Math.min(3, Math.ceil(nIn * 0.2)));
        const traits = [];
        for (const [kind, extract] of Object.entries(FEATURES)) {
            const inCounts = countFeatures(members, extract);
            const allCounts = countFeatures(all, extract);
            for (const [key, cIn] of inCounts) {
                if (cIn < minSupport) continue;
                const lift = (cIn / nIn) / ((allCounts.get(key) || cIn) / nAll);
                if (lift >= minLift) traits.push({ kind, key, lift: round1(lift), count: cIn, share: cIn / nIn });
            }
        }
        // strongest first; among equals prefer the trait more members share
        traits.sort((a, b) => (b.lift - a.lift) || (b.count - a.count));
        return traits.slice(0, limit);
    }

    function topCounts(movies, extract, limit) {
        return [...countFeatures(movies, extract).entries()]
            .sort((a, b) => b[1] - a[1])
            .slice(0, limit)
            .map(([key, count]) => ({ key, count, share: count / movies.length }));
    }

    function decadeHistogram(movies) {
        const counts = countFeatures(movies, FEATURES.decade);
        return [...counts.entries()]
            .sort((a, b) => parseInt(a[0], 10) - parseInt(b[0], 10))
            .map(([key, count]) => ({ key, count, share: count / movies.length }));
    }

    /** Everything the Cluster profile panel shows, from node data alone. */
    function clusterProfile(members, all, me = {}) {
        const ratings = me.ratings || {};
        const predicted = me.predicted || {};
        const watched = members.filter((m) => typeof ratings[m.id] === 'number');
        const yourAvg = mean(watched.map((m) => ratings[m.id]));
        const theirAvg = mean(watched.map((m) => m.user_rating));
        const unwatched = members.filter((m) => typeof ratings[m.id] !== 'number' && typeof predicted[m.id] === 'number');
        return {
            size: members.length,
            traits: distinctiveTraits(members, all),
            genres: topCounts(members, FEATURES.genre, 5),
            decades: decadeHistogram(members),
            avgTmdb: mean(members.map((m) => m.rating)),
            avgCommunity: mean(members.map((m) => m.user_rating)),
            you: {
                watched: watched.length,
                yourAvg,
                // your avg vs everyone's avg on the *same* movies - positive = you like this cluster more
                delta: yourAvg !== null && theirAvg !== null ? yourAvg - theirAvg : null,
                predictedAvg: mean(unwatched.map((m) => predicted[m.id])),
                topPredicted: unwatched
                    .sort((a, b) => predicted[b.id] - predicted[a.id])
                    .slice(0, 3)
                    .map((m) => ({ id: m.id, label: m.label, predicted: predicted[m.id] })),
            },
        };
    }

    /** Placement weights from your ratings: only movies you liked pull you towards them. */
    function likedWeights(ratings) {
        const entries = Object.entries(ratings || {});
        const weighted = entries
            .map(([id, r]) => [id, Math.pow(Math.max(0, r - 5), 1.5)])
            .filter(([, w]) => w > 0);
        // nobody rated above 5? fall back to "everything you've seen, equally"
        return weighted.length ? weighted : entries.map(([id]) => [id, 1]);
    }

    /**
     * Weighted centroid of [[nodeId, weight], ...] using positionOf(nodeId) -> {x, y} | null.
     * Returns null when none of the anchors are on the map.
     */
    function weightedCentroid(weightedIds, positionOf) {
        let sx = 0;
        let sy = 0;
        let sw = 0;
        for (const [id, w] of weightedIds) {
            const p = positionOf(id);
            if (!p || !(w > 0)) continue;
            sx += p.x * w;
            sy += p.y * w;
            sw += w;
        }
        return sw ? { x: sx / sw, y: sy / sw } : null;
    }

    /** Per-community "you" stats for the Your map panel. */
    function yourClusters(movies, communities, me = {}) {
        const ratings = me.ratings || {};
        const byComm = new Map();
        for (const m of movies) {
            if (m.community === undefined) continue;
            if (!byComm.has(m.community)) byComm.set(m.community, []);
            byComm.get(m.community).push(m);
        }
        return communities
            .map((c) => {
                const members = byComm.get(c.id) || [];
                const watched = members.filter((m) => typeof ratings[m.id] === 'number');
                const yourAvg = mean(watched.map((m) => ratings[m.id]));
                const theirAvg = mean(watched.map((m) => m.user_rating));
                return {
                    id: c.id,
                    name: c.name,
                    size: members.length,
                    watched: watched.length,
                    // "home" pull: how much liked-weight you put into this cluster
                    pull: likedWeights(Object.fromEntries(watched.map((m) => [m.id, ratings[m.id]])))
                        .reduce((s, [, w]) => s + w, 0),
                    delta: yourAvg !== null && theirAvg !== null && watched.length >= 2 ? yourAvg - theirAvg : null,
                };
            })
            .filter((c) => c.size > 0);
    }

    /** Taste-profile entries sorted into the strongest likes and dislikes. */
    function tasteDna(profileSection, { likes = 4, dislikes = 3, minAbs = 0.05 } = {}) {
        const entries = Object.entries(profileSection || {}).filter(([, v]) => Math.abs(v) >= minAbs);
        return {
            likes: entries.filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, likes),
            dislikes: entries.filter(([, v]) => v < 0).sort((a, b) => a[1] - b[1]).slice(0, dislikes),
        };
    }

    function distance(a, b) {
        return Math.hypot(a.x - b.x, a.y - b.y);
    }

    /**
     * Group assignment for the cluster layout: groups smaller than `minSize` fold into
     * one shared '__misc' group so the map isn't littered with 1-2 movie islands.
     * groupOf: Map id -> key (missing = no group). Returns Map key -> [ids].
     */
    function buildGroups(ids, groupOf, { minSize = 3 } = {}) {
        const groups = new Map();
        for (const id of ids) {
            const key = groupOf.get(id);
            const k = key === undefined || key === null ? '__misc' : String(key);
            if (!groups.has(k)) groups.set(k, []);
            groups.get(k).push(id);
        }
        const misc = groups.get('__misc') || [];
        for (const [k, members] of [...groups]) {
            if (k !== '__misc' && members.length < minSize) {
                misc.push(...members);
                groups.delete(k);
            }
        }
        groups.delete('__misc');
        if (misc.length) groups.set('__misc', misc);
        return groups;
    }

    /** Small seeded PRNG so layouts and community detection are reproducible. */
    function mulberry32(seed) {
        let a = seed >>> 0;
        return function () {
            a = (a + 0x6D2B79F5) >>> 0;
            let t = a;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    /**
     * "Wikipedia map" layout: the Gephi/sigma.js recipe.
     *   - franchises (`groups`, e.g. collections) are chained with strong virtual links so
     *     community detection and the layout both treat them as one unit;
     *   - Louvain community detection finds the map's regions, then every group is moved
     *     wholesale into its majority region so a franchise is never split;
     *   - ForceAtlas2 with community-weighted attraction positions everything;
     *   - positions are rescaled to the node sizes, then noverlap removes overlaps;
     *   - movies with no links at all go on a tidy outer ring instead of piling up in the
     *     middle (gravity would otherwise drag every isolate to the centre).
     *
     * nodes: [{ id, size (rendered diameter), label }], edges: [[a, b, weight]],
     * groups: [[id, id, ...], ...] (nodes that belong together; never drawn).
     * libs: { Graph, forceAtlas2, noverlap, louvain } (graphology + graphology-library).
     * Returns { positions: Map id -> {x, y}, regions: Map id -> regionKey (linked nodes only) }.
     */
    function mapLayout(nodes, edges, libs, { seed = 7, iterations, resolution = 1, communityBoost = 6, groups = [] } = {}) {
        const { Graph, forceAtlas2, noverlap, louvain } = libs;
        const rng = mulberry32(seed);
        const graph = new Graph({ type: 'undirected', multi: false, allowSelfLoops: false });
        for (const n of nodes) {
            graph.addNode(n.id, { x: (rng() - 0.5) * 1000, y: (rng() - 0.5) * 1000, size: n.size / 2, label: n.label || '' });
        }
        let maxWeight = 0;
        for (const [a, b, w] of edges) {
            if (a === b || !graph.hasNode(a) || !graph.hasNode(b)) continue;
            maxWeight = Math.max(maxWeight, w);
            if (graph.hasEdge(a, b)) graph.updateEdgeAttribute(a, b, 'weight', (x) => Math.max(x, w));
            else graph.addEdge(a, b, { weight: w });
        }
        // franchise glue: a chain through each group, stronger than any real link
        const presentGroups = groups.map((g) => g.filter((id) => graph.hasNode(id))).filter((g) => g.length >= 2);
        const glue = (maxWeight || 1) * 2;
        for (const g of presentGroups) {
            for (let i = 1; i < g.length; i++) {
                if (graph.hasEdge(g[i - 1], g[i])) graph.updateEdgeAttribute(g[i - 1], g[i], 'weight', (x) => Math.max(x, glue));
                else graph.addEdge(g[i - 1], g[i], { weight: glue });
            }
        }

        const isolates = [];
        graph.forEachNode((id) => {
            if (graph.degree(id) === 0) isolates.push(id);
        });
        isolates.forEach((id) => graph.dropNode(id));

        const regions = new Map();
        if (graph.order) {
            if (graph.size) {
                const communities = louvain(graph, { getEdgeWeight: 'weight', resolution, rng });
                for (const [id, c] of Object.entries(communities)) regions.set(id, `r${c}`);
                // a franchise lives in exactly one region: its members' majority region
                for (const g of presentGroups) {
                    const votes = new Map();
                    g.forEach((id) => votes.set(regions.get(id), (votes.get(regions.get(id)) || 0) + 1));
                    const winner = [...votes.entries()].sort((a, b) => b[1] - a[1])[0][0];
                    g.forEach((id) => regions.set(id, winner));
                }
                // community-weighted attraction: real movie graphs are too interlinked for
                // any force layout to separate on raw weights alone, so links inside a region
                // pull harder and links between regions go slack
                if (communityBoost !== 1) {
                    graph.updateEachEdgeAttributes((edge, attr, a, b) => Object.assign(attr, {
                        weight: attr.weight * (regions.get(a) === regions.get(b) ? communityBoost : 1 / communityBoost),
                    }));
                }
            }
            const n = graph.order;
            // graphology's inferred settings (strong gravity, slowDown by graph size) measured
            // best here: with the community boost they separate regions ~5-6x on the real
            // taste graph, while LinLog mode barely converged (<2x) in the same time budget
            const settings = Object.assign(forceAtlas2.inferSettings(graph), {
                edgeWeightInfluence: 1,
                barnesHutOptimize: n > 1500,
            });
            forceAtlas2.assign(graph, {
                iterations: iterations || (n > 1500 ? 350 : 500),
                settings,
                getEdgeWeight: 'weight',
            });

            // rescale so the typical nearest-neighbour gap is ~1.6 node diameters, then de-overlap
            const ids = graph.nodes();
            const pts = ids.map((id) => graph.getNodeAttributes(id));
            const sample = pts.length > 600 ? pts.filter((_, i) => i % Math.ceil(pts.length / 600) === 0) : pts;
            let nnSum = 0;
            for (const p of sample) {
                let best = Infinity;
                for (const q of pts) {
                    if (p === q) continue;
                    const d = Math.hypot(p.x - q.x, p.y - q.y);
                    if (d < best) best = d;
                }
                if (best < Infinity) nnSum += best;
            }
            const meanNn = nnSum / Math.max(1, sample.length) || 1;
            const meanSize = pts.reduce((s, p) => s + p.size * 2, 0) / pts.length;
            const k = (meanSize * 1.6) / meanNn;
            graph.updateEachNodeAttributes((id, attr) => Object.assign(attr, { x: attr.x * k, y: attr.y * k }));
            noverlap.assign(graph, { maxIterations: 120, settings: { margin: 3, ratio: 1.05, expansion: 1.05 } });
        }

        const positions = new Map();
        let cx = 0;
        let cy = 0;
        graph.forEachNode((id, a) => {
            positions.set(id, { x: a.x, y: a.y });
            cx += a.x;
            cy += a.y;
        });
        if (graph.order) {
            cx /= graph.order;
            cy /= graph.order;
        }

        // isolates: evenly spaced on concentric rings just outside the map
        if (isolates.length) {
            let radius = 0;
            positions.forEach((p) => { radius = Math.max(radius, Math.hypot(p.x - cx, p.y - cy)); });
            const sizeOf = new Map(nodes.map((n) => [n.id, n.size]));
            const gap = Math.max(...isolates.map((id) => sizeOf.get(id) || 10)) * 1.8;
            radius += gap * 3;
            const ordered = [...isolates].sort((a, b) => a.localeCompare(b));
            let i = 0;
            while (i < ordered.length) {
                const capacity = Math.max(8, Math.floor((2 * Math.PI * radius) / gap));
                // a partly-filled last ring still goes all the way round
                const slots = Math.min(capacity, ordered.length - i);
                for (let s = 0; s < slots && i < ordered.length; s++, i++) {
                    const angle = (2 * Math.PI * s) / slots;
                    positions.set(ordered[i], { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) });
                }
                radius += gap;
            }
        }
        return { positions, regions };
    }

    // TMDB meta tags that say nothing about what a movie is
    const KEYWORD_STOPLIST = new Set([
        'duringcreditsstinger', 'aftercreditsstinger', 'woman director', 'sequel', 'prequel',
        'remake', '3d', 'independent film', 'based on novel or book',
    ]);
    const SMALL_WORDS = new Set(['a', 'an', 'and', 'as', 'at', 'by', 'for', 'in', 'of', 'on', 'or', 'the', 'to', 'with', 'vs']);

    const UPPER_WORDS = new Set(['fbi', 'cia', 'kgb', 'nasa', 'usa', 'uk', 'nyc', 'lgbt', 'ai', 'dc', 'mi6', 'cgi', 'vhs']);

    function titleCase(text) {
        return String(text).split(/\s+/).map((w, i) => {
            const lower = w.toLowerCase();
            // roman numerals ("world war ii") and acronyms stay upper-case
            if (/^(i{1,3}|iv|vi{0,3}|ix|x)$/.test(lower) || UPPER_WORDS.has(lower)) return w.toUpperCase();
            if (i > 0 && SMALL_WORDS.has(lower)) return lower;
            return w.charAt(0).toUpperCase() + w.slice(1);
        }).join(' ');
    }

    /** "Demon Slayer: Kimetsu no Yaiba Collection" -> "Demon Slayer"; "Harry Potter Collection" -> "Harry Potter". */
    function shortCollectionName(name) {
        if (!name) return null;
        const original = String(name).trim();
        let s = original.replace(/[\s\-–—:([]*\bcollection\b[)\]]*\s*$/i, '').trim();
        const colon = s.indexOf(':');
        if (colon >= 3) s = s.slice(0, colon).trim();
        return s || original;
    }

    function joinAnd(parts) {
        return parts.length <= 1 ? parts.join('') : `${parts.slice(0, -1).join(', ')} & ${parts[parts.length - 1]}`;
    }

    const THEME_FEATURES = {
        keyword: (m) => (m.keywords || []).map((k) => String(k).toLowerCase()).filter((k) => !KEYWORD_STOPLIST.has(k)),
        genre: (m) => m.genres || [],
        studio: (m) => (m.studio ? [m.studio] : []),
    };

    /**
     * Name every region from its own movies (first rule that fits):
     *   1. one franchise is >=60% of it              -> "Demon Slayer"
     *   2. 2-3 franchises together are >=50%          -> "Demon Slayer & Attack on Titan"
     *   3. one director is >=50% (3+ movies)          -> "Christopher Nolan films"
     *   4. its most distinctive keyword + genre, scored c-TF-IDF style against the other
     *      regions (share here x log(1 + regions / regions where it's common)), so traits
     *      on everything - Drama - score ~0         -> "Superhero · Action (2010s)"
     *   5. its two most distinctive genres            -> "Crime · Thriller"
     * A dominant decade (>=45%) is appended to theme names, and names are made unique by
     * falling back to the next-best candidate.
     * regions: [{ id, nodes: [movieId] }], all: movie infos (with collection, studio).
     * Returns Map regionId -> name.
     */
    function nameRegions(regions, all) {
        const byId = new Map(all.map((m) => [m.id, m]));
        const R = Math.max(1, regions.length);
        const stats = regions.map((r) => {
            const members = r.nodes.map((id) => byId.get(id)).filter(Boolean);
            const counts = {};
            for (const [kind, extract] of Object.entries(THEME_FEATURES)) counts[kind] = countFeatures(members, extract);
            return { region: r, members, counts };
        });
        // in how many regions is each trait "common" (>=15% of the region)?
        const commonIn = {};
        for (const kind of Object.keys(THEME_FEATURES)) {
            commonIn[kind] = new Map();
            for (const { members, counts } of stats) {
                for (const [key, c] of counts[kind]) {
                    if (members.length && c / members.length >= 0.15) commonIn[kind].set(key, (commonIn[kind].get(key) || 0) + 1);
                }
            }
        }
        const scoreOf = (kind, key, c, n) => (c / n) * Math.log(1 + R / Math.max(1, commonIn[kind].get(key) || 1));
        // "on nearly every region" - usable only as a last resort (e.g. Drama)
        const ubiquitous = (kind, key) => (commonIn[kind].get(key) || 0) / R > 0.6 && R >= 4;

        const candidates = stats.map(({ region, members, counts }) => {
            const n = members.length || 1;
            const names = []; // best first; { text, theme } - only theme names carry an era

            const collections = [...countFeatures(members, (m) => (m.collection ? [m.collection] : [])).entries()]
                .sort((a, b) => b[1] - a[1]);
            if (collections[0] && collections[0][1] / n >= 0.6) {
                names.push({ text: collections[0][0], theme: false });
            } else {
                const multi = collections.filter(([, c]) => c >= 2).slice(0, 3);
                for (let k = 2; k <= multi.length; k++) {
                    if (multi.slice(0, k).reduce((s, [, c]) => s + c, 0) / n >= 0.5) {
                        names.push({ text: joinAnd(multi.slice(0, k).map(([name]) => name)), theme: false });
                        break;
                    }
                }
            }

            const director = topCounts(members, FEATURES.director, 1)[0];
            if (director && director.count >= 3 && director.share >= 0.5) names.push({ text: `${director.key} films`, theme: false });

            const ranked = (kind, minShare, minCount) => [...counts[kind].entries()]
                .filter(([key, c]) => c / n >= minShare && c >= minCount)
                .map(([key, c]) => ({ key, score: scoreOf(kind, key, c, n), ubiq: ubiquitous(kind, key) }))
                .sort((a, b) => b.score - a.score);
            const keywords = ranked('keyword', 0.2, 3);
            const genres = ranked('genre', 0.2, 3);
            const specificGenres = genres.filter((g) => !g.ubiq);
            const bestGenre = (exclude) => (specificGenres.find((g) => !exclude.includes(g.key.toLowerCase())) || {}).key;
            for (const kw of keywords.slice(0, 3)) {
                const genre = bestGenre([kw.key]);
                names.push({ text: genre ? `${titleCase(kw.key)} · ${genre}` : titleCase(kw.key), theme: true });
            }
            // genre pair: prefer genres that aren't on every region; Drama only as a last resort
            const loose = ranked('genre', 0.1, 1);
            const specific = loose.filter((g) => !g.ubiq);
            const pool = (specific.length ? specific.concat(loose.filter((g) => g.ubiq)) : loose).map((g) => g.key);
            if (pool.length) {
                // best pair first, then alternative pairs so near-identical regions can still differ
                [[0, 1], [0, 2], [1, 2], [0, 3]].forEach(([a, b]) => {
                    if (pool[a] && (b === 1 || pool[b])) names.push({ text: [pool[a], pool[b]].filter(Boolean).join(' · '), theme: true });
                });
            }
            if (!names.length) names.push({ text: 'Assorted', theme: true });

            const decade = topCounts(members, FEATURES.decade, 1)[0];
            const era = decade && decade.share >= 0.45 ? decade.key : null;
            return { region, names, era, size: members.length };
        });

        // biggest regions pick first so they get the cleanest names; a taken name falls
        // through to the region's next-best candidate, and only then gets a number
        const out = new Map();
        const used = new Set();
        for (const c of [...candidates].sort((a, b) => b.size - a.size)) {
            const options = c.names.map((nm) => (nm.theme && c.era ? `${nm.text} (${c.era})` : nm.text));
            let name = options.find((o) => !used.has(o));
            if (!name) {
                let k = 2;
                while (used.has(`${options[0]} ${k}`)) k++;
                name = `${options[0]} ${k}`;
            }
            used.add(name);
            out.set(c.region.id, name);
        }
        return out;
    }

    /**
     * Map colouring for regions. Region identity is carried by the names printed on the
     * map, so colour only has to tell *neighbouring* regions apart - like a political map.
     * That is a different job from series identity, which is why the 8 validated slots
     * may repeat here: greedily, biggest region first, each region takes the slot it
     * shares the least link weight with among already-coloured neighbours (ties go to the
     * earliest slot, so the biggest regions get the most distinct colours).
     * keys: region keys, biggest first. adjacency: Map "a|b" (sorted) -> weight.
     */
    function mapColoring(keys, adjacency) {
        const colors = new Map();
        const weightBetween = (a, b) => adjacency.get(a < b ? `${a}|${b}` : `${b}|${a}`) || 0;
        for (const key of keys) {
            let best = 0;
            let bestConflict = Infinity;
            CATEGORICAL.forEach((color, slot) => {
                let conflict = 0;
                colors.forEach((c, other) => {
                    if (c === color) conflict += weightBetween(key, other);
                });
                if (conflict < bestConflict) {
                    bestConflict = conflict;
                    best = slot;
                }
            });
            colors.set(key, CATEGORICAL[best]);
        }
        return colors;
    }

    const api = {
        CATEGORICAL, OTHER, SEQUENTIAL, DIVERGING,
        mean, ratingCenter, colorScale, ratingColor,
        distinctiveTraits, clusterProfile, likedWeights, weightedCentroid,
        yourClusters, tasteDna, distance, buildGroups, nameRegions, shortCollectionName, mapLayout, mapColoring,
    };
    global.NGInsights = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
