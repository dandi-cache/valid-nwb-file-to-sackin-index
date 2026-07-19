import argparse
import itertools
import json
import math
import pathlib

import h5py
import remfile
import s3fs
import zarr

# Testing mode processes only this many items and writes to its own designated file
# (`derivatives/testing.jsonl`), leaving the real cache untouched.
_TESTING_LIMIT = 10
_CACHE_FILE_NAME = "valid_nwb_file_to_sackin_index.jsonl"
_TESTING_FILE_NAME = "testing.jsonl"

# The input is the `content-id-to-valid-nwb-file` cache, registered as an input subdataset.
_INPUT_FILE_PATH = (
    pathlib.Path("sourcedata") / "content-id-to-valid-nwb-file" / "derivatives" / "content_id_to_valid_nwb_file.jsonl"
)

# The public DANDI archive S3 bucket. Every asset is content-addressed, so each valid NWB
# file is reachable directly from its content ID without consulting the DANDI API:
#   - HDF5 assets are stored as a single blob at `blobs/<c[:3]>/<c[3:6]>/<content_id>`.
#   - Zarr assets are stored as a directory store under `zarr/<content_id>/`.
# The content ID alone does not say which layout an entry uses, so the blob key is probed
# first and the entry is treated as Zarr when no such blob exists.
_BUCKET = "dandiarchive"
_BLOB_URL_TEMPLATE = "https://dandiarchive.s3.amazonaws.com/blobs/{prefix}/{infix}/{content_id}"


def _load_content_id_to_validity(file_path: pathlib.Path) -> dict:
    """Load the `{content_id: bool}` mapping from the input JSONL, or an empty dict if missing."""
    records: dict = {}
    if not file_path.exists():
        return records
    with file_path.open(mode="r") as file_stream:
        for line in file_stream:
            if line.strip():
                records.update(json.loads(line))
    return records


def _load_previous_cache(file_path: pathlib.Path) -> dict:
    """Load the previously computed `{content_id: normalized_sackin_index}` mapping (empty on bootstrap)."""
    records: dict = {}
    if not file_path.exists():
        return records
    with file_path.open(mode="r") as file_stream:
        for line in file_stream:
            if line.strip():
                records.update(json.loads(line))
    return records


def _write_cache(file_path: pathlib.Path, records: dict) -> None:
    """Write the `{content_id: normalized_sackin_index}` mapping, one sorted content ID per line."""
    with file_path.open(mode="w") as file_stream:
        file_stream.writelines(f"{json.dumps({content_id: records[content_id]})}\n" for content_id in sorted(records))


def _normalized_sackin_index(leaf_depths: list[int]) -> float:
    """
    Normalized Sackin index of a tree, given the depth of each of its leaves.

    The Sackin index S = sum of leaf depths measures tree imbalance. It is scaled to roughly
    [0, 1] with the min-max convention stated in the README:

        S_norm = (S - S_min) / (S_max - S_min)

    where S_max = n(n + 1)/2 - 1 is the caterpillar (most imbalanced) tree and
    S_min = n * ceil(log2(n)) approximates a balanced tree, for n leaves. A tree with a
    single leaf (or none) has no spread of imbalance to measure, so it normalizes to 0.0.
    """
    n = len(leaf_depths)
    if n <= 1:
        return 0.0
    sackin = sum(leaf_depths)
    sackin_max = n * (n + 1) / 2 - 1
    sackin_min = n * math.ceil(math.log2(n))
    if sackin_max <= sackin_min:
        return 0.0
    return (sackin - sackin_min) / (sackin_max - sackin_min)


def _hdf5_leaf_depths(content_id: str) -> list[int]:
    """
    Stream an HDF5 asset and collect the depth of every leaf in its object hierarchy.

    The file's own group/dataset structure is the tree: groups are internal nodes and any
    node without children is a leaf (every dataset, plus any empty group). A node's depth is
    its number of ancestors, i.e. the number of components in its path from the root.
    """
    blob_url = _BLOB_URL_TEMPLATE.format(prefix=content_id[:3], infix=content_id[3:6], content_id=content_id)
    rem_file = remfile.File(url=blob_url)
    leaf_depths: list[int] = []
    with h5py.File(name=rem_file, mode="r") as h5py_file:

        def _visit(name: str, obj: object) -> None:
            # `name` is the path relative to the root (e.g. "acquisition/data"), so its number
            # of components is exactly the node's depth (number of ancestors, root included).
            depth = name.count("/") + 1
            if isinstance(obj, h5py.Dataset) or (isinstance(obj, h5py.Group) and len(obj) == 0):
                leaf_depths.append(depth)

        h5py_file.visititems(_visit)
    return leaf_depths


def _zarr_leaf_depths(s3_filesystem: s3fs.S3FileSystem, content_id: str) -> list[int]:
    """
    Stream a Zarr asset's metadata and collect the depth of every leaf in its object hierarchy.

    The Zarr analogue of the HDF5 walk: groups are internal nodes; arrays (and any empty
    group) are leaves. A node's depth is its number of ancestors below the root group.
    """
    store = s3fs.S3Map(root=f"{_BUCKET}/zarr/{content_id}", s3=s3_filesystem, check=False)
    # DANDI writes consolidated metadata (`.zmetadata`) for every Zarr asset, so the whole
    # hierarchy loads in a single request and the walk below never touches the network again.
    # Fall back to the plain store for the rare asset that lacks it.
    try:
        root_group = zarr.open_consolidated(store=store, mode="r")
    except KeyError:
        root_group = zarr.open_group(store=store, mode="r")

    leaf_depths: list[int] = []

    def _walk(group: zarr.hierarchy.Group, depth: int) -> None:
        arrays = list(group.arrays())
        subgroups = list(group.groups())
        if not arrays and not subgroups:
            # A childless group is itself a leaf, sitting at its own depth.
            leaf_depths.append(depth)
            return
        leaf_depths.extend(depth + 1 for _ in arrays)
        for _name, subgroup in subgroups:
            _walk(subgroup, depth + 1)

    _walk(root_group, 0)
    return leaf_depths


def _compute_normalized_sackin_index(s3_filesystem: s3fs.S3FileSystem, content_id: str) -> float:
    """Compute the normalized Sackin index of the valid NWB file identified by `content_id`."""
    blob_key = f"{_BUCKET}/blobs/{content_id[:3]}/{content_id[3:6]}/{content_id}"
    if s3_filesystem.exists(blob_key):
        leaf_depths = _hdf5_leaf_depths(content_id=content_id)
    else:
        leaf_depths = _zarr_leaf_depths(s3_filesystem=s3_filesystem, content_id=content_id)
    return _normalized_sackin_index(leaf_depths=leaf_depths)


def _run(base_directory: pathlib.Path, testing: bool, limit: int | None) -> None:
    content_id_to_validity = _load_content_id_to_validity(file_path=base_directory / _INPUT_FILE_PATH)
    # Only the assets the upstream cache marked valid ('true') are processed.
    valid_content_ids = {content_id for content_id, is_valid in content_id_to_validity.items() if is_valid is True}

    derivatives_directory = base_directory / "derivatives"
    derivatives_directory.mkdir(parents=True, exist_ok=True)
    cache_file_path = derivatives_directory / (_TESTING_FILE_NAME if testing else _CACHE_FILE_NAME)
    valid_nwb_file_to_sackin_index = _load_previous_cache(file_path=cache_file_path)

    # Already-computed content IDs are exactly the keys already in the output, so re-runs skip
    # them and only pick up content IDs newly marked valid upstream.
    content_ids_to_process = sorted(valid_content_ids - valid_nwb_file_to_sackin_index.keys())

    # A testing run caps the batch tightly; otherwise the optional `--limit` bounds a single
    # run because streaming and walking each file is heavy.
    effective_limit = _TESTING_LIMIT if testing else limit
    content_ids_to_process = list(itertools.islice(content_ids_to_process, effective_limit))

    s3_filesystem = s3fs.S3FileSystem(anon=True)
    for content_id in content_ids_to_process:
        try:
            normalized_sackin_index = _compute_normalized_sackin_index(
                s3_filesystem=s3_filesystem, content_id=content_id
            )
        except Exception as exception:
            # These files were already opened successfully upstream, so a failure here is
            # almost always transient (network). Skip it and leave it for a later run to retry
            # rather than recording a wrong value.
            print(f"Skipping `{content_id}`: {type(exception).__name__}: {exception}", flush=True)
            continue
        valid_nwb_file_to_sackin_index[content_id] = normalized_sackin_index

    _write_cache(file_path=cache_file_path, records=valid_nwb_file_to_sackin_index)


if __name__ == "__main__":
    default_base_directory = pathlib.Path(__file__).parent.parent

    parser = argparse.ArgumentParser(description="Update the valid-nwb-file-to-sackin-index DANDI cache.")
    parser.add_argument(
        "--base-directory",
        type=pathlib.Path,
        default=default_base_directory,
        help=(
            "The directory containing the `sourcedata` and `derivatives` directories. "
            "Set to the mounted dataset path when run inside the pipeline container; "
            "defaults to the repository root."
        ),
    )
    parser.add_argument(
        "--testing",
        action="store_true",
        help=(
            f"Run in testing mode: process only the first {_TESTING_LIMIT} items and write "
            f"`derivatives/{_TESTING_FILE_NAME}` instead of the real cache, leaving it "
            "untouched. Omit for a complete update."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of newly valid content IDs to process in this run.",
    )
    args = parser.parse_args()

    _run(base_directory=args.base_directory, testing=args.testing, limit=args.limit)
