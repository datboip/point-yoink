#!/usr/bin/env python3
"""Pure mesh topology helpers for editor selections."""
import numpy as np


def _faces_array(faces):
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        return faces.reshape(0, 3)
    return faces.reshape(-1, 3)


def selected_faces_from_vertices(faces, vertex_selection, require_all=True):
    faces = _faces_array(faces)
    sel = np.asarray(vertex_selection, dtype=bool)
    if len(faces) == 0 or len(sel) == 0:
        return np.zeros(len(faces), dtype=bool)
    covered = sel[faces]
    return covered.all(axis=1) if require_all else covered.any(axis=1)


def vertex_mask_from_faces(face_count, faces, face_mask, vertex_count):
    faces = _faces_array(faces)
    out = np.zeros(int(vertex_count), dtype=bool)
    mask = np.asarray(face_mask, dtype=bool)
    if int(face_count) and mask.any():
        out[np.unique(faces[mask])] = True
    return out


class FaceGraph:
    """Face-to-face adjacency of a triangle mesh as flat numpy arrays: `pairs` (k, 2) lists every pair of
    faces that share an edge, once per edge (a non-manifold edge with n owners gives all n*(n-1)/2 pairs).
    Built once per mesh in vectorised numpy (a million faces in well under a second) and cached by the
    caller, instead of a dict of edges rebuilt in Python on every click."""
    __slots__ = ("n", "pairs", "_key")
    def __init__(self, faces):
        faces = _faces_array(faces); self.n = len(faces)
        if self.n == 0:
            self.pairs = np.zeros((0, 2), dtype=np.int64); return
        e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])   # 3 edges per face
        e.sort(axis=1)
        owner = np.tile(np.arange(self.n, dtype=np.int64), 3)
        key = e[:, 0] * (faces.max() + 1) + e[:, 1]
        order = np.argsort(key, kind="stable"); key = key[order]; owner = owner[order]
        same = key[1:] == key[:-1]                       # consecutive rows with the same edge
        run_start = np.concatenate([[True], ~same]); starts = np.flatnonzero(run_start)
        lengths = np.diff(np.append(starts, len(key)))
        pairs = [np.stack([owner[:-1][same], owner[1:][same]], axis=1)]   # every edge with 2+ owners: consecutive pairs
        for L in np.unique(lengths[lengths > 2]):       # a non-manifold edge (3+ owners): add the non-consecutive pairs too
            i, j = np.triu_indices(int(L), 2)            # offsets at least 2 apart; the consecutive ones are already in
            for st in starts[lengths == L]:
                o = owner[st:st + int(L)]; pairs.append(np.stack([o[i], o[j]], axis=1))
        pairs = np.concatenate(pairs); pairs = pairs[pairs[:, 0] != pairs[:, 1]]
        self.pairs = np.unique(np.sort(pairs, axis=1), axis=0)
    @classmethod
    def from_pairs(cls, n, pairs):
        g = cls.__new__(cls); g.n = int(n)
        g.pairs = np.ascontiguousarray(np.asarray(pairs, dtype=np.int64).reshape(-1, 2)); return g
    def subgraph(self, keep):
        """The graph after dropping the faces where keep is False (the faces left keep their order), derived
        from this one in one numpy pass instead of rebuilding it from the faces."""
        keep = np.asarray(keep, dtype=bool)
        remap = np.cumsum(keep) - 1
        a, b = self.pairs[:, 0], self.pairs[:, 1]
        both = keep[a] & keep[b]
        return FaceGraph.from_pairs(int(keep.sum()), np.stack([remap[a[both]], remap[b[both]]], axis=1))
    def component_of(self, seed, allowed):
        """Faces reachable from `seed` through faces in `allowed` (bool per face), as a bool mask. One
        connected-components pass over the allowed subgraph (scipy), so a big region costs the same as a
        small one; without scipy it falls back to a ring-by-ring flood fill."""
        allowed = np.asarray(allowed, dtype=bool).copy(); seed = int(seed); allowed[seed] = True
        a, b = self.pairs[:, 0], self.pairs[:, 1]
        try:
            from scipy.sparse import coo_matrix
            from scipy.sparse.csgraph import connected_components
        except Exception:
            out = np.zeros(self.n, dtype=bool); out[seed] = True
            while True:
                nxt = self.neighbours_of(out) & allowed & ~out
                if not nxt.any(): return out
                out |= nxt
        keep = allowed[a] & allowed[b]
        m = coo_matrix((np.ones(int(keep.sum()), dtype=np.int8), (a[keep], b[keep])), shape=(self.n, self.n))
        _, lab = connected_components(m, directed=False)
        return np.ascontiguousarray((lab == lab[seed]) & allowed)
    def neighbours_of(self, mask):
        """Faces sharing an edge with any face in `mask` (bool per face), as a bool mask."""
        a, b = self.pairs[:, 0], self.pairs[:, 1]
        out = np.zeros(self.n, dtype=bool)
        out[b[mask[a]]] = True; out[a[mask[b]]] = True
        return out
    def interior(self, mask):
        """Faces in `mask` whose every edge is shared with another face in `mask` (bool per face)."""
        # count, per face, the edges it shares with a selected face; a face with 3 such edges is interior
        a, b = self.pairs[:, 0], self.pairs[:, 1]
        both = mask[a] & mask[b]
        cnt = np.bincount(np.concatenate([a[both], b[both]]), minlength=self.n)
        return mask & (cnt >= 3)
    def components(self):
        """Connected-component label per face (union-find over the pair list, vectorised)."""
        parent = np.arange(self.n, dtype=np.int64)
        a, b = self.pairs[:, 0].copy(), self.pairs[:, 1].copy()
        while True:
            ra = _find(parent, a); rb = _find(parent, b)
            lo = np.minimum(ra, rb); hi = np.maximum(ra, rb)
            diff = lo != hi
            if not diff.any(): break
            parent[hi[diff]] = lo[diff]
            # compress
            while True:
                gp = parent[parent]
                if np.array_equal(gp, parent): break
                parent = gp
        return _find(parent, np.arange(self.n))


def _find(parent, idx):
    r = parent[idx]
    while True:
        rr = parent[r]
        if np.array_equal(rr, r): return r
        r = rr


def face_adjacency(faces):
    """Back-compatible: tuple of neighbours per face. Prefer FaceGraph for anything bigger than a test mesh."""
    g = FaceGraph(faces); adj = [[] for _ in range(g.n)]
    for a, b in g.pairs.tolist(): adj[a].append(b); adj[b].append(a)
    return [tuple(sorted(n)) for n in adj]


def _graph(faces, graph):
    return graph if graph is not None else FaceGraph(faces)


def grow_selection(faces, vertex_selection, rings=1, graph=None):
    faces = _faces_array(faces)
    selected = selected_faces_from_vertices(faces, vertex_selection, require_all=True)
    if not selected.any():
        selected = selected_faces_from_vertices(faces, vertex_selection, require_all=False)
    grown = grow_face_selection(faces, selected, rings, graph)
    return vertex_mask_from_faces(len(faces), faces, grown, len(vertex_selection))


def grow_face_selection(faces, face_selection, rings=1, graph=None):
    faces = _faces_array(faces)
    grown = np.asarray(face_selection, dtype=bool).copy()
    if len(grown) != len(faces) or not grown.any():
        return np.zeros(len(faces), dtype=bool)
    g = _graph(faces, graph)
    for _ in range(max(1, int(rings))):
        nxt = g.neighbours_of(grown) & ~grown
        if not nxt.any(): break
        grown |= nxt
    return np.ascontiguousarray(grown)


def shrink_selection(faces, vertex_selection, rings=1, graph=None):
    faces = _faces_array(faces)
    selected = selected_faces_from_vertices(faces, vertex_selection, require_all=True)
    if not selected.any():
        return np.zeros(len(vertex_selection), dtype=bool)
    g = _graph(faces, graph)
    current_faces = selected.copy()
    current_vertices = np.asarray(vertex_selection, dtype=bool).copy()
    for _ in range(max(1, int(rings))):
        boundary = current_faces & ~g.interior(current_faces)
        if not boundary.any(): break
        current_vertices[np.unique(faces[boundary])] = False
        current_faces = selected_faces_from_vertices(faces, current_vertices, require_all=True)
        if not current_faces.any(): break
    return np.ascontiguousarray(current_vertices)


def shrink_face_selection(faces, face_selection, rings=1, graph=None):
    faces = _faces_array(faces)
    current = np.asarray(face_selection, dtype=bool).copy()
    if len(current) != len(faces) or not current.any():
        return np.zeros(len(faces), dtype=bool)
    g = _graph(faces, graph)
    for _ in range(max(1, int(rings))):
        nxt = g.interior(current)
        if nxt.sum() == current.sum() or not nxt.any(): break
        current = nxt
    return np.ascontiguousarray(current)


def connected_selection(faces, vertex_selection, graph=None):
    faces = _faces_array(faces)
    seeds = selected_faces_from_vertices(faces, vertex_selection, require_all=True)
    if not seeds.any():
        seeds = selected_faces_from_vertices(faces, vertex_selection, require_all=False)
    if not seeds.any():
        return np.zeros(len(vertex_selection), dtype=bool)
    out = connected_face_selection(faces, seeds, graph)
    return vertex_mask_from_faces(len(faces), faces, out, len(vertex_selection))


def connected_face_selection(faces, face_selection, graph=None):
    faces = _faces_array(faces)
    seeds = np.asarray(face_selection, dtype=bool)
    if len(seeds) != len(faces) or not seeds.any():
        return np.zeros(len(faces), dtype=bool)
    labels = _graph(faces, graph).components()
    return np.ascontiguousarray(np.isin(labels, np.unique(labels[seeds])))


def points_in_polygon(points, polygon):
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    poly = np.asarray(polygon, dtype=float).reshape(-1, 2)
    x = pts[:, 0]
    y = pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    if len(poly) < 3:
        return inside
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi)
        inside ^= cond
        j = i
    return inside


def face_center_selection(projected_centers, polygon, visible=None):
    hit = points_in_polygon(projected_centers, polygon)
    if visible is not None:
        hit &= np.asarray(visible, dtype=bool)
    return np.ascontiguousarray(hit)


def face_radius_selection(projected_centers, x, y, radius, visible=None):
    pts = np.asarray(projected_centers, dtype=float).reshape(-1, 2)
    hit = ((pts[:, 0] - float(x)) ** 2 + (pts[:, 1] - float(y)) ** 2 <= float(radius) ** 2)
    if visible is not None:
        hit &= np.asarray(visible, dtype=bool)
    return np.ascontiguousarray(hit)
