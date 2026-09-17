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

    function edgeStyle(type, color) {
        return {
            selector: `edge.${type}`,
            style: {
                'line-color': color,
                'target-arrow-color': color,
                'width': 'mapData(weight, 0, 4, 1, 5)',
                'opacity': 0.55,
                'curve-style': 'haystack',
                'haystack-radius': 0.2,
            },
        };
    }

    global.NG_CYTOSCAPE_STYLE = [
        {
            selector: 'node',
            style: {
                label: 'data(label)',
                'font-size': 9,
                'font-family': 'MS Sans Serif, sans-serif',
                color: '#fff',
                'text-outline-width': 2,
                'text-outline-color': '#000',
                'text-valign': 'bottom',
                'text-margin-y': 4,
                // hides labels once zoomed out past readability instead of rendering an
                // unreadable pile of overlapping text on large graphs
                'min-zoomed-font-size': 7,
            },
        },
        {
            selector: 'node.movie',
            style: {
                shape: 'round-rectangle',
                // domain floor is lower than the raw size range (18-60) so the JS-side
                // sizeScale down-weighting for large graphs actually shrinks nodes instead
                // of clamping at the same minimum
                width: 'mapData(size, 9, 60, 18, 78)',
                height: 'mapData(size, 9, 60, 27, 117)',
                'background-color': '#F56565',
                'border-width': 2,
                'border-color': '#1a1a1a',
            },
        },
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
            selector: 'node.movie[?predicted_score]',
            style: {
                'border-color': '#FFD700',
                'border-width': 4,
            },
        },
        {
            selector: 'node.director, node.actor',
            style: {
                shape: 'ellipse',
                width: 26,
                height: 26,
                'background-color': '#ED8936',
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
            style: { 'background-color': '#FFD700' },
        },
        {
            selector: 'node.user',
            style: {
                shape: 'ellipse',
                width: 24,
                height: 24,
                'background-color': '#4299E1',
            },
        },
        {
            selector: 'node.collection-parent',
            style: {
                shape: 'round-rectangle',
                'background-color': '#F56565',
                'background-opacity': 0.18,
                'border-width': 1.5,
                'border-style': 'dashed',
                'border-opacity': 0.7,
                'border-color': '#F56565',
                'text-valign': 'top',
                'text-halign': 'center',
                'font-size': 11,
                'font-weight': 'bold',
                'min-zoomed-font-size': 6,
                color: '#ffd7d7',
                'text-outline-width': 2,
                'text-outline-color': '#000',
                padding: 18,
            },
        },
        {
            selector: 'node.community-parent',
            style: {
                shape: 'round-rectangle',
                'background-color': '#1084d0',
                'background-opacity': 0.1,
                'border-width': 1.5,
                'border-style': 'dotted',
                'border-opacity': 0.7,
                'border-color': '#1084d0',
                'text-valign': 'top',
                'text-halign': 'center',
                'font-size': 12,
                'font-weight': 'bold',
                'min-zoomed-font-size': 6,
                color: '#cfe8ff',
                'text-outline-width': 2,
                'text-outline-color': '#000',
                padding: 30,
            },
        },
        {
            selector: 'node:selected',
            style: {
                'border-color': '#fff',
                'border-width': 4,
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
            selector: 'edge:selected',
            style: { opacity: 1, width: 4 },
        },
    ];

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
        return {
            name: 'fcose',
            // 'proof' runs a dedicated overlap-removal pass after the physics settle - the
            // key piece for "genuine distance...realistically at any amount of films"
            quality: 'proof',
            nodeDimensionsIncludeLabels: true,
            animate: nodeCount <= 250,
            randomize: true,
            fit: true,
            padding: 40,
            nodeSeparation: 90,
            // person overlay nodes only have 1-2 weak edges each, so they'd otherwise get
            // pushed far away by repulsion from the (much better connected) movie cluster -
            // pull them in tight with a short ideal length + high elasticity instead
            idealEdgeLength: (edge) => (
                edge.data('type') === 'directed_by' || edge.data('type') === 'acted_in' ? 35 : 80
            ),
            edgeElasticity: (edge) => (
                edge.data('type') === 'directed_by' || edge.data('type') === 'acted_in' ? 0.5 : 0.15
            ),
            nodeRepulsion: (node) => (
                node.hasClass('director') || node.hasClass('actor') ? 900 : 4500
            ),
            nestingFactor: 0.1,
            // lower than before - high gravity was pulling every community/collection
            // toward one shared center, which is what read as "clustering in the middle"
            gravity: 0.25,
            gravityRange: 3.8,
            componentSpacing: 100,
            numIter: Math.min(6000, 2500 + nodeCount * 2),
            tile: true,
            tilingPaddingVertical: 20,
            tilingPaddingHorizontal: 20,
        };
    };
})(window);
