"""Harmony backend seams.

Every place where this plugin depends on the ``harmony-py`` API lives here so
that there is a single fix point when the backend changes. The functions are
patched in tests.

Verified against ``harmony-py`` 1.8.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

__all__ = [
    "CapabilitiesUnavailable",
    "CollectionNotFound",
    "build_request",
    "capabilities",
    "make_client",
]

_CAPABILITIES_VERSION = "3"


class CapabilitiesUnavailable(Exception):
    """Raised when the Harmony capabilities check cannot be performed.

    Maps to ``Capabilities(None)``: never hard-block a request on a failed check.
    """


class CollectionNotFound(Exception):
    """Raised when Harmony definitively has no service for a collection."""


def make_client(ctx: Any) -> Any:
    """Build a Harmony client from the caller's Earthdata Login credentials.

    ``harmony.Client`` takes either ``auth=(username, password)`` or
    ``token=...``. A netrc login has no token, so prefer the basic-auth pair.
    """

    import harmony

    basic = getattr(ctx.auth, "basic_auth", None)
    creds = basic() if callable(basic) else None
    if creds:
        return harmony.Client(auth=creds)
    return harmony.Client(token=ctx.auth.token())


def build_request(
    *, collection_id: str, granule_ids: Sequence[str], **options: Any
) -> Any:
    """Build a ``harmony.Request`` from plugin ``Options`` values.

    The plugin's ``Options`` dataclass is the single schema: every option is
    accepted here and passed through, except the few names that differ from
    ``harmony-py`` (``bbox``/``wkt`` -> ``spatial``, ``shapefile`` -> ``shape``,
    ``output_format`` -> ``format``). ``None``/empty values are omitted so
    Harmony applies its own defaults.
    """

    import harmony

    bbox = options.pop("bbox", None)
    wkt = options.pop("wkt", None)
    shapefile = options.pop("shapefile", None)
    output_format = options.pop("output_format", None)
    skip_preview = options.pop("skip_preview", False)

    kwargs: dict[str, Any] = {
        "collection": harmony.Collection(id=collection_id),
        "granule_id": list(granule_ids),
    }
    if bbox is not None:
        kwargs["spatial"] = harmony.BBox(*bbox)
    elif wkt is not None:
        kwargs["spatial"] = harmony.WKT(wkt)
    if shapefile:
        # harmony-py takes the shapefile path and opens/uploads it itself,
        # inferring the MIME type from the extension. Passing an open handle or
        # a (file, mime) tuple fails its path validation.
        kwargs["shape"] = shapefile
    if output_format:
        kwargs["format"] = output_format
    if skip_preview:
        kwargs["skip_preview"] = True
    for key, value in options.items():
        if key == "variables":
            # An empty selection means "all variables", i.e. send nothing.
            if value:
                kwargs[key] = list(value)
        elif value is not None:
            kwargs[key] = value
    return harmony.Request(**kwargs)


def capabilities(
    client: Any, *, collection_id: str | None = None, short_name: str | None = None
) -> dict[str, Any]:
    """Return Harmony's capabilities response for a collection.

    Raises :class:`CollectionNotFound` for a definitive no and
    :class:`CapabilitiesUnavailable` when the check cannot be made.
    """

    import harmony

    if not collection_id and not short_name:
        raise CapabilitiesUnavailable("a collection id or short name is required")
    # CapabilitiesRequest rejects sending both, so prefer the concept id.
    if collection_id:
        request = harmony.CapabilitiesRequest(
            collection_id=collection_id,
            capabilities_version=_CAPABILITIES_VERSION,
        )
    else:
        request = harmony.CapabilitiesRequest(
            short_name=short_name,
            capabilities_version=_CAPABILITIES_VERSION,
        )
    try:
        response = client.submit(request)
    except Exception as exc:  # noqa: BLE001 - translate backend errors below
        if _is_not_found(exc):
            raise CollectionNotFound(short_name or collection_id) from exc
        raise CapabilitiesUnavailable(str(exc)) from exc
    if not isinstance(response, dict):
        raise CapabilitiesUnavailable("unexpected capabilities response")
    return response


def _is_not_found(exc: Exception) -> bool:
    text = " ".join(str(arg) for arg in exc.args).lower()
    return "not found" in text or "could not find" in text
