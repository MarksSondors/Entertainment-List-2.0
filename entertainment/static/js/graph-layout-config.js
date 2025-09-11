/**
 * Graph Layout Configuration for Network Visualization
 * 
 * This configuration helps reduce center clustering by implementing:
 * - Strategic repulsion forces
 * - Dynamic edge lengths
 * - Physics-based constraints
 * - Adaptive parameters based on graph size
 */

function getOptimalLayoutConfig(graphData) {
    const nodeCount = graphData.nodes ? graphData.nodes.length : 0;
    const edgeCount = graphData.edges ? graphData.edges.length : 0;
    const density = nodeCount > 1 ? (2 * edgeCount) / (nodeCount * (nodeCount - 1)) : 0;
    
    // Base configuration that works well for most graphs
    const baseConfig = {
        physics: {
            enabled: true,
            stabilization: {
                enabled: true,
                iterations: Math.min(1000, nodeCount * 2),
                updateInterval: 25,
                onlyDynamicEdges: false,
                fit: true
            },
            // Use forceAtlas2Based for better spread and less center clustering
            forceAtlas2Based: {
                theta: 0.5,
                gravitationalConstant: -50,
                centralGravity: 0.005,
                springConstant: 0.18,
                springLength: 100 + (density * 100), // Longer springs for denser graphs
                damping: 0.4,
                avoidOverlap: 0.5
            },
            maxVelocity: 30,
            minVelocity: 0.1,
            solver: 'forceAtlas2Based',
            timestep: 0.35,
            adaptiveTimestep: true
        },
        layout: {
            improvedLayout: true,
            clusterThreshold: 150,
            randomSeed: 42 // For reproducible layouts
        }
    };
    
    // Adjust configuration based on graph size
    if (nodeCount > 500) {
        // Large graphs - use hierarchical layout to prevent clustering
        return {
            ...baseConfig,
            physics: {
                ...baseConfig.physics,
                solver: 'hierarchicalRepulsion',
                hierarchicalRepulsion: {
                    centralGravity: 0.0,
                    springLength: 120,
                    springConstant: 0.01,
                    nodeDistance: 150,
                    damping: 0.09,
                    avoidOverlap: 0.5
                }
            },
            layout: {
                ...baseConfig.layout,
                hierarchical: {
                    enabled: true,
                    levelSeparation: 150,
                    nodeSpacing: 100,
                    treeSpacing: 200,
                    blockShifting: true,
                    edgeMinimization: true,
                    parentCentralization: true,
                    direction: 'UD',
                    sortMethod: 'directed'
                }
            }
        };
    } else if (nodeCount > 200) {
        // Medium graphs - enhanced repulsion
        return {
            ...baseConfig,
            physics: {
                ...baseConfig.physics,
                forceAtlas2Based: {
                    ...baseConfig.physics.forceAtlas2Based,
                    gravitationalConstant: -80,
                    springLength: 130,
                    avoidOverlap: 0.8
                }
            }
        };
    }
    
    // Small graphs - standard configuration with tweaks
    return baseConfig;
}

/**
 * Apply layout enhancements to node and edge data
 */
function enhanceGraphData(graphData) {
    if (!graphData || !graphData.nodes || !graphData.edges) {
        return graphData;
    }
    
    const enhanced = {
        nodes: graphData.nodes.map(node => ({
            ...node,
            // Apply layout properties from backend
            mass: node.mass || Math.max(1, (node.size || 15) / 10),
            physics: {
                repulsion: Math.abs(node.charge) || 30,
                mass: node.mass || 1
            },
            // Set constraints for major hubs
            fixed: node.fixed || false,
            x: node.fx !== undefined ? node.fx : node.x,
            y: node.fy !== undefined ? node.fy : node.y
        })),
        edges: graphData.edges.map(edge => ({
            ...edge,
            // Use backend-calculated edge lengths
            length: edge.length || 80,
            width: Math.max(1, (edge.weight || 1) * 2),
            smooth: {
                enabled: true,
                type: 'dynamic',
                roundness: 0.5
            }
        }))
    };
    
    return enhanced;
}

/**
 * Initialize graph with anti-clustering configuration
 */
function initializeGraph(container, graphData, options = {}) {
    const enhancedData = enhanceGraphData(graphData);
    const layoutConfig = getOptimalLayoutConfig(enhancedData);
    
    // Merge with user options
    const finalOptions = {
        ...layoutConfig,
        ...options,
        physics: {
            ...layoutConfig.physics,
            ...options.physics
        }
    };
    
    // Add common visual options
    finalOptions.nodes = {
        borderWidth: 2,
        shadow: true,
        font: { color: '#343434', size: 14, face: 'arial' },
        ...options.nodes
    };
    
    finalOptions.edges = {
        width: 1,
        shadow: true,
        smooth: true,
        color: { inherit: 'from' },
        ...options.edges
    };
    
    finalOptions.interaction = {
        tooltipDelay: 200,
        hover: true,
        ...options.interaction
    };
    
    return finalOptions;
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        getOptimalLayoutConfig,
        enhanceGraphData,
        initializeGraph
    };
}
