# earthaccess-harmony

> **Plugin preview.** This package is part of the v2.0.0 plugin preview and only
> works with the `plugins` branch of earthaccess
> ([`earthaccess-dev/earthaccess@plugins`](https://github.com/earthaccess-dev/earthaccess/tree/plugins)).
> It is **not on PyPI yet** — see
> [Installing and testing the drafts](https://github.com/earthaccess-dev/earthaccess-plugin-spec/blob/main/docs/install.md).

Subset, reproject, reformat and transform Earthdata granules with
[NASA Harmony](https://harmony.earthdata.nasa.gov/), through earthaccess.

## What Harmony does

Harmony is NASA's asynchronous, cloud-based transformation service. You hand it
a collection, a set of granules, a spatial/temporal region and a transformation
(service chain) such as `subset`, `reproject` or `concatenate`, and it runs the
work in the cloud, writing output files to object storage. Large requests can be
held in a **preview** state for a human to approve before they run.

This plugin exposes Harmony through the earthaccess plugin interface: one
`request()` that returns a `Job`, and `results()` that returns the output files
as assets you can open or download.

## Install

This package is **not on PyPI yet**. It is part of the v2.0.0 plugin preview and
must be installed together with the `plugins` branch of earthaccess. The full
steps (credentials, notebook deps, sanity check) are in
[Installing and testing the drafts](https://github.com/earthaccess-dev/earthaccess-plugin-spec/blob/main/docs/install.md).

```bash
git clone git@github.com:earthaccess-dev/earthaccess.git
cd earthaccess
git checkout plugins

uv venv
uv pip install -e ".[all]"
uv pip install "git+https://github.com/earthaccess-dev/earthaccess-plugin-harmony.git"
```

## Use with earthaccess

```python
import earthaccess as ea

results = ea.search_data(
    short_name="ATL06",
    bounding_box=(-120, 40, -110, 45),
    temporal=("2020-01-01", "2020-02-01"),
)

job = ea.plugins.harmony.request(
    results,
    bbox=(-120, 40, -110, 45),
    variables=["/gt1l/land_ice_segments/h_li"],
    output_format="application/x-netcdf4",
)
```

`request()` returns immediately. Then either open the results in memory or
download them to disk — both block until the job finishes:

```python
dataset = ea.open(job)            # list[earthaccess.Asset] -> open locally
paths = ea.download(job, "./data")
```

You can also get the raw assets and drive them yourself:

```python
assets = job.results()            # list[Asset]; each has href/access/format
```

### Options

The options mirror `harmony-py`'s `Request`, so every Harmony operation is
available:

| Option | Operation | Meaning |
| --- | --- | --- |
| `bbox=(west, south, east, north)` | subset | spatial subset |
| `wkt="POLYGON(...)"` | subset | spatial subset from WKT |
| `temporal={"start": dt, "stop": dt}` | subset | temporal subset |
| `variables=[...]` | subset | variables to keep |
| `shapefile="path.shp"` | subset | spatial subset from a shapefile |
| `crs="EPSG:3995"` | reproject | target coordinate reference system |
| `width`, `height` | reproject | output raster size |
| `scale_extent=[...]`, `scale_size=[...]`, `interpolation="..."` | reproject | resampling controls |
| `average="time"` | aggregate | `"time"`, `"area"` or `"time,area"` |
| `concatenate=True` | aggregate | concatenate outputs |
| `output_format="image/tiff"` | reformat | requested media type |
| `max_results`, `grid`, `destination_url`, `dimensions`, `extend`, `labels`, `pixel_subset`, `service_id`, `ignore_errors` | misc | passed through to `harmony.Request` |
| `skip_preview=True` | — | run immediately instead of waiting for a preview |

Not every service supports every operation for every collection; check first:

```python
caps = ea.plugins.harmony.capabilities("MUR-JPL-L4-GLOB-v4.1")
print(caps)         # one summary line per operation
caps.explain()      # options, output kinds and an example for each
```

Example — subset, reproject to a polar grid and return a GeoTIFF:

```python
import datetime as dt

job = ea.plugins.harmony.request(
    results,
    bbox=(-165, 52, -140, 77),
    temporal={"start": dt.datetime(2010, 1, 1), "stop": dt.datetime(2020, 12, 30)},
    variables=["blue_var"],
    crs="EPSG:3995",
    width=512,
    height=512,
    output_format="image/tiff",
    max_results=10,
)
```

### Managing a Harmony order

Harmony jobs are remote and resumable, so you can inspect and steer an order
after submitting it:

```python
job.status()      # PENDING / RUNNING / PAUSED / SUCCEEDED / PARTIAL / FAILED / CANCELED
job.info()        # backend detail (includes an order_url); never the request payload
job.resumable     # True
job.capabilities  # {'cancel', 'pause', 'resume'}

job.pause()          # pause a running order
job.resume()         # resume a paused order
job.skip_preview()   # accept a large request without the preview gate
job.cancel()         # cancel the order

# Rebuild the job from its id, even from another process:
same = ea.plugins.harmony.get_job(job.id)
```

Large requests enter Harmony's preview state, which maps to
`JobStatus.PAUSED`. Calling `job.results()` then raises `JobPausedError`, whose
message tells you to call `skip_preview()` or `resume()` first:

```python
from earthaccess_plugin_spec.errors import JobPausedError

job = ea.plugins.harmony.request(results, variables=["h_li"])  # big request
try:
    dataset = ea.open(job)
except JobPausedError:
    job.skip_preview()
    dataset = ea.open(job)
```

## Checking that a collection is supported

Before submitting, the plugin asks Harmony's capabilities endpoint what the
collection supports and raises `UnsupportedCollectionError` early if there is no
service:

```python
ea.plugins.harmony.capabilities(results)
# harmony supported ATL06
#   subset_bbox      restrict the result to a spatial extent (bbox or wkt)
#   subset_temporal  restrict the result to a time interval (temporal)
#   subset_variable  select a subset of variables; an unknown name raises
#   formats: netcdf4
#   hint: use .explain() for options and examples

ea.plugins.harmony.capabilities("ATL06")  # by short name
```

## License

MIT.
