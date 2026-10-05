# Translink — Home Assistant custom component

Arrival predictions for a Translink (Vancouver, BC) bus stop, from the same
undocumented JSON API that powers the map view on translink.ca.

No API key. No cloud service. Nothing to sign up for.

## Install

### HACS (recommended)

1. **HACS → Integrations → ⋮ → Custom repositories**, add `iamprem/translink`.
2. **HACS → Integrations → Translink → Download.**
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Translink.**
5. Pick route → direction → stop. Add the integration once per stop.

The repo has no GitHub releases, so HACS offers the tip of `main`. The version
shown comes from `manifest.json`.

### Manual clone

If you would rather not use HACS, clone the repo onto the Home Assistant host
and symlink the component into place, from an SSH or web terminal:

```bash
cd /config
git clone --depth 1 https://github.com/iamprem/translink.git translink-src
mkdir -p custom_components
ln -s /config/translink-src/custom_components/translink custom_components/translink
```

To update later:

```bash
cd /config/translink-src && git pull
```

then restart Home Assistant. A fresh `git pull` plus restart is the whole
upgrade path.

The symlink works because Home Assistant's loader accepts any directory entry
under `custom_components/`, and a symlink to a directory satisfies that.

### Manual copy

Copy `custom_components/translink/` into your Home Assistant `config`
directory, so you end up with `config/custom_components/translink/`, then
restart.

On Home Assistant OS, `config/` is reachable over SSH or the file editor; on
Supervised or Container installs it is usually a bind mount such as
`/usr/src/homeassistant/config`.

## What you get

One sensor per configured stop:

- **State** — whole minutes until the next bus, or `unknown` when nothing is
  scheduled.
- **Attributes** — `next_arrival`, `next_scheduled`, `next_is_realtime`,
  `next_delay_seconds`, `headsign`, `destination`, plus an `upcoming` list with
  the same detail for every bus in the window.

Example automation:

```yaml
automation:
  - alias: "Leave for the bus"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.translink_langley_centre_502_direction_1
        below: 6
    actions:
      - action: notify.mobile_app
        data:
          message: >-
            Bus in {{ states('sensor.translink_langley_centre_502_direction_1') }}
            min to {{ state_attr('sensor.translink_langley_centre_502_direction_1', 'headsign') }}
```

## The API

Base: `https://getaway.translink.ca/api`. Endpoints used:

| Purpose | Endpoint | TTL |
| --- | --- | --- |
| Route list (config flow) | `GET /gtfs/routes` | 24 h |
| Stops + timetable (config flow) | `GET /gtfs/route/{r}/direction/{d}/schedules/{YYYY-MM-DD}` | 6 h |
| **Arrivals (the sensor)** | `GET /gtfs/stop/{s}/route/{r}/realtimeschedules?querySize=N` | 25 s |
| Vehicle positions | `GET /gtfs/route/{r}/direction/{d}/vehiclepositions` | 10 s |

Responses are cached in `.storage/translink_http_cache` with ETag
revalidation, and `Accept-Encoding: gzip` is always sent — the timetable
endpoint is 418 KB raw but 49 KB compressed.

### Field notes

Verified against live data, not documentation:

- `ut` is epoch seconds and is **already the predicted arrival**. Use it
  directly. `dt` (`"HH:MM"`) is display text.
- `rt` (per trip) marks a real-time prediction. Without it the arrival is
  timetable-only.
- `dl` is **signed seconds**, predicted minus scheduled. Negative means early.
  The timetable time is recovered as `arrival - dl`.
- `route` is **required** on `realtimeschedules`. Omitting it returns an empty
  list rather than an error.
- `po` / `do` / `no` mean pickup-only / drop-off-only / node-only. A node-only
  stop never boards, so it can never produce a departure.
- `vehiclepositions` carries **no next-stop field**, so a bus's distance to your
  stop cannot be derived from it. That is why this integration ships a sensor
  rather than a `device_tracker`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install httpx pytest
.venv/bin/pytest tests -q -m "not live"   # offline parser + cache tests
.venv/bin/pytest tests -q -m live         # hits the real API
```

`translink_client.py` and `const.py` deliberately avoid importing Home
Assistant so they can be tested without an HA install; `tests/conftest.py`
loads them standalone. The coordinator, sensor and config flow are **not**
covered by these tests — they need `pytest-homeassistant-custom-component`.

## Limitations

- This API is undocumented and unsupported. Translink can change it without
  notice. All of it is isolated in `translink_client.py` for that reason.
- British Columbia only.
- No vehicle-position entity yet, for the reason above.
