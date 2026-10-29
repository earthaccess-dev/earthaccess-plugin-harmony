"""The Harmony plugin: subset and transform Earthdata granules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from earthaccess_plugin_spec import (
    Capabilities,
    Capability,
    PluginBase,
    format_name,
)
from earthaccess_plugin_spec.granules import (
    collection_concept_id,
    concept_id,
    peek,
)

from . import client as _client
from .job import HarmonyJob

__all__ = ["HarmonyPlugin"]


_EXAMPLES = {
    Capability.SUBSET_VARIABLE: (
        'ea.plugins.harmony.request(results, variables=["blue_var"]).results()'
    ),
    Capability.REPROJECT: (
        'ea.plugins.harmony.request(results, crs="EPSG:3995", '
        "width=512, height=512).results()"
    ),
}


def _operations_from_services(
    services: list[dict[str, Any]],
) -> tuple[frozenset[Capability], frozenset[str]]:
    """Map Harmony's capabilities response onto our operation/format enums."""

    operations: set[Capability] = set()
    formats: set[str] = set()
    for service in services:
        caps = service.get("capabilities", {}) or {}
        subsetting = caps.get("subsetting", {}) or {}
        if subsetting.get("bbox"):
            operations.add(Capability.SUBSET_BBOX)
        if subsetting.get("temporal"):
            operations.add(Capability.SUBSET_TEMPORAL)
        if subsetting.get("variable"):
            operations.add(Capability.SUBSET_VARIABLE)
        if subsetting.get("shape"):
            operations.add(Capability.SUBSET_SHAPE)
        if subsetting.get("dimension"):
            operations.add(Capability.SUBSET_DIMENSION)
        if (caps.get("reprojection") or {}).get("supported"):
            operations.add(Capability.REPROJECT)
        averaging = caps.get("averaging", {}) or {}
        if averaging.get("time"):
            operations.add(Capability.AVERAGE_TIME)
        if averaging.get("area"):
            operations.add(Capability.AVERAGE_AREA)
        if caps.get("concatenation"):
            operations.add(Capability.CONCATENATE)
        for output in caps.get("outputFormats", []) or []:
            # Normalize backend MIME types to the contract's FORMATS vocabulary.
            name = format_name(output.get("mimeType") or output.get("name") or "")
            if name:
                formats.add(name)
    return frozenset(operations), frozenset(formats)


class HarmonyPlugin(PluginBase):
    """Subset, reproject, reformat and transform Earthdata granules with Harmony.

    The ``Options`` mirror the ``harmony-py`` ``Request`` keywords, so every
    Harmony operation is available. For example, reproject to a polar grid and
    return a GeoTIFF:

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
    dataset = ea.open(job)   # or ea.download(job, "./data")
    ```
    """

    name = "harmony"
    needs = frozenset({"concept_id"})
    auth = "edl"
    output = "assets"

    @dataclass
    class Options:
        # Subsetting
        bbox: tuple[float, float, float, float] | None = (
            None  # west, south, east, north
        )
        wkt: str | None = None  # alternative spatial extent
        temporal: dict[str, Any] | None = None  # {"start": datetime, "stop": datetime}
        variables: list[str] = field(default_factory=list)
        shapefile: str | None = None
        # Reprojection
        crs: str | None = None
        width: int | None = None
        height: int | None = None
        scale_extent: list[float] | None = None
        scale_size: list[float] | None = None
        interpolation: str | None = None
        # Aggregation / output
        average: str | None = None  # "time" | "area" | "time,area"
        concatenate: bool | None = None
        output_format: str | None = None  # e.g. "application/x-netcdf4", "image/tiff"
        # Misc harmony-py passthroughs
        max_results: int | None = None
        dimensions: list[Any] | None = None
        extend: list[str] | None = None
        destination_url: str | None = None
        grid: str | None = None
        ignore_errors: bool | None = None
        pixel_subset: bool | None = None
        service_id: str | None = None
        labels: list[str] | None = None
        granule_name: list[str] | None = None
        skip_preview: bool = False

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._h: Any = None

    def _harmony_client(self) -> Any:
        if self._h is None:
            self._h = _client.make_client(self._ctx)
        return self._h

    def _request(self, results: Any, options: Options) -> HarmonyJob:
        # Stream the resultset once, projecting only what Harmony needs: the
        # collection concept id and the granule concept ids.
        collection_id = ""
        granule_ids: list[str] = []
        for granule in results:
            if not collection_id:
                collection_id = collection_concept_id(granule)
            granule_ids.append(concept_id(granule))
        request = _client.build_request(
            collection_id=collection_id,
            granule_ids=granule_ids,
            **asdict(options),
        )
        client = self._harmony_client()
        return HarmonyJob(client.submit(request), client)

    def get_job(self, job_id: str) -> HarmonyJob:
        return HarmonyJob(job_id, self._harmony_client())

    def _supports(self, results: Any) -> Capabilities:
        short_name = getattr(results, "short_name", "") or ""
        collection_id = (
            "" if short_name else collection_concept_id(peek(results))
        )
        label = short_name or collection_id
        if not label:
            return Capabilities(None, reason="need a collection id or short name")
        try:
            response = _client.capabilities(
                self._harmony_client(),
                collection_id=collection_id or None,
                short_name=short_name or None,
            )
        except _client.CollectionNotFound:
            return Capabilities(
                False, collection=label, reason=f"no Harmony service for {label}"
            )
        except _client.CapabilitiesUnavailable as exc:
            return Capabilities(
                None, collection=label, reason=f"could not check Harmony: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - unknown means don't hard-block
            return Capabilities(
                None, collection=label, reason=f"could not check Harmony: {exc!r}"
            )
        services = response.get("services") or []
        if not services:
            return Capabilities(
                False, collection=label, reason=f"no Harmony service for {label}"
            )
        operations, formats = _operations_from_services(services)
        return Capabilities(
            True,
            collection=label,
            operations=operations,
            formats=formats,
            examples=_EXAMPLES,
        )
