"""Tests for collection-first community detection in the movie network graph."""
import os
import subprocess
import sys
from pathlib import Path

import networkx as nx
from django.test import SimpleTestCase

from movies.services.network_graph.algorithms.community import (
    detect_communities_leiden,
    leiden_communities,
)


def _graph_fixture():
    """Two dense actor/movie clusters joined by one weak edge, plus a 3-film collection
    whose films are wired to *different* clusters."""
    nodes, edges = [], []
    for cluster in ('a', 'b'):
        movies = [f'{cluster}_m{i}' for i in range(6)]
        people = [f'{cluster}_p{i}' for i in range(4)]
        nodes += [{'id': m, 'type': 'movie', 'genres': ['Drama']} for m in movies]
        nodes += [{'id': p, 'type': 'person'} for p in people]
        for m in movies:
            for p in people:
                edges.append({'source': m, 'target': p, 'weight': 1.0})
    edges.append({'source': 'a_m0', 'target': 'b_p0', 'weight': 0.1})
    for i, anchor in enumerate(('a_p1', 'b_p1', 'b_p2')):
        nodes.append({'id': f'coll_m{i}', 'type': 'movie', 'collection_id': 77, 'collection_name': 'Saga'})
        edges.append({'source': f'coll_m{i}', 'target': anchor, 'weight': 2.0})
    return nodes, edges


class LeidenCommunitiesTests(SimpleTestCase):
    def test_two_cliques_split_into_two_communities(self):
        G = nx.Graph()
        G.add_edges_from((u, v, {'weight': 1.0}) for u in range(6) for v in range(u + 1, 6))
        G.add_edges_from((u, v, {'weight': 1.0}) for u in range(6, 12) for v in range(u + 1, 12))
        G.add_edge(0, 6, weight=0.1)

        communities = leiden_communities(G, random_state=42)

        self.assertEqual(sorted(sorted(c) for c in communities), [list(range(6)), list(range(6, 12))])


class DetectCommunitiesLeidenTests(SimpleTestCase):
    def test_partition_covers_every_node_once_and_keeps_collections_together(self):
        nodes, edges = _graph_fixture()
        result = detect_communities_leiden(nodes, edges, random_state=42)

        members = [n for c in result['communities'].values() for n in c['nodes']]
        self.assertEqual(len(members), len(set(members)))
        self.assertGreater(result['modularity'], 0.3)

        collection_homes = {
            cid for cid, c in result['communities'].items()
            for n in c['nodes'] if n.startswith('coll_m')
        }
        self.assertEqual(len(collection_homes), 1)
        name = result['communities'][collection_homes.pop()]['name']
        self.assertIn('Saga', name)

    def test_result_is_identical_across_hash_seeds(self):
        """String-id sets iterate differently per process; output must not depend on that."""
        script = (
            'import django, json; django.setup();'
            'from movies.tests.test_community_detection import _graph_fixture;'
            'from movies.services.network_graph.algorithms.community import detect_communities_leiden;'
            'r = detect_communities_leiden(*_graph_fixture(), random_state=42);'
            'print(json.dumps(sorted((sorted(c["nodes"]), c["name"]) for c in r["communities"].values())))'
        )
        project_root = Path(__file__).resolve().parents[2]
        outputs = set()
        for seed in ('1', '2', '3'):
            env = {**os.environ, 'PYTHONHASHSEED': seed,
                   'DJANGO_SETTINGS_MODULE': os.environ.get('DJANGO_SETTINGS_MODULE', 'entertainment.settings')}
            proc = subprocess.run([sys.executable, '-c', script], cwd=project_root, env=env,
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outputs.add(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(len(outputs), 1)
