/* Cytoscape style + layout configuration for the movie network graph.
   Kept separate from network-graph.js so visual tuning doesn't require touching logic. */

(function (global) {
    const EDGE_COLORS = {
        shared_director: '#ED8936',
        shared_actor: '#FFD700',
        keyword_similarity: '#9F7AEA',
        same_studio: '#48BB78',
        same_collection: '#F56565',
        review: '#4299E1',
        prediction: '#FF6B6B',
        directed_by: '#ED8936',
        acted_in: '#FFD700',
    };

    global.NG_EDGE_COLORS = EDGE_COLORS;

    // unquoted on purpose - cytoscape rejects quoted family names and silently falls back
    const LABEL_FONT = 'Pixelated MS Sans Serif, MS Sans Serif, Tahoma, sans-serif';

    function edgeStyle(type, color) {
        return {
            selector: `edge.${type}`,
            style: {
                'line-color': color,
                'target-arrow-color': color,
                'width': 'mapData(weight, 0, 4, 1, 5)',
                'curve-style': 'haystack',
                'haystack-radius': 0.2,
            },
        };
    }

    function hullStyle(extra) {
        // label rendered as a solid "title tab" sitting on top of the box, Win98-window style
        return Object.assign({
            label: 'data(label)',
            shape: 'rectangle',
            events: 'no',
            'z-compound-depth': 'bottom',
            'z-index': 0,
            ghost: 'no',
            'background-image': 'none',
            'background-opacity': 0.07,
            'border-width': 1.5,
            'border-opacity': 0.8,
            'text-valign': 'top',
            'text-halign': 'center',
            'font-size': 11,
            'min-zoomed-font-size': 5,
            color: '#000',
            'text-outline-width': 0,
            'text-background-opacity': 0.92,
            'text-background-shape': 'rectangle',
            'text-background-padding': 3,
            'text-margin-y': -2,
            'text-max-width': 260,
            'text-wrap': 'ellipsis',
        }, extra);
    }

    /**
     * Build the full stylesheet. Edge opacity depends on graph density and heavy effects
     * (drop shadows) are dropped on big graphs, so the stylesheet is rebuilt per render
     * rather than patched - patching appended a rule *after* the focus/hover rules and
     * silently overrode them.
     */
    global.NG_BUILD_STYLE = function ({ edgeOpacity = 0.55, heavy = true, dots = false } = {}) {
        return [
            {
                selector: 'node',
                style: {
                    label: 'data(label)',
                    'font-size': 9,
                    'font-family': LABEL_FONT,
                    color: '#fff',
                    'text-valign': 'bottom',
                    'text-margin-y': 4,
                    'text-background-color': '#000',
                    'text-background-opacity': 0.62,
                    'text-background-shape': 'rectangle',
                    'text-background-padding': 2,
                    'text-max-width': 110,
                    'text-wrap': 'ellipsis',
                    // hides labels once zoomed out past readability instead of rendering an
                    // unreadable pile of overlapping text on large graphs
                    'min-zoomed-font-size': 7,
                    'transition-property': 'opacity',
                    'transition-duration': 160,
                },
            },
            {
                selector: 'node.movie',
                style: {
                    shape: 'rectangle',
                    // domain floor is lower than the raw size range (18-60) so the JS-side
                    // sizeScale down-weighting for large graphs actually shrinks nodes instead
                    // of clamping at the same minimum
                    width: 'mapData(size, 9, 60, 18, 78)',
                    height: 'mapData(size, 9, 60, 27, 117)',
                    'background-color': '#2b2f55',
                    // silver picture frame + hard offset shadow = Win98 window depth
                    'border-width': 2,
                    'border-color': '#c0c0c0',
                    ghost: heavy ? 'yes' : 'no',
                    'ghost-offset-x': 3,
                    'ghost-offset-y': 3,
                    'ghost-opacity': 0.45,
                },
            },
            ...(dots ? [
                {
                    // "constellation" view: every movie a dot filled by the Colour-by scale
                    selector: 'node.movie',
                    style: {
                        shape: 'ellipse',
                        width: 'mapData(size, 9, 60, 9, 30)',
                        height: 'mapData(size, 9, 60, 9, 30)',
                        'background-color': 'data(vizColor)',
                        'border-width': 1,
                        'border-color': '#0b0d1f',
                        ghost: 'no',
                        'font-size': 8,
                        'text-margin-y': 3,
                    },
                },
                {
                    // no value for the current scale (e.g. movies you haven't rated) recede
                    selector: 'node.movie.ng-novalue',
                    style: { opacity: 0.35 },
                },
            ] : [
                {
                    // only movies with an actual poster URL get a background-image - binding it
                    // directly on node.movie throws "background-image: " is invalid when empty
                    selector: 'node.movie[poster]',
                    style: {
                        'background-image': 'data(poster)',
                        'background-fit': 'cover',
                        'background-clip': 'node',
                        'background-image-crossorigin': 'null',
                    },
                },
                {
                    // poster view shows the Colour-by scale on the picture frame
                    selector: 'node.movie[vizColor]',
                    style: { 'border-color': 'data(vizColor)', 'border-width': 3 },
                },
            ]),
            {
                // the recommender's top picks for you
                selector: 'node.movie.ng-pick',
                style: { 'border-color': '#FFD700', 'border-width': dots ? 2.5 : 4 },
            },
            {
                selector: 'node.movie.ng-watchlist.ng-highlight',
                style: { 'border-style': 'dashed', 'border-color': '#ffffff', 'border-width': dots ? 2 : 3 },
            },
            {
                selector: 'node.director, node.actor',
                style: {
                    shape: 'ellipse',
                    width: 26,
                    height: 26,
                    'background-color': '#ED8936',
                    'border-width': 2,
                    'border-color': '#ED8936',
                },
            },
            {
                selector: 'node.director[profile_picture], node.actor[profile_picture]',
                style: {
                    'background-image': 'data(profile_picture)',
                    'background-fit': 'cover',
                    'background-image-crossorigin': 'null',
                },
            },
            {
                selector: 'node.actor',
                style: { 'background-color': '#FFD700', 'border-color': '#FFD700' },
            },
            {
                // floating island name above each cluster, styled as a Win98 title tab
                selector: 'node.ng-group-label',
                style: {
                    width: 1,
                    height: 1,
                    'background-opacity': 0,
                    'border-width': 0,
                    ghost: 'no',
                    label: 'data(label)',
                    'font-size': 'data(fontSize)',
                    color: '#ffffff',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'text-margin-y': 0,
                    'text-background-color': '#000080',
                    'text-background-opacity': 0.78,
                    'text-background-padding': 4,
                    'text-max-width': 420,
                    'text-wrap': 'none',
                    'min-zoomed-font-size': 3,
                    // clickable: opens that region's profile
                    'z-index': 50,
                },
            },
            {
                // franchise tab inside a region (collection red, like the collection outline);
                // min-zoomed-font-size keeps them hidden until zoomed in (~0.9x), so the
                // zoomed-out map reads by region and the zoomed-in map by franchise
                selector: 'node.ng-collection-label',
                style: {
                    width: 1,
                    height: 1,
                    'background-opacity': 0,
                    'border-width': 0,
                    ghost: 'no',
                    label: 'data(label)',
                    'font-size': 10,
                    color: '#1a0000',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'text-margin-y': 0,
                    'text-background-color': '#F56565',
                    'text-background-opacity': 0.92,
                    'text-background-padding': 2,
                    'text-max-width': 220,
                    'text-wrap': 'ellipsis',
                    'min-zoomed-font-size': 9,
                    'z-index': 45,
                },
            },
            {
                // "you" / other users: placed from the taste map, never part of the physics
                selector: 'node.ng-marker',
                style: {
                    shape: 'ellipse',
                    width: 32,
                    height: 32,
                    'background-color': '#000080',
                    'border-width': 3,
                    'border-color': '#ffffff',
                    color: '#ffffff',
                    'font-size': 10,
                    'text-valign': 'top',
                    'text-margin-y': -5,
                    'text-background-color': '#000080',
                    'text-background-opacity': 1,
                    'text-background-padding': 3,
                    'min-zoomed-font-size': 0,
                    ghost: 'yes',
                    'ghost-offset-x': 3,
                    'ghost-offset-y': 3,
                    'ghost-opacity': 0.5,
                    'z-index': 60,
                },
            },
            {
                selector: 'node.ng-marker[avatar]',
                style: {
                    'background-image': 'data(avatar)',
                    'background-fit': 'cover',
                    'background-image-crossorigin': 'null',
                },
            },
            {
                selector: 'node.ng-marker--other',
                style: {
                    width: 22,
                    height: 22,
                    'border-width': 2,
                    'border-color': '#c0c0c0',
                    'font-size': 9,
                    'text-background-color': '#404040',
                    'min-zoomed-font-size': 6,
                    'z-index': 55,
                },
            },
            {
                // on-demand outline box (community picked in the sidebar, or the selected
                // movie's collection) - a plain node sized to its members, drawn beneath
                // everything and ignoring pointer events
                selector: 'node.ng-hull',
                style: hullStyle({
                    width: 'data(w)',
                    height: 'data(h)',
                    'background-color': 'data(color)',
                    'border-color': 'data(color)',
                    'text-background-color': 'data(color)',
                }),
            },
            {
                selector: 'node.ng-hull--collection',
                style: {
                    'border-style': 'dashed',
                    'background-opacity': 0.1,
                    'font-size': 10,
                },
            },
            edgeStyle('shared_director', EDGE_COLORS.shared_director),
            edgeStyle('shared_actor', EDGE_COLORS.shared_actor),
            edgeStyle('keyword_similarity', EDGE_COLORS.keyword_similarity),
            edgeStyle('same_studio', EDGE_COLORS.same_studio),
            edgeStyle('same_collection', EDGE_COLORS.same_collection),
            edgeStyle('review', EDGE_COLORS.review),
            edgeStyle('prediction', EDGE_COLORS.prediction),
            edgeStyle('directed_by', EDGE_COLORS.directed_by),
            edgeStyle('acted_in', EDGE_COLORS.acted_in),
            {
                selector: 'edge',
                style: { opacity: edgeOpacity },
            },
            ...(dots ? [{
                // Wikipedia-map look: in dots view links take their source movie's colour
                selector: 'edge[ecolor]',
                style: { 'line-color': 'data(ecolor)' },
            }] : []),

            {
                // taste-space neighbours ("liked by the same people"): hidden, they only
                // drive the Taste space layout - shown dotted when their movie is focused
                selector: 'edge.taste',
                style: {
                    display: 'none',
                    'line-color': '#cde2fb',
                    'line-style': 'dotted',
                    'curve-style': 'straight',
                    width: 1.5,
                },
            },
            {
                // in the Taste space layout the taste links *are* the map's structure: drawn faintly
                selector: 'edge.taste.ng-taste-on',
                style: {
                    display: 'element',
                    'line-style': 'solid',
                    'curve-style': 'haystack',
                    width: 1,
                    opacity: edgeOpacity * 0.8,
                },
            },
            {
                // dashed "where to go next" lines from you to your picks, when you're focused
                selector: 'edge.ng-you-link',
                style: {
                    'line-color': '#FFD700',
                    'line-style': 'dashed',
                    'curve-style': 'straight',
                    width: 1.5,
                    opacity: 0.75,
                    events: 'no',
                },
            },

            // ---- interaction states (must stay last so they win over the base rules) ----
            {
                // "Highlight my movies": your rated movies glow in your rating's colour
                selector: 'node.movie.ng-highlight.ng-mine',
                style: {
                    'underlay-color': 'data(mineColor)',
                    'underlay-opacity': 0.9,
                    'underlay-padding': 4,
                },
            },
            {
                // ...and everything you haven't rated or watchlisted steps back
                selector: 'node.movie.ng-muted',
                style: { opacity: 0.28 },
            },
            {
                selector: 'node.ng-hover',
                style: {
                    'underlay-color': '#ffffff',
                    'underlay-opacity': 0.18,
                    'underlay-padding': 5,
                    'z-index': 20,
                },
            },
            {
                // focus mode: everything outside the selection's neighborhood recedes
                selector: 'node.ng-faded',
                style: { opacity: 0.14, 'text-opacity': 0 },
            },
            {
                // weak (non-backbone) links stay hidden until their movie is focused
                selector: 'edge.ng-weak',
                style: { display: 'none' },
            },
            {
                selector: 'edge.ng-weak.ng-hood',
                style: { display: 'element' },
            },
            {
                selector: 'edge.ng-faded',
                style: { opacity: 0.03 },
            },
            {
                selector: 'edge.ng-hood',
                style: {
                    opacity: 0.95,
                    width: 'mapData(weight, 0, 4, 2, 6)',
                    'z-index': 10,
                },
            },
            {
                selector: 'node.ng-hood',
                style: { 'z-index': 15, 'min-zoomed-font-size': 4, opacity: 1 },
            },
            {
                selector: 'edge.taste.ng-hood',
                style: { display: 'element', opacity: 0.85, width: 1.5 },
            },
            {
                selector: 'node:selected',
                style: {
                    'border-color': '#ffffff',
                    'border-width': 3,
                    'underlay-color': '#1084d0',
                    'underlay-opacity': 0.55,
                    'underlay-padding': 6,
                    'z-index': 30,
                },
            },
            {
                selector: 'edge:selected',
                style: { opacity: 1, width: 4 },
            },
        ];
    };

    global.NG_CYTOSCAPE_STYLE = global.NG_BUILD_STYLE();

    global.NG_CYTOSCAPE_LAYOUT = function (nodeCount) {
        // No manually-computed bounding box anymore - trying to pre-guess "enough space" for
        // an arbitrary node count (whether 50 or 5000) either crammed large graphs into too
        // small an area (severe overlap) or left small graphs swimming in empty space.
        // Instead let fcose's own physics (repulsion vs. ideal edge length vs. gravity)
        // settle into whatever area is genuinely needed, then fit() zooms out to match -
        // this is what makes distance scale correctly at any node count. Since labels are
        // rendered outside the node shape, nodeDimensionsIncludeLabels makes the repulsion/
        // overlap-removal pass treat the label text as part of the node's footprint too,
        // which is what was letting titles overlap each other even when posters didn't.
        //
        // The caller only passes nodes + "backbone" edges (each movie's strongest few links),
        // never the full edge set - see markBackbone() in network-graph.js. Communities are
        // expressed purely through edge springs: links inside a community are short and
        // stiff, links between communities long and slack, so clusters form and pull apart
        // on their own without compound boxes constraining the physics.
        //
        // mode 'taste': the caller passes the recommender's taste-neighbour links instead
        // (each movie -> its nearest movies in the model's learned space). Spring length
        // follows similarity, so tightly-shared audiences pack into dense clusters.
        const isPerson = (edge) => edge.data('type') === 'directed_by' || edge.data('type') === 'acted_in';
        const isTaste = (edge) => edge.data('type') === 'taste';
        return {
            name: 'fcose',
            // 'proof' runs a dedicated overlap-removal pass after the physics settle; it gets
            // slow on very large graphs, where 'default' is close enough
            quality: nodeCount <= 600 ? 'proof' : 'default',
            nodeDimensionsIncludeLabels: true,
            animate: nodeCount <= 250,
            randomize: true,
            fit: true,
            padding: 40,
            nodeSeparation: 75,
            // person overlay nodes only have 1-2 edges each, so they'd otherwise get pushed
            // far away by repulsion - pull them in tight with a short, stiff spring instead
            idealEdgeLength: (edge) => {
                if (isPerson(edge)) return 40;
                // similarity is cosine (~0.3-0.95): the closer the audiences, the shorter the spring
                if (isTaste(edge)) return 30 + (1 - (edge.data('sim') || 0.5)) * 160;
                return edge.data('sameCommunity') ? 70 : 220;
            },
            edgeElasticity: (edge) => {
                if (isPerson(edge)) return 0.5;
                if (isTaste(edge)) return 0.35;
                return edge.data('sameCommunity') ? 0.45 : 0.05;
            },
            nodeRepulsion: (node) => (
                node.hasClass('director') || node.hasClass('actor') ? 1200 : 8000
            ),
            // weak gravity: strong gravity is what dragged every cluster into one pile in
            // the middle of the canvas
            gravity: 0.08,
            gravityRange: 3.8,
            // disconnected pieces are packed side by side (needs cytoscape-layout-utilities,
            // loaded in the template) instead of being piled on the same centre
            packComponents: true,
            componentSpacing: 60,
            numIter: Math.min(6000, 2500 + nodeCount * 2),
            // movies with no backbone links at all get tiled in a tidy grid off to the side
            tile: true,
            tilingPaddingVertical: 20,
            tilingPaddingHorizontal: 20,
        };
    };
})(window);
