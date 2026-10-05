"""Live smoke test for the Translink client, plus offline parser tests.

The live tests are marked so they can be skipped: `pytest -m "not live"`.

    .venv/bin/pytest tests -q            # offline only, no network
    .venv/bin/pytest tests -q -m live    # hit the real API
"""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tl.translink_client import (
    TranslinkClient,
    TranslinkError,
    parse_arrivals,
    parse_route_stops,
    parse_vehicle_positions,
)

# A trimmed but structurally faithful realtimeschedules response, taken from
# the Langley Centre terminus of route 502 -- a public landmark, deliberately
# not a stop that would identify the maintainer. Two realtime trips (both
# running early) and one scheduled-only trip.
FIXTURE_ARRIVALS = [
    {
        "id": "13070",
        "sc": "57027",
        "sn": "Langley Centre @ Bay 3",
        "la": 49.106256,
        "lo": -122.654259,
        "sd": "2026-10-04T18:22:48.0219727",
        "lu": 1791163355,
        "r": [
            {
                "ri": "6738",
                "rs": "502",
                "rl": "Fraser Hwy",
                "di": 1,
                "dn": "Surrey Central Station",
                "hs": "Fraser Hwy/To Surrey Central Station",
                "rt": 3,
                "t": [
                    {
                        "ti": "15495767",
                        "th": "502 Fraser Hwy/To Surrey Central Station",
                        "dt": "18:30",
                        "ut": 1791165000,
                        "dl": -549,
                        "po": False,
                        "do": False,
                        "no": False,
                        "ba": 1,
                        "tp": False,
                        "rt": True,
                    },
                    {
                        "ti": "15495768",
                        "th": "502 Fraser Hwy/To Surrey Central Station",
                        "dt": "18:45",
                        "ut": 1791165900,
                        "dl": -684,
                        "po": False,
                        "do": False,
                        "no": False,
                        "ba": 1,
                        "tp": False,
                        "rt": True,
                    },
                    {
                        "ti": "15495769",
                        "th": "502 Fraser Hwy/To Surrey Central Station",
                        "dt": "19:10",
                        "ut": 1791167400,
                        "dl": 0,
                        "po": False,
                        "do": False,
                        "no": False,
                        "ba": 1,
                        "tp": False,
                        "rt": False,
                    },
                ],
            }
        ],
    }
]


# The live tests poll a route terminus -- a public landmark that identifies
# nobody. Override to test a stop of your own:
#   TRANSLINK_TEST_ROUTE=502 TRANSLINK_TEST_STOP=57027 pytest -m live
import os

TEST_ROUTE = os.environ.get("TRANSLINK_TEST_ROUTE", "502")
TEST_DIRECTION = int(os.environ.get("TRANSLINK_TEST_DIRECTION", "1"))
TEST_STOP = os.environ.get("TRANSLINK_TEST_STOP", "57027")


def make_client() -> TranslinkClient:
    session = httpx.AsyncClient(
        headers={"Accept-Encoding": "gzip"}, timeout=15, follow_redirects=True
    )
    return TranslinkClient(session, {})


# --- offline parser tests -------------------------------------------------


def test_parse_arrivals_shape():
    arrivals = parse_arrivals(FIXTURE_ARRIVALS, route="502", direction=1)
    assert len(arrivals) == 3
    first = arrivals[0]
    assert first.trip_id == "15495767"
    assert first.stop_code == "57027"
    assert first.route == "502"
    assert first.direction == 1
    assert first.destination == "Surrey Central Station"
    assert first.bay == "1"
    assert first.is_realtime is True


def test_scheduled_time_is_recovered_from_delay():
    """`ut` is predicted only; the timetable time comes from undoing `dl`."""
    arrivals = parse_arrivals(FIXTURE_ARRIVALS, route="502", direction=1)
    first = arrivals[0]
    assert first.delay_seconds == -549
    assert first.scheduled - first.arrival == timedelta(seconds=549)
    assert first.arrival < first.scheduled, "early bus should arrive before timetable"


def test_scheduled_only_trip_has_zero_delay():
    arrivals = parse_arrivals(FIXTURE_ARRIVALS, route="502", direction=1)
    last = arrivals[-1]
    assert last.is_realtime is False
    assert last.delay_seconds == 0
    assert last.scheduled == last.arrival


def test_arrivals_are_sorted_chronologically():
    payload = json.loads(json.dumps(FIXTURE_ARRIVALS))
    trips = payload[0]["r"][0]["t"]
    trips.reverse()
    arrivals = parse_arrivals(payload, route="502", direction=1)
    assert [a.arrival for a in arrivals] == sorted(a.arrival for a in arrivals)


def test_empty_and_malformed_payloads():
    assert parse_arrivals([], route="502", direction=1) == []
    with pytest.raises(TranslinkError):
        parse_arrivals({"not": "a list"}, route="502", direction=1)
    # Junk inside a well-formed envelope must be skipped, not raised.
    assert parse_arrivals([{"sc": "1", "r": None}], route="502", direction=1) == []
    assert parse_arrivals([{"sc": "1", "r": [{"t": [{"ti": "x"}]}]}], route="502") == []


def test_parse_vehicle_positions_skips_bad_rows():
    vehicles = parse_vehicle_positions(
        [
            {"vi": "19140", "ti": "15495765", "p": "322143", "th": "502", "la": 49.17, "ln": -122.83, "ts": 1791163016},
            {"vi": "bad", "la": "not-a-number", "ln": None, "ts": 1791163016},
            {"vi": "no-ts", "la": 49.1, "ln": -122.8},
        ]
    )
    assert len(vehicles) == 1
    assert vehicles[0].vehicle_id == "19140"
    assert vehicles[0].pattern_id == "322143"


def test_parse_route_stops_preserves_order():
    stops = parse_route_stops(
        {"s": [{"sc": "57027", "sn": "Langley Centre @ Bay 3"}, {"sc": "57028", "sn": "Fraser Hwy @ 56 Ave"}]}
    )
    assert [s.code for s in stops] == ["57027", "57028"]
    assert stops[0].sequence == 0


# --- live tests -----------------------------------------------------------


@pytest.mark.live
def test_live_arrivals_at_route_terminus():
    async def run():
        client = make_client()
        try:
            return await client.get_arrivals(TEST_STOP, TEST_ROUTE, query_size=5, direction=1)
        finally:
            await client._session.aclose()

    arrivals = asyncio.run(run())
    assert arrivals, "expected upcoming buses at a live stop"
    assert all(a.stop_code == TEST_STOP for a in arrivals)
    assert all(a.route == TEST_ROUTE for a in arrivals)
    assert all(a.direction == 1 for a in arrivals)
    now = datetime.now().astimezone()
    assert all(a.arrival > now for a in arrivals), "API returned a past arrival"
    assert arrivals == sorted(arrivals, key=lambda a: a.arrival)


@pytest.mark.live
def test_live_route_stops_include_watched_stop():
    async def run():
        client = make_client()
        try:
            return await client.get_route_stops(TEST_ROUTE, TEST_DIRECTION)
        finally:
            await client._session.aclose()

    stops = asyncio.run(run())
    codes = {s.code for s in stops}
    assert TEST_STOP in codes, "test stop missing from the route"
    assert len(stops) > 20


@pytest.mark.live
def test_live_vehicle_positions():
    async def run():
        client = make_client()
        try:
            return await client.get_vehicle_positions(TEST_ROUTE, TEST_DIRECTION)
        finally:
            await client._session.aclose()

    vehicles = asyncio.run(run())
    assert vehicles, "expected at least one bus in service"
    assert all(-123.5 < v.longitude < -122.0 for v in vehicles), "coordinates look wrong"


@pytest.mark.live
def test_live_unknown_route_raises():
    async def run():
        client = make_client()
        try:
            await client.get_route_stops("99999", 1)
            return None
        finally:
            await client._session.aclose()

    with pytest.raises(TranslinkError):
        asyncio.run(run())


# --- caching tests --------------------------------------------------------


def _counting_session(responses: list[httpx.Response]) -> tuple[httpx.AsyncClient, list[str]]:
    """A session that replays `responses` and records the requests it saw."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return responses[min(len(seen) - 1, len(responses) - 1)]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, seen


def test_cache_serves_second_call_without_network():
    import asyncio

    async def run():
        session, seen = _counting_session(
            [httpx.Response(200, json=FIXTURE_ARRIVALS, headers={"etag": 'W/"abc"'})]
        )
        client = TranslinkClient(session, {})
        try:
            first = await client.get_arrivals(TEST_STOP, TEST_ROUTE, 5, 1)
            second = await client.get_arrivals(TEST_STOP, TEST_ROUTE, 5, 1)
        finally:
            await session.aclose()
        return first, second, seen

    first, second, seen = asyncio.run(run())
    assert len(seen) == 1, "second call should have hit the cache"
    assert [a.trip_id for a in first] == [a.trip_id for a in second]


def test_304_revalidation_reuses_cached_body():
    """A 304 must extend the TTL and return the stored body, not fail."""

    import asyncio

    async def run():
        payload = FIXTURE_ARRIVALS
        session, seen = _counting_session(
            [
                httpx.Response(200, json=payload, headers={"etag": 'W/"abc"'}),
                httpx.Response(304),
            ]
        )
        client = TranslinkClient(session, {})
        try:
            await client.get_arrivals("1", "1", 5, 1)
            # Force the entry stale so the second call revalidates.
            key = next(iter(client._cache))
            client._cache[key]["expires"] = (datetime.now().astimezone() - timedelta(seconds=1)).isoformat()
            again = await client.get_arrivals("1", "1", 5, 1)
            expires = client._cache[key]["expires"]
        finally:
            await session.aclose()
        return again, seen, expires

    again, seen, expires = asyncio.run(run())
    assert len(seen) == 2, "stale entry should have triggered a revalidation"
    assert again[0].stop_code == TEST_STOP, "304 should return the cached body"
    assert datetime.fromisoformat(expires) > datetime.now().astimezone(), "304 should extend the TTL"


def test_http_404_raises_translink_error():
    import asyncio

    async def run():
        session, _ = _counting_session([httpx.Response(404, json={})])
        client = TranslinkClient(session, {})
        try:
            await client.get_route_stops("99999", 1)
            return None
        finally:
            await session.aclose()

    with pytest.raises(TranslinkError):
        asyncio.run(run())


# --- static guards -------------------------------------------------------


def _config_flow_calls() -> list[ast.Call]:
    """Every call expression in config_flow.py."""
    source = Path(__file__).resolve().parents[1] / "custom_components" / "translink" / "config_flow.py"
    tree = ast.parse(source.read_text())
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def test_vol_schema_calls_take_no_kwargs():
    """Regression guard: `description_placeholders` belongs to async_show_form.

    Passing it to vol.Schema raises TypeError the moment a user submits that
    step, which fails the whole config flow with no useful message in the UI.
    """
    offenders = []
    for call in _config_flow_calls():
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "Schema"):
            continue
        if [kw.arg for kw in call.keywords]:
            offenders.append(ast.dump(call)[:120])
    assert not offenders, f"vol.Schema called with keyword arguments: {offenders}"


def test_placeholders_are_passed_to_show_form():
    """Every description placeholder must reach async_show_form."""
    source = Path(__file__).resolve().parents[1] / "custom_components" / "translink" / "config_flow.py"
    tree = ast.parse(source.read_text())
    show_form_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "async_show_form"
    ]
    assert show_form_calls, "expected async_show_form calls"
    for call in show_form_calls:
        # Error-path forms are rendered without a schema at all.
        schema_kwarg = next((kw for kw in call.keywords if kw.arg == "data_schema"), None)
        if schema_kwarg is None or not isinstance(schema_kwarg.value, ast.Call):
            continue
        nested = [
            kw.arg
            for arg in schema_kwarg.value.args
            if isinstance(arg, ast.Call)
            for kw in arg.keywords
        ]
        assert "description_placeholders" not in nested


def test_cache_survives_a_json_round_trip():
    """Regression guard: the cache is persisted as JSON, datetimes are not.

    A save/load cycle used to turn `expires` into a string, and the freshness
    check then raised TypeError outside the try block -- which failed every
    poll and left the entity unavailable after the first restart.
    """

    import asyncio

    async def run():
        payload = FIXTURE_ARRIVALS
        session, seen = _counting_session(
            [httpx.Response(200, json=payload, headers={"etag": 'W/"abc"'})]
        )
        client = TranslinkClient(session, {})
        try:
            await client.get_arrivals(TEST_STOP, TEST_ROUTE, 5, 1)
            # Simulate Store.async_save() -> async_load().
            client._cache = json.loads(json.dumps(client._cache))
            again = await client.get_arrivals(TEST_STOP, TEST_ROUTE, 5, 1)
        finally:
            await session.aclose()
        return again, seen

    again, seen = asyncio.run(run())
    assert len(seen) == 1, "reloaded cache should still be fresh"
    assert again[0].stop_code == TEST_STOP


def test_cache_expiry_is_json_serialisable():
    import asyncio

    async def run():
        session, _ = _counting_session([httpx.Response(200, json=FIXTURE_ARRIVALS)])
        client = TranslinkClient(session, {})
        try:
            await client.get_arrivals(TEST_STOP, TEST_ROUTE, 5, 1)
            json.dumps(client._cache)  # must not raise
            return True
        finally:
            await session.aclose()

    assert asyncio.run(run()) is True
