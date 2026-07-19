# DANDI Cache: `valid-nwb-file-to-sackin-index`

A mapping from the content ID of every valid NWB file on the DANDI archive to the normalized Sackin index of that file's internal object hierarchy.

The set of valid NWB files is taken from the [`content-id-to-valid-nwb-file`](https://github.com/dandi-cache/content-id-to-valid-nwb-file) cache, restricted to the entries it marked `true`. Each such file is streamed directly from the public DANDI S3 bucket and read with [h5py](https://www.h5py.org/) (HDF5 assets) or [zarr](https://zarr.readthedocs.io/) (Zarr assets), and the Sackin index of its object hierarchy is computed.

## What is computed

The **Sackin index** measures the imbalance (asymmetry) of a tree by summing the depths of its leaves: `S = Σ d_i`, where `d_i` is the number of ancestors of leaf `i`. An NWB file is itself a tree — the file's own group/array structure — so it is used directly as the tree:

- **Internal nodes** are groups that contain children.
- **Leaves** are nodes with no children: every dataset (HDF5) / array (Zarr), plus any empty group.
- A node's **depth** is the number of components in its path from the root (its number of ancestors).

The raw index is normalized to roughly `[0, 1]` with the min-max convention, so files of different sizes are comparable:

```
S_norm = (S - S_min) / (S_max - S_min)
```

where, for `n` leaves, `S_max = n(n + 1)/2 - 1` is the caterpillar (most imbalanced) tree and `S_min = n * ceil(log2(n))` approximates a balanced tree. A file with a single leaf normalizes to `0.0`.

Because `S_min` is the balanced *binary* tree approximation, a high-branching file — whose leaves sit shallower than they would in a binary tree — can produce a value slightly below `0`. The values are reported as-is, without clamping, so this convention stays transparent.

Each line of the derivatives is a JSON object of the form:

```json
{"<content_id>": <normalized_sackin_index>}
```

Updated frequently.

Primarily for use by developers.



## One-time use

If you only plan to use this cache infrequently or from disparate locations, you can directly download the latest version of the cache as a compressed [JSON Lines](https://jsonlines.org/) file from the `dist` branch:

### Python API (recommended)

```python
import gzip
import json

import requests

url = "https://raw.githubusercontent.com/dandi-cache/valid-nwb-file-to-sackin-index/refs/heads/dist/derivatives/valid_nwb_file_to_sackin_index.jsonl.gz"
response = requests.get(url)
lines = gzip.decompress(data=response.content).decode("utf-8").splitlines()
valid_nwb_file_to_sackin_index = [json.loads(line) for line in lines]
```

Each line is a single-entry mapping of `{"<content_id>": <normalized_sackin_index>}`.

### Save to file

```bash
curl https://raw.githubusercontent.com/dandi-cache/valid-nwb-file-to-sackin-index/refs/heads/dist/derivatives/valid_nwb_file_to_sackin_index.jsonl.gz -o valid_nwb_file_to_sackin_index.jsonl.gz
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
