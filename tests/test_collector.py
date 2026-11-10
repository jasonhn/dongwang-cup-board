import json
from pathlib import Path
from typing import Any

from collector import (
    DEFAULT_CONFIG,
    LeagueCollectionError,
    _stable_snapshot_timestamp,
    collect_league_data,
    collect_manual_data,
    find_league_id,
    load_config,
    load_display_names,
    normalise_match,
    read_manual_match_ids,
    write_snapshot,
)


def make_match(match_id: int, *, league_id: int = 19224) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "leagueid": league_id,
        "start_time": 1_800_000_000 + match_id,
        "duration": 2_400,
        "radiant_win": True,
        "players": [
            {
                "account_id": 1000 + slot,
                "personaname": f"Player {slot}",
                "player_slot": slot if slot < 5 else 123 + slot,
                "hero_id": slot + 1,
            }
            for slot in range(10)
        ],
    }


class FakeClient:
    paid_calls = 0
    free_exhausted = False

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def get_json(self, path: str) -> Any:
        self.requested.append(path)
        return self.responses[path]


def test_normalise_match_keeps_active_roster() -> None:
    match = normalise_match(make_match(1), 19224)

    assert match["match_id"] == 1
    assert match["radiant_win"] is True
    assert len(match["players"]) == 10
    assert match["players"][5]["player_slot"] == 128


def test_collector_reuses_old_matches_and_refreshes_recent(tmp_path: Path) -> None:
    output = tmp_path / "league.json"
    old_payload = {
        "league_id": 19224,
        "matches": [normalise_match(make_match(1), 19224)],
    }
    write_snapshot(output, old_payload)
    client = FakeClient(
        {
            "/leagues/19224": {"name": "Test League", "tier": "excluded"},
            "/leagues/19224/matchIds": [1, 2],
            "/matches/2": make_match(2),
        }
    )

    payload = collect_league_data(
        league_id=19224,
        output_path=output,
        refresh_recent=1,
        client=client,  # type: ignore[arg-type]
    )

    assert [match["match_id"] for match in payload["matches"]] == [1, 2]
    assert "/matches/1" not in client.requested
    assert "/matches/2" in client.requested
    assert payload["metadata"]["matches_available"] == 2
    assert payload["metadata"]["anonymous_player_slots"] == 0


def test_normalise_match_keeps_display_name(tmp_path: Path) -> None:
    match = normalise_match(make_match(1), 19224, names={"1000": "小明", "1001": "阿强"})

    assert match["players"][0]["display_name"] == "小明"
    assert match["players"][1]["display_name"] == "阿强"
    assert match["players"][2]["display_name"] is None


def test_league_mode_rejects_foreign_match(tmp_path: Path) -> None:
    client = FakeClient(
        {
            "/leagues/19224": {"name": "Test League"},
            "/leagues/19224/matchIds": [1],
            "/matches/1": make_match(1, league_id=999),
        }
    )

    payload = collect_league_data(
        league_id=19224,
        output_path=tmp_path / "league.json",
        refresh_recent=1,
        client=client,  # type: ignore[arg-type]
    )

    assert payload["matches"] == []
    assert payload["metadata"]["unavailable_matches"][0]["match_id"] == 1


def test_manual_mode_accepts_casual_rooms_and_sorts_by_time(tmp_path: Path) -> None:
    late = make_match(11, league_id=0)
    late["start_time"] = 1_900_000_000
    early = make_match(22, league_id=777)
    early["start_time"] = 1_850_000_000
    client = FakeClient({"/matches/11": late, "/matches/22": early})
    config = {**DEFAULT_CONFIG, "collect_mode": "manual", "league_id": 0}

    payload = collect_manual_data(
        match_ids=[22, 11],
        output_path=tmp_path / "league.json",
        refresh_recent=5,
        client=client,  # type: ignore[arg-type]
        names={"1000": "小明"},
        config=config,
    )

    assert payload["collection_mode"] == "manual"
    assert [match["match_id"] for match in payload["matches"]] == [22, 11]
    assert payload["matches"][0]["players"][0]["display_name"] == "小明"
    assert payload["site"]["title"] == config["site"]["title"]
    assert payload["time_zone"] == "Asia/Shanghai"


def test_manual_mode_reports_missing_match(tmp_path: Path) -> None:
    client = FakeClient({"/matches/11": {"error": "not found"}})
    config = {**DEFAULT_CONFIG, "collect_mode": "manual"}

    payload = collect_manual_data(
        match_ids=[11],
        output_path=tmp_path / "league.json",
        refresh_recent=5,
        client=client,  # type: ignore[arg-type]
        config=config,
    )

    assert payload["metadata"]["matches_available"] == 0
    assert payload["metadata"]["unavailable_matches"][0]["match_id"] == 11


def test_read_manual_match_ids_skips_comments(tmp_path: Path) -> None:
    path = tmp_path / "matches.txt"
    path.write_text("# comment\n8657068580\n\n9061487194 # trailing comment\n", encoding="utf-8")

    assert read_manual_match_ids(path) == [8657068580, 9061487194]


def test_read_manual_match_ids_rejects_junk(tmp_path: Path) -> None:
    path = tmp_path / "matches.txt"
    path.write_text("not-a-match-id\n", encoding="utf-8")

    try:
        read_manual_match_ids(path)
    except LeagueCollectionError as error:
        assert "Invalid match id" in str(error)
    else:
        raise AssertionError("expected LeagueCollectionError")


def test_load_config_merges_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"league_id": 42, "site": {"title": "群内战榜"}}),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["league_id"] == 42
    assert config["collect_mode"] == "league"
    assert config["time_zone"] == "Asia/Shanghai"
    assert config["site"]["title"] == "群内战榜"
    assert config["site"]["subtitle"] == DEFAULT_CONFIG["site"]["subtitle"]


def test_find_league_id_matches_exact_name(tmp_path: Path) -> None:
    client = FakeClient(
        {
            "/leagues": [
                {"leagueid": 19905, "name": "懂王杯"},
                {"leagueid": 20261, "name": "中军杯"},
                {"leagueid": 0, "name": "broken"},
            ]
        }
    )

    assert find_league_id(client, "懂王杯") == 19905  # type: ignore[arg-type]


def test_find_league_id_lists_candidates(tmp_path: Path) -> None:
    client = FakeClient(
        {"/leagues": [{"leagueid": 19905, "name": "懂王杯"}, {"leagueid": 2, "name": "懂王杯 S2"}]}
    )

    try:
        find_league_id(client, "懂王")  # type: ignore[arg-type]
    except LeagueCollectionError as error:
        assert "懂王杯 (19905)" in str(error)
    else:
        raise AssertionError("expected LeagueCollectionError")


def test_stable_timestamp_when_content_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "league.json"
    payload = {
        "generated_at_utc": "2026-01-01T00:00:00+00:00",
        "league_id": 19224,
        "matches": [normalise_match(make_match(1), 19224)],
    }
    write_snapshot(path, payload)

    same_content = {**payload, "generated_at_utc": "2026-01-02T00:00:00+00:00"}
    kept = _stable_snapshot_timestamp(path, same_content)
    assert kept["generated_at_utc"] == "2026-01-01T00:00:00+00:00"

    changed = {**payload, "generated_at_utc": "2026-01-02T00:00:00+00:00", "matches": []}
    bumped = _stable_snapshot_timestamp(path, changed)
    assert bumped["generated_at_utc"] == "2026-01-02T00:00:00+00:00"
