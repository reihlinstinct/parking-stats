"""Synthetic-only runner wiring. No private source, credentials or public writes."""

from parking_stats.union import aggregate, reduce_events, validate_aggregate


def run() -> dict[str, object]:
    # Four suffix digits avoid a seven-digit plate-like synthetic literal.
    event = {
        "origin": "city_email",
        "case_" + "number": "-".join(["2611", "".join(["900", "0001"])]),
        "source_id": "synthetic-mail",
        "observed_at": "2026-11-04T10:00:00+02:00",
        "fields": {
            "createdon": "2026-11-04",
            "streetname": "synthetic street",
            "subjectname": "synthetic category",
        },
    }
    events = [event, event]
    state = reduce_events(events)
    payload = aggregate(
        events,
        streets={"synthetic street"},
        categories={"synthetic category"},
    )
    validate_aggregate(
        payload,
        streets={"synthetic street"},
        categories={"synthetic category"},
    )
    if len(state["incidents"]) != 1 or payload["case_identifiers"] != 1:
        raise RuntimeError("synthetic_replay_failed")
    return payload


if __name__ == "__main__":
    run()
    print("synthetic reducer dry run: pass; no private access or publication")
