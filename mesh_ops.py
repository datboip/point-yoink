#!/usr/bin/env python3
"""Pure mesh topology helpers for editor selections."""
from collections import defaultdict, deque

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


def face_adjacency(faces):
    faces = _faces_array(faces)
    edges = _edge_owners(faces)
    adj = [set() for _ in range(len(faces))]
    for owners in edges.values():
        if len(owners) < 2:
            continue
        for i in owners:
            adj[i].update(j for j in owners if j != i)
    return [tuple(sorted(n)) for n in adj]


def _edge_owners(faces):
    edges = defaultdict(list)
    for i, tri in enumerate(faces):
        a, b, c = [int(v) for v in tri]
        for u, v in ((a, b), (b, c), (c, a)):
            if u > v:
                u, v = v, u
            edges[(u, v)].append(i)
    return edges


def grow_selection(faces, vertex_selection, rings=1):
    faces = _faces_array(faces)
    selected = selected_faces_from_vertices(faces, vertex_selection, require_all=True)
    if not selected.any():
        selected = selected_faces_from_vertices(faces, vertex_selection, require_all=False)
    adj = face_adjacency(faces)
    grown = selected.copy()
    frontier = set(np.flatnonzero(selected))
    for _ in range(max(1, int(rings))):
        nxt = set()
        for i in frontier:
            nxt.update(adj[i])
        nxt.difference_update(np.flatnonzero(grown))
        if not nxt:
            break
        grown[list(nxt)] = True
        frontier = nxt
    return vertex_mask_from_faces(len(faces), faces, grown, len(vertex_selection))


def grow_face_selection(faces, face_selection, rings=1):
    faces = _faces_array(faces)
    grown = np.asarray(face_selection, dtype=bool).copy()
    if len(grown) != len(faces) or not grown.any():
        return np.zeros(len(faces), dtype=bool)
    adj = face_adjacency(faces)
    frontier = set(np.flatnonzero(grown))
    for _ in range(max(1, int(rings))):
        nxt = set()
        for i in frontier:
            nxt.update(adj[i])
        nxt.difference_update(np.flatnonzero(grown))
        if not nxt:
            break
        grown[list(nxt)] = True
        frontier = nxt
    return np.ascontiguousarray(grown)


def shrink_selection(faces, vertex_selection, rings=1):
    faces = _faces_array(faces)
    selected = selected_faces_from_vertices(faces, vertex_selection, require_all=True)
    if not selected.any():
        return np.zeros(len(vertex_selection), dtype=bool)
    edge_owners = _edge_owners(faces)
    current_faces = selected.copy()
    current_vertices = np.asarray(vertex_selection, dtype=bool).copy()
    for _ in range(max(1, int(rings))):
        boundary_vertices = set()
        for i in np.flatnonzero(current_faces):
            tri = [int(v) for v in faces[i]]
            edges = ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0]))
            boundary = False
            for u, v in edges:
                if u > v:
                    u, v = v, u
                owners = edge_owners[(u, v)]
                if not any(j != i and current_faces[j] for j in owners):
                    boundary = True
                    break
            if boundary:
                boundary_vertices.update(int(v) for v in faces[i])
        if not boundary_vertices:
            break
        current_vertices[list(boundary_vertices)] = False
        current_faces = selected_faces_from_vertices(faces, current_vertices, require_all=True)
        if not current_faces.any():
            break
    return np.ascontiguousarray(current_vertices)


def shrink_face_selection(faces, face_selection, rings=1):
    faces = _faces_array(faces)
    current = np.asarray(face_selection, dtype=bool).copy()
    if len(current) != len(faces) or not current.any():
        return np.zeros(len(faces), dtype=bool)
    edge_owners = _edge_owners(faces)
    for _ in range(max(1, int(rings))):
        remove = np.zeros(len(faces), dtype=bool)
        for i in np.flatnonzero(current):
            tri = [int(v) for v in faces[i]]
            for u, v in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                if u > v:
                    u, v = v, u
                owners = edge_owners[(u, v)]
                if not any(j != i and current[j] for j in owners):
                    remove[i] = True
                    break
        if not remove.any():
            break
        next_current = current & ~remove
        if not next_current.any():
            break
        current = next_current
    return np.ascontiguousarray(current)


def connected_selection(faces, vertex_selection):
    faces = _faces_array(faces)
    seeds = selected_faces_from_vertices(faces, vertex_selection, require_all=True)
    if not seeds.any():
        seeds = selected_faces_from_vertices(faces, vertex_selection, require_all=False)
    if not seeds.any():
        return np.zeros(len(vertex_selection), dtype=bool)
    adj = face_adjacency(faces)
    out = np.zeros(len(faces), dtype=bool)
    seen = np.zeros(len(faces), dtype=bool)
    seed_set = set(np.flatnonzero(seeds))
    for seed in list(seed_set):
        if seen[seed]:
            continue
        component = []
        q = deque([int(seed)])
        seen[seed] = True
        touches_seed = False
        while q:
            i = q.popleft()
            component.append(i)
            if i in seed_set:
                touches_seed = True
            for j in adj[i]:
                if not seen[j]:
                    seen[j] = True
                    q.append(j)
        if touches_seed:
            out[component] = True
    return vertex_mask_from_faces(len(faces), faces, out, len(vertex_selection))


def connected_face_selection(faces, face_selection):
    faces = _faces_array(faces)
    seeds = np.asarray(face_selection, dtype=bool)
    if len(seeds) != len(faces) or not seeds.any():
        return np.zeros(len(faces), dtype=bool)
    adj = face_adjacency(faces)
    out = np.zeros(len(faces), dtype=bool)
    seen = np.zeros(len(faces), dtype=bool)
    for seed in np.flatnonzero(seeds):
        if seen[seed]:
            continue
        component = []
        q = deque([int(seed)])
        seen[seed] = True
        while q:
            i = q.popleft()
            component.append(i)
            for j in adj[i]:
                if not seen[j]:
                    seen[j] = True
                    q.append(j)
        out[component] = True
    return np.ascontiguousarray(out)


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
