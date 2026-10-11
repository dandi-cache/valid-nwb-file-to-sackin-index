# DANDI Cache: `valid-nwb-file-to-sackin-index`

A mapping from the content ID of every valid NWB file on the DANDI archive to the normalized Sackin index of that file's internal object hierarchy.

The set of valid NWB files is taken from the [`content-id-to-valid-nwb-file`](https://github.com/dandi-cache/content-id-to-valid-nwb-file) cache, restricted to the entries it marked `true`. Each such file is streamed directly from the public DANDI S3 bucket and read with [h5py](https://www.h5py.org/) (HDF5 assets) or [zarr](https://zarr.readthedocs.io/) (Zarr assets), and the Sackin index of its object hierarchy is computed.

## What is computed

The **Sackin index** measures the imbalance (asymmetry) of a tree by summing the depths of its leaves: $S = \sum_i d_i$, where $d_i$ is the number of ancestors of leaf $i$. An NWB file is itself a tree — the file's own group/array structure — so it is used directly as the tree:

- **Internal nodes** are groups that contain children.
- **Leaves** are nodes with no children: every dataset (HDF5) / array (Zarr), plus any empty group.
- A node's **depth** is the number of components in its path from the root (its number of ancestors).

The raw index is normalized to roughly $[0, 1]$ with the min-max convention, so files of different sizes are comparable:

$$S_{\text{norm}} = \frac{S - S_{\min}}{S_{\max} - S_{\min}}$$

For $n$ leaves, $S_{\max} = n(n + 1)/2 - 1$ is the caterpillar (most imbalanced) tree and $S_{\min} = n \lceil \log_2 n \rceil$ approximates a balanced tree. A file with a single leaf normalizes to $0.0$.

Because $S_{\min}$ is the balanced *binary* tree approximation, a high-branching file — whose leaves sit shallower than they would in a binary tree — can produce a value slightly below $0$. The values are reported as-is, without clamping, so this convention stays transparent.

Each line of the derivatives is a JSON object of the form:

```json
{"<content_id>": <normalized_sackin_index>}
```

Updated frequently.

Primarily for use by developers.



## One-time use

`dist` publishes each output as a [Zarr](https://zarr.dev/) store: the cache itself is `derivatives/valid_nwb_file_to_sackin_index.zarr/`.
A store is one array of [JSON Lines](https://jsonlines.org/) text cut into chunk files, each holding whole lines and gzip-compressed.
Where a key appears more than once, the last line is its current entry, and the published copy has no repeats.
It can be read with the standard library alone, or opened remotely with any Zarr library.

### Python API (recommended)

Fetch `stream/zarr.json` for the array's length and chunk size, then each chunk in order:

```python
import gzip
import json
import math
import urllib.request

store = "https://raw.githubusercontent.com/dandi-cache/valid-nwb-file-to-sackin-index/refs/heads/dist/derivatives/valid_nwb_file_to_sackin_index.zarr"


def fetch(path):
    with urllib.request.urlopen(f"{store}/{path}") as response:
        return response.read()


array = json.loads(fetch("stream/zarr.json"))
length = array["shape"][0]
chunk_bytes = array["chunk_grid"]["configuration"]["chunk_shape"][0]

valid_nwb_file_to_sackin_index = {}
for index in range(math.ceil(length / chunk_bytes)):
    for line in gzip.decompress(fetch(f"stream/c/{index}")).decode("utf-8").splitlines():
        if line.strip():
            valid_nwb_file_to_sackin_index.update(json.loads(line))
```

Each line is a single-entry mapping of `{"<content_id>": <normalized_sackin_index>}`.

An entry is not filed under its key: the chunks hold the entries in the order they were recorded, so looking up one means reading through them.
To look up many, read it once as above, or clone the `derivatives` branch.

### Remote Zarr store

Anything that opens Zarr can open the store over HTTP, reading only the chunks it needs.
The group's `zarr.json` carries the array's metadata, so opening it takes one request:

```bash
pip install "zarr>=3" fsspec aiohttp
```

```python
import json

import zarr

url = "https://raw.githubusercontent.com/dandi-cache/valid-nwb-file-to-sackin-index/refs/heads/dist/derivatives/valid_nwb_file_to_sackin_index.zarr"
stream = zarr.open_group(url, mode="r")["stream"]
print(stream.shape, stream.chunks)  # Bytes in all, and bytes per chunk.

# The first chunk, fetched and decompressed on its own: whole lines, each `{"<key>": <value>}`.
first_chunk = stream[: stream.chunks[0]].tobytes().decode("utf-8")
for line in first_chunk.splitlines():
    if line.strip():
        print(json.loads(line))
        break
```

Reading `stream[:]` returns the whole text; to read it in pieces, step through it by `stream.chunks[0]`, as each chunk is fetched whole.

### Save to file

A raw URL cannot be listed, so to fetch every chunk at once clone the `dist` branch, which holds nothing else:

```bash
git clone --branch dist --depth 1 https://github.com/dandi-cache/valid-nwb-file-to-sackin-index.git
```



## Repeated use

If you plan on using this cache regularly, clone the `derivatives` branch of this repository:

```bash
git clone --branch derivatives https://github.com/dandi-cache/valid-nwb-file-to-sackin-index.git
```

Or, if you prefer [DataLad](https://www.datalad.org/):

```bash
datalad clone https://github.com/dandi-cache/valid-nwb-file-to-sackin-index.git --branch derivatives
```

Then set up a CRON on your system to pull the latest version of the cache at your desired frequency.

For example, through `crontab -e`, add:

```bash
0 0 * * * git -C /path/to/valid-nwb-file-to-sackin-index pull
```

This will minimize data overhead by only loading the most recent changes.

Each output is a store on the `derivatives` branch, where it is the whole log, uncompacted, and plain text:

```python
import json
import pathlib

chunks = pathlib.Path("valid-nwb-file-to-sackin-index/derivatives/valid_nwb_file_to_sackin_index.zarr/stream/c")
valid_nwb_file_to_sackin_index = {}
for chunk in sorted(chunks.iterdir(), key=lambda path: int(path.name)):
    for line in chunk.read_text().splitlines():
        if line.strip():
            valid_nwb_file_to_sackin_index.update(json.loads(line))  # A later line for a key replaces an earlier one.
```
