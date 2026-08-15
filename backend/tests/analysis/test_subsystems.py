from app.analysis.subsystems import (
    MAX_SUBSYSTEMS,
    MIN_SUBSYSTEM_FILES,
    ROOT_KEY,
    UNREACHABLE,
    partition_subsystems,
)


def _graph(file_paths, edges=()):
    return {
        "nodes": [{"id": p, "kind": "file", "language": "python"} for p in file_paths],
        "edges": [{"source": s, "target": t, "kind": "imports"} for s, t in edges],
    }


def _keys(partitions):
    return [p.key for p in partitions]


def _by_key(partitions):
    return {p.key: p for p in partitions}


def test_empty_graph_produces_no_subsystems():
    assert partition_subsystems({"nodes": [], "edges": []}, []) == []


def test_files_group_by_directory():
    paths = [f"app/api/f{i}.py" for i in range(3)] + [f"app/db/f{i}.py" for i in range(3)]
    partitions = partition_subsystems(_graph(paths), [])

    assert set(_keys(partitions)) == {"app/api", "app/db"}
    assert _by_key(partitions)["app/api"].file_paths == tuple(sorted(f"app/api/f{i}.py" for i in range(3)))


def test_every_file_belongs_to_exactly_one_subsystem():
    # Overlapping membership would make "which section does this deep dive live
    # under" ambiguous — the partition must stay a partition.
    paths = [f"app/api/f{i}.py" for i in range(4)] + [f"app/db/f{i}.py" for i in range(4)] + ["setup.py"]
    partitions = partition_subsystems(_graph(paths), [])

    assigned = [p for part in partitions for p in part.file_paths]
    assert sorted(assigned) == sorted(paths)
    assert len(assigned) == len(set(assigned))


def test_thin_directory_merges_into_its_parent():
    # A directory holding fewer than MIN_SUBSYSTEM_FILES files is a folder, not
    # a subsystem — otherwise a repo with one file per directory produces one
    # "subsystem" per file, which is the per-file framing this replaces.
    paths = [f"app/core/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)] + ["app/core/tiny/only.py"]
    partitions = partition_subsystems(_graph(paths), [])

    assert _keys(partitions) == ["app/core"]
    assert "app/core/tiny/only.py" in _by_key(partitions)["app/core"].file_paths


def test_chain_of_thin_directories_collapses_all_the_way_up():
    paths = [f"app/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)] + ["app/a/b/c/deep.py"]
    partitions = partition_subsystems(_graph(paths), [])

    assert _keys(partitions) == ["app"]
    assert "app/a/b/c/deep.py" in _by_key(partitions)["app"].file_paths


def test_root_level_files_get_the_root_key():
    paths = ["setup.py", "conftest.py"] + [f"app/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
    partitions = partition_subsystems(_graph(paths), [])

    assert ROOT_KEY in _keys(partitions)
    assert set(_by_key(partitions)[ROOT_KEY].file_paths) == {"setup.py", "conftest.py"}


def test_subsystem_count_is_capped():
    paths = [f"pkg_{i:02d}/f{j}.py" for i in range(MAX_SUBSYSTEMS + 8) for j in range(MIN_SUBSYSTEM_FILES)]
    partitions = partition_subsystems(_graph(paths), [])

    assert len(partitions) <= MAX_SUBSYSTEMS
    assigned = [p for part in partitions for p in part.file_paths]
    assert sorted(assigned) == sorted(paths)  # capping merges, never drops


def test_cap_terminates_when_everything_is_already_at_the_root():
    # Root groups can't merge any further; the cap loop has to stop rather than
    # spin looking for a parent that doesn't exist.
    paths = [f"f{i}.py" for i in range(5)]
    partitions = partition_subsystems(_graph(paths), [])

    assert _keys(partitions) == [ROOT_KEY]


def test_depth_counts_edges_from_the_nearest_entry_point():
    paths = (
        [f"app/entry/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
        + [f"app/mid/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
        + [f"app/leaf/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
    )
    edges = [("app/entry/f0.py", "app/mid/f0.py"), ("app/mid/f0.py", "app/leaf/f0.py")]
    entry_points = [{"file": "app/entry/f0.py", "kind": "http", "reason": "x"}]

    partitions = _by_key(partition_subsystems(_graph(paths, edges), entry_points))

    assert partitions["app/entry"].depth == 0
    assert partitions["app/mid"].depth == 1
    assert partitions["app/leaf"].depth == 2


def test_subsystems_are_ordered_outside_in():
    paths = [f"app/entry/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)] + [
        f"app/leaf/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)
    ]
    edges = [("app/entry/f0.py", "app/leaf/f0.py")]
    entry_points = [{"file": "app/entry/f0.py", "kind": "http", "reason": "x"}]

    # "leaf" sorts before "entry" alphabetically, so ordering by depth is doing
    # real work here rather than coinciding with the name order.
    assert _keys(partition_subsystems(_graph(paths, edges), entry_points)) == ["app/entry", "app/leaf"]


def test_subsystem_depth_is_its_closest_file():
    # One entry-point file makes the whole group part of the system's edge.
    paths = [f"app/mixed/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
    entry_points = [{"file": "app/mixed/f1.py", "kind": "cli", "reason": "x"}]

    partitions = partition_subsystems(_graph(paths), entry_points)

    assert partitions[0].depth == 0


def test_unreachable_subsystems_sort_last_and_read_outside_in():
    # A repo whose imports don't resolve to file nodes (a monorepo indexed from
    # its root) leaves everything unreachable — the order must still be useful,
    # shallowest directory first rather than pure alphabetical.
    paths = (
        [f"z_top/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
        + [f"a_top/nested/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
        + [f"a_top/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
    )
    partitions = partition_subsystems(_graph(paths), [])

    assert all(p.depth == UNREACHABLE for p in partitions)
    assert _keys(partitions) == ["a_top", "z_top", "a_top/nested"]


def test_partition_is_stable_across_node_ordering():
    # Phase 7 diffs these across snapshots; an unstable partition would show up
    # as architectural change that never happened.
    paths = [f"app/api/f{i}.py" for i in range(4)] + [f"app/db/f{i}.py" for i in range(4)]
    forward = partition_subsystems(_graph(paths), [])
    reversed_order = partition_subsystems(_graph(list(reversed(paths))), [])

    assert [(p.key, p.file_paths, p.depth) for p in forward] == [
        (p.key, p.file_paths, p.depth) for p in reversed_order
    ]


def test_external_nodes_are_not_partitioned():
    graph = _graph([f"app/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)])
    graph["nodes"].append({"id": "external:redis", "kind": "external", "language": None})

    partitions = partition_subsystems(graph, [])

    assigned = [p for part in partitions for p in part.file_paths]
    assert "external:redis" not in assigned


def test_entry_point_for_a_non_graph_file_is_ignored():
    # package.json/Dockerfile entry points are never graph nodes (only parsed
    # source files are), so they can't seed the traversal.
    paths = [f"app/f{i}.py" for i in range(MIN_SUBSYSTEM_FILES)]
    entry_points = [{"file": "Dockerfile", "kind": "http", "reason": "x"}]

    partitions = partition_subsystems(_graph(paths), entry_points)

    assert partitions[0].depth == UNREACHABLE
