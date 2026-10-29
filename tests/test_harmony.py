from unittest import mock

import pytest
from earthaccess_plugin_spec import (
    Asset,
    Capability,
    JobStatus,
    UnsupportedCollectionError,
)
from earthaccess_plugin_spec.errors import JobFailedError, JobPausedError
from earthaccess_plugin_spec.results import GranuleResultSet
from earthaccess_plugin_spec.testing import fake_context, sample_result_set

from earthaccess_harmony import HarmonyJob, HarmonyPlugin
from earthaccess_harmony.client import CapabilitiesUnavailable, CollectionNotFound
from earthaccess_harmony.job import ORDER_URL, STATUS_MAP, format_of

from .fixtures import FakeHarmonyClient, make_granule


def make_plugin(client: FakeHarmonyClient) -> HarmonyPlugin:
    plugin = HarmonyPlugin(fake_context())
    plugin._h = client  # skip make_client
    return plugin


class TestFormatOf:
    @pytest.mark.parametrize(
        ("href", "expected"),
        [
            ("https://e.org/a.nc4", "netcdf4"),
            ("https://e.org/a.nc", "netcdf4"),
            ("https://e.org/a.zarr", "zarr"),
            ("https://e.org/a.parquet", "geoparquet"),
            ("https://e.org/a.csv", "csv"),
            ("https://e.org/a.tif", "geotiff"),
            ("https://e.org/a.nc4?token=1", "netcdf4"),
            ("https://e.org/a.unknown", None),
        ],
    )
    def test_formats(self, href, expected):
        assert format_of(href) == expected


class TestStatusMapping:
    def test_known_states(self):
        client = FakeHarmonyClient()
        job = HarmonyJob("j1", client)
        for raw, expected in STATUS_MAP.items():
            client._statuses = [raw]
            assert job.status() is expected

    def test_unknown_state_warns_and_keeps_running(self, caplog):
        client = FakeHarmonyClient(statuses=["running", "nonsense", "successful"])
        job = HarmonyJob("j1", client)
        with caplog.at_level("WARNING", logger="earthaccess.plugins"):
            assert job.status() is JobStatus.RUNNING  # running
            assert job.status() is JobStatus.RUNNING  # nonsense -> warning
        assert "unknown Harmony status" in caplog.text
        assert job.status() is JobStatus.SUCCEEDED

    def test_info_has_order_url_and_no_request(self):
        job = HarmonyJob("j1", FakeHarmonyClient())
        info = job.info()
        assert info["order_url"] == ORDER_URL + "j1"
        assert "request" not in info
        assert info["message"] == "ok"


class TestFetch:
    def test_assets_access_and_format(self):
        client = FakeHarmonyClient(
            urls=[
                "s3://bucket/a.nc4",
                "https://e.org/b.parquet",
                "https://e.org/c.unknown",
            ]
        )
        job = HarmonyJob("j1", client)
        assets = job.results()
        assert all(isinstance(a, Asset) for a in assets)
        assert assets[0].access == "s3" and assets[0].format == "netcdf4"
        assert assets[1].access == "https" and assets[1].format == "geoparquet"
        assert assets[2].format is None


class TestFailureAndPause:
    def test_failed_job_raises(self):
        job = HarmonyJob("j1", FakeHarmonyClient(statuses=["failed"]))
        assert job.wait(timeout=5) is JobStatus.FAILED
        with pytest.raises(JobFailedError):
            job.results()

    def test_paused_then_resume(self):
        client = FakeHarmonyClient(statuses=["previewing"])
        job = HarmonyJob("j1", client)
        assert job.status() is JobStatus.PAUSED
        with pytest.raises(JobPausedError, match="resume"):
            job.results()
        assert job.resume() is True
        assert client.resumed

    def test_skip_preview_pauses_and_resumes_a_preview(self):
        client = FakeHarmonyClient(statuses=["previewing"])
        job = HarmonyJob("j1", client)
        assert job.skip_preview() is True
        assert client.paused and client.resumed

    def test_skip_preview_is_a_noop_when_not_previewing(self):
        client = FakeHarmonyClient(statuses=["successful"])
        job = HarmonyJob("j1", client)
        assert job.skip_preview() is True
        assert not client.paused and not client.resumed

    def test_cancel(self):
        client = FakeHarmonyClient()
        job = HarmonyJob("j1", client)
        assert job.cancel() is True
        assert client.canceled


class TestRequest:
    def test_forwards_collection_and_granules(self):
        plugin = HarmonyPlugin(fake_context())
        results = sample_result_set()
        with (
            mock.patch("earthaccess_harmony.client.build_request") as build,
            mock.patch(
                "earthaccess_harmony.client.capabilities",
                return_value={"services": [{"capabilities": {}}]},
            ),
        ):
            build.return_value = {"request": True}
            plugin._h = FakeHarmonyClient()
            job = plugin.request(results, variables=["x"], skip_preview=True)
        assert isinstance(job, HarmonyJob)
        kwargs = build.call_args.kwargs
        assert kwargs["collection_id"] == results.collection_id
        assert kwargs["granule_ids"] == list(results.ids)
        assert kwargs["variables"] == ["x"]
        assert kwargs["skip_preview"] is True

    def test_get_job_is_resumable(self):
        plugin = HarmonyPlugin(fake_context())
        plugin._h = FakeHarmonyClient()
        job = plugin.get_job("abc")
        assert isinstance(job, HarmonyJob)
        assert job.id == "abc"
        assert job.resumable is True


class TestBuildRequest:
    @staticmethod
    def _fake_harmony(monkeypatch):
        import sys
        import types

        captured: dict = {}

        class Request:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class Collection:
            def __init__(self, id):
                self.id = id

        class BBox:
            def __init__(self, *args):
                self.args = args

        class WKT:
            def __init__(self, value):
                self.value = value

        fake = types.ModuleType("harmony")
        fake.Request = Request
        fake.Collection = Collection
        fake.BBox = BBox
        fake.WKT = WKT
        monkeypatch.setitem(sys.modules, "harmony", fake)
        return captured, BBox

    def test_maps_every_operation(self, monkeypatch):
        captured, BBox = self._fake_harmony(monkeypatch)
        from earthaccess_harmony.client import build_request

        build_request(
            collection_id="C1",
            granule_ids=["G1"],
            bbox=(-165, 52, -140, 77),
            temporal={"start": "s", "stop": "e"},
            variables=["blue_var"],
            crs="EPSG:3995",
            width=512,
            height=512,
            output_format="image/tiff",
            max_results=10,
            average="time",
            concatenate=True,
            skip_preview=True,
        )

        assert isinstance(captured["spatial"], BBox)
        assert captured["temporal"] == {"start": "s", "stop": "e"}
        assert captured["variables"] == ["blue_var"]
        assert captured["crs"] == "EPSG:3995"
        assert captured["width"] == 512 and captured["height"] == 512
        assert captured["format"] == "image/tiff"
        assert captured["max_results"] == 10
        assert captured["average"] == "time"
        assert captured["concatenate"] is True
        assert captured["skip_preview"] is True

    def test_geojson_shapefile_passes_path(self, monkeypatch, tmp_path):
        captured, _ = self._fake_harmony(monkeypatch)
        from earthaccess_harmony.client import build_request

        path = tmp_path / "aoi.geojson"
        path.write_text("{}")
        build_request(collection_id="C1", granule_ids=["G1"], shapefile=str(path))

        # harmony-py opens/uploads the file itself and infers the MIME type from
        # the extension, so it wants the path, not an open handle or tuple.
        assert captured["shape"] == str(path)

    def test_zip_shapefile_passes_path(self, monkeypatch, tmp_path):
        captured, _ = self._fake_harmony(monkeypatch)
        from earthaccess_harmony.client import build_request

        path = tmp_path / "aoi.zip"
        path.write_bytes(b"")
        build_request(collection_id="C1", granule_ids=["G1"], shapefile=str(path))

        assert captured["shape"] == str(path)

    def test_shapefile_request_is_valid_against_real_harmony(self, tmp_path):
        # Regression: harmony-py validates `shape` as a path string. Passing an
        # open handle or a (file, mime) tuple made Request.is_valid() raise
        # "TypeError: stat: path should be string ... not tuple" at submit time.
        from earthaccess_harmony.client import build_request

        path = tmp_path / "aoi.geojson"
        path.write_text("{}")
        request = build_request(
            collection_id="C1234-PROV",
            granule_ids=["G1"],
            shapefile=str(path),
        )

        assert request.is_valid(), request.error_messages()

    def test_wkt_sets_spatial(self, monkeypatch):
        captured, _ = self._fake_harmony(monkeypatch)
        from earthaccess_harmony.client import build_request

        build_request(collection_id="C1", granule_ids=["G1"], wkt="POLYGON ((0 0))")

        assert captured["spatial"].value == "POLYGON ((0 0))"

    def test_none_values_are_omitted(self, monkeypatch):
        captured, _ = self._fake_harmony(monkeypatch)
        from earthaccess_harmony.client import build_request

        build_request(collection_id="C1", granule_ids=["G1"])
        assert "crs" not in captured
        assert "spatial" not in captured
        assert "format" not in captured


def test_entry_point_registered():
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="earthaccess.plugins")}
    assert "harmony" in names


class TestSupport:
    def test_supported_with_operations(self):
        plugin = HarmonyPlugin(fake_context())
        plugin._h = FakeHarmonyClient()
        results = GranuleResultSet([make_granule(collection_id="C-HARMONY")])
        with mock.patch(
            "earthaccess_harmony.client.capabilities",
            return_value={
                "services": [
                    {
                        "capabilities": {
                            "subsetting": {"bbox": True, "variable": True},
                            "outputFormats": [{"mimeType": "application/netcdf4"}],
                        }
                    }
                ]
            },
        ) as caps:
            capabilities = plugin.capabilities(results)
        caps.assert_called_once()
        assert capabilities.ok is True
        assert capabilities.operations == {
            Capability.SUBSET_BBOX,
            Capability.SUBSET_VARIABLE,
        }
        assert capabilities.formats == frozenset({"netcdf4"})

    def test_unknown_output_format_is_not_advertised(self):
        plugin = HarmonyPlugin(fake_context())
        plugin._h = FakeHarmonyClient()
        results = GranuleResultSet([make_granule(collection_id="C-HARMONY")])
        with mock.patch(
            "earthaccess_harmony.client.capabilities",
            return_value={
                "services": [
                    {
                        "capabilities": {
                            "outputFormats": [
                                {"mimeType": "application/octet-stream"}
                            ]
                        }
                    }
                ]
            },
        ):
            capabilities = plugin.capabilities(results)
        assert capabilities.formats == frozenset()

    def test_capabilities_by_short_name(self):
        plugin = HarmonyPlugin(fake_context())
        plugin._h = FakeHarmonyClient()
        with mock.patch(
            "earthaccess_harmony.client.capabilities",
            return_value={"services": [{"capabilities": {}}]},
        ) as caps:
            capabilities = plugin.capabilities("ATL06")
        assert capabilities.ok is True
        assert capabilities.collection == "ATL06"
        assert caps.call_args.kwargs["short_name"] == "ATL06"

    def test_definitive_no(self):
        plugin = HarmonyPlugin(fake_context())
        plugin._h = FakeHarmonyClient()
        results = GranuleResultSet([make_granule(collection_id="C-NO-HARMONY")])
        with mock.patch(
            "earthaccess_harmony.client.capabilities",
            side_effect=CollectionNotFound("C-NO-HARMONY"),
        ):
            support = plugin.capabilities(results)
        assert support.ok is False
        assert "no Harmony service" in support.reason

    def test_unknown_when_capabilities_unavailable(self):
        plugin = HarmonyPlugin(fake_context())
        plugin._h = FakeHarmonyClient()
        results = GranuleResultSet([make_granule(collection_id="C-X")])
        with mock.patch(
            "earthaccess_harmony.client.capabilities",
            side_effect=CapabilitiesUnavailable("boom"),
        ):
            support = plugin.capabilities(results)
        assert support.ok is None

    def test_request_raises_before_backend_call(self):
        plugin = HarmonyPlugin(fake_context())
        client = FakeHarmonyClient()
        plugin._h = client
        results = GranuleResultSet([make_granule(collection_id="C-NO-HARMONY")])
        with (
            mock.patch(
                "earthaccess_harmony.client.capabilities",
                side_effect=CollectionNotFound("C-NO-HARMONY"),
            ),
            pytest.raises(UnsupportedCollectionError),
        ):
            plugin.request(results, variables=["x"])
        # The support probe ran, but nothing reached submit().
        assert all(name != "submit" for name, _ in client.calls)
