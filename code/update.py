"""The normalized Sackin index of every valid NWB file (HDF5 or Zarr).

The file's own hierarchy is the tree: groups are internal nodes, and every dataset plus every
childless group is a leaf. The Sackin index is the sum of the leaf depths, which measures how
unbalanced that tree is, and it is normalized here against the two extremes for the same number of
leaves.

The walk uses the default `LINKS_SKIPPED` policy, which is `h5py.Group.visititems`: a soft link is
not a child, and an object reachable by two hard links is counted once, at the first path that
reaches it. That is the tree every published index was computed from. Following soft links instead
would move the index for half of the archive's files, because `/acquisition/<series>/imaging_plane`
is routinely a link to the plane's real home under `/general/optophysiology`.

Everything shared with the other caches -- the argument parsing, the logging, the batch cap, the
error logs, the output paths, testing mode, and the S3 layout probe with the HDF5 and Zarr walks
-- comes from `dandi_cache_utils`, which the runtime image carries.
"""

import math

import dandi_cache_utils as dandi_cache


def normalized_sackin_index(leaf_depths: list[int], /) -> float:
    """Normalized Sackin index of a tree, given the depth of each of its leaves.

    The Sackin index S is the sum of the leaf depths. It is scaled to roughly [0, 1] with the
    min-max convention stated in the README:

        S_norm = (S - S_min) / (S_max - S_min)

    where S_max = n(n + 1)/2 - 1 is the caterpillar (most imbalanced) tree and S_min =
    n * ceil(log2(n)) approximates a balanced one, for n leaves. A tree with a single leaf, or
    none, has no spread of imbalance to measure, so it normalizes to 0.0.
    """
    number_of_leaves = len(leaf_depths)
    if number_of_leaves <= 1:
        return 0.0
    maximum = number_of_leaves * (number_of_leaves + 1) / 2 - 1
    minimum = number_of_leaves * math.ceil(math.log2(number_of_leaves))
    if maximum <= minimum:
        return 0.0
    return (sum(leaf_depths) - minimum) / (maximum - minimum)


def compute_sackin_index(content_id, item) -> float:
    """Walk one asset, resolved straight from its content ID, and score its tree's imbalance."""
    item.stage = "reading the NWB file"
    structure = dandi_cache.nwb.walk_structure(content_id)
    return normalized_sackin_index(structure.leaf_depths)


def main() -> None:
    dataset, arguments = dandi_cache.open_dataset()

    # Only the assets the upstream cache marked valid are scored.
    validity = dataset.read_input()
    valid_content_ids = [content_id for content_id, is_valid in validity.items() if is_valid is True]

    dandi_cache.run_incremental_update(
        dataset,
        candidates=valid_content_ids,
        process=compute_sackin_index,
        limit=dandi_cache.effective_limit(testing=dataset.testing, limit=arguments.limit),
        # These files were already opened successfully upstream, so a failure here is almost always
        # transient. Leave the item for a later run rather than recording a wrong index.
        on_failure=dandi_cache.SKIP,
        stages={"reading the NWB file": "file_read_errors.txt"},
        describe=lambda index: f"Sackin index {index:.4f}",
        checkpoint_every=50,
    )


if __name__ == "__main__":
    main()
