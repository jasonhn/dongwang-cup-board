import json
from pathlib import Path
from typing import Any

import httpx

from collector import (
    DEFAULT_CONFIG,
    LeagueCollectionError,
    _stable_snapshot_timestamp,
    collect_league_data,
    collect_manual_data,
    collect_snapshot,
    collect_tournament_data,
    find_league_id,
    load_config,
    load_display_names,
    normalise_match,
    read_manual_match_ids,
    write_snapshot,
)
from collector import append_manual_match_ids
from collector import SteamMatchClient
from collector import account_ids_in_snapshot, load_avatar_cache, refresh_avatars


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


def test_add_match_registers_new_ids_and_skips_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "matches.txt"
    path.write_text("# 手动登记的比赛 ID\n9081139900\n", encoding="utf-8")

    added = append_manual_match_ids(path, [9085584594, 9081139900])

    assert added == [9085584594]
    assert read_manual_match_ids(path) == [9081139900, 9085584594]
    assert "# 手动登记的比赛 ID" in path.read_text(encoding="utf-8")

    assert append_manual_match_ids(path, [9085584594]) == []


def test_add_match_creates_the_file_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "matches.txt"

    added = append_manual_match_ids(path, [9085584594])

    assert added == [9085584594]
    assert read_manual_match_ids(path) == [9085584594]


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


def test_read_manual_match_ids_can_allow_empty(tmp_path: Path) -> None:
    path = tmp_path / "matches.txt"
    path.write_text("# 只有注释\n", encoding="utf-8")

    assert read_manual_match_ids(path, require_non_empty=False) == []


def test_both_mode_merges_league_and_manual_matches(tmp_path: Path) -> None:
    manual_file = tmp_path / "matches.txt"
    manual_file.write_text("2\n", encoding="utf-8")
    league_match = make_match(1, league_id=19905)
    league_match["start_time"] = 1_800_000_001
    extra_match = make_match(2, league_id=0)
    extra_match["start_time"] = 1_800_000_002
    config = {
        **DEFAULT_CONFIG,
        "collect_mode": "both",
        "league_id": 19905,
        "manual_match_file": str(manual_file),
    }
    client = FakeClient(
        {
            "/leagues/19905": {"name": "懂王杯"},
            "/leagues/19905/matchIds": [1],
            "/matches/1": league_match,
            "/matches/2": extra_match,
        }
    )

    payload = collect_snapshot(
        config=config, client=client, output_path=tmp_path / "league.json"  # type: ignore[arg-type]
    )

    assert payload["collection_mode"] == "both"
    assert [match["match_id"] for match in payload["matches"]] == [1, 2]
    assert payload["league"]["name"] == "懂王杯"
    assert payload["metadata"]["match_ids_returned"] == 2


def test_both_mode_does_not_fetch_duplicates_twice(tmp_path: Path) -> None:
    manual_file = tmp_path / "matches.txt"
    manual_file.write_text("1\n", encoding="utf-8")
    config = {
        **DEFAULT_CONFIG,
        "collect_mode": "both",
        "league_id": 19905,
        "manual_match_file": str(manual_file),
    }
    client = FakeClient(
        {
            "/leagues/19905": {"name": "懂王杯"},
            "/leagues/19905/matchIds": [1],
            "/matches/1": make_match(1, league_id=19905),
        }
    )

    payload = collect_snapshot(
        config=config, client=client, output_path=tmp_path / "league.json"  # type: ignore[arg-type]
    )

    assert [match["match_id"] for match in payload["matches"]] == [1]
    assert client.requested.count("/matches/1") == 1


def test_both_mode_requires_a_league_id(tmp_path: Path) -> None:
    config = {**DEFAULT_CONFIG, "collect_mode": "both", "league_id": 0}
    client = FakeClient({})

    try:
        collect_snapshot(
            config=config, client=client, output_path=tmp_path / "league.json"  # type: ignore[arg-type]
        )
    except LeagueCollectionError as error:
        assert "league_id is required" in str(error)
    else:
        raise AssertionError("expected LeagueCollectionError")


class AvatarClient:
    """Serves /players/<id> for the ids it knows and fails for the rest."""

    paid_calls = 0
    free_exhausted = False

    def __init__(self, profiles: dict[int, str | None]) -> None:
        self.profiles = profiles
        self.requested: list[str] = []

    def get_json(self, path: str) -> Any:
        self.requested.append(path)
        account_id = int(path.removeprefix("/players/"))
        if account_id not in self.profiles:
            raise LeagueCollectionError(f"OpenDota returned 404 for {path}")
        avatar = self.profiles[account_id]
        return {"profile": {"avatarfull": avatar} if avatar is not None else {}}


def avatar_config(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    return {
        **DEFAULT_CONFIG,
        "avatars_file": str(tmp_path / "avatars.json"),
        **overrides,
    }


def test_refresh_avatars_caches_urls_and_known_gaps(tmp_path: Path) -> None:
    payload = {"matches": [normalise_match(make_match(1), 19224)]}
    client = AvatarClient({1000: "https://cdn.example/1000.jpg", 1001: None})

    path, total, fetched = refresh_avatars(
        config=avatar_config(tmp_path), client=client, payload=payload  # type: ignore[arg-type]
    )

    cached = json.loads(path.read_text(encoding="utf-8"))
    assert cached["1000"] == "https://cdn.example/1000.jpg"
    assert cached["1001"] == ""
    assert total == 10
    assert fetched == 10
    # Players without a profile are recorded once so later runs skip them.
    assert client.requested == [f"/players/{account_id}" for account_id in range(1000, 1010)]

    second_client = AvatarClient({1000: "https://cdn.example/1000.jpg"})
    _, total_after, fetched_after = refresh_avatars(
        config=avatar_config(tmp_path), client=second_client, payload=payload  # type: ignore[arg-type]
    )
    assert second_client.requested == []
    assert fetched_after == 0
    assert total_after == 10
    assert load_avatar_cache(path) == cached


def test_refresh_avatars_caps_new_lookups(tmp_path: Path) -> None:
    payload = {"matches": [normalise_match(make_match(1), 19224)]}
    client = AvatarClient({1000: "https://cdn.example/1000.jpg"})

    path, total, fetched = refresh_avatars(
        config=avatar_config(tmp_path, max_new_avatars=3),
        client=client,  # type: ignore[arg-type]
        payload=payload,
    )

    assert fetched == 3
    assert total == 3
    assert client.requested == ["/players/1000", "/players/1001", "/players/1002"]
    assert sorted(json.loads(path.read_text(encoding="utf-8"))) == ["1000", "1001", "1002"]


def test_account_ids_in_snapshot_skips_anonymous_slots() -> None:
    match = normalise_match(make_match(1), 19224)
    match["players"][0]["account_id"] = None
    match["players"].append({"account_id": None, "player_slot": 128})

    assert account_ids_in_snapshot({"matches": [match]}) == list(range(1001, 1010))


def test_load_config_rejects_non_numeric_avatar_budget(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"max_new_avatars": "lots"}), encoding="utf-8")

    try:
        load_config(path)
    except LeagueCollectionError as error:
        assert "max_new_avatars" in str(error)
    else:
        raise AssertionError("expected LeagueCollectionError")


def steam_transport(pages: list[list[int]]) -> httpx.MockTransport:
    """Serves GetMatchHistory pages newest-first, paging downward like Valve does."""
    state = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "KEY"
        assert request.url.params["league_id"] == "19905"
        index = state["page"]
        state["page"] += 1
        matches = pages[index] if index < len(pages) else []
        return httpx.Response(
            200,
            json={"result": {"status": "SUCCESS", "matches": [{"match_id": m} for m in matches]}},
        )

    return httpx.MockTransport(handler)


def test_steam_client_pages_through_the_league_history() -> None:
    with SteamMatchClient(
        api_key="KEY", transport=steam_transport([[30, 20, 10], [9, 4], []])
    ) as client:
        ids = client.tournament_match_ids(19905, max_matches=10)

    assert ids == [4, 9, 10, 20, 30]


def test_steam_client_respects_the_match_cap() -> None:
    with SteamMatchClient(api_key="KEY", transport=steam_transport([[50, 40, 30, 20, 10]])) as client:
        ids = client.tournament_match_ids(19905, max_matches=3)

    # The cap keeps the newest games, not the oldest ones.
    assert ids == [30, 40, 50]


def test_steam_client_requires_a_key() -> None:
    try:
        SteamMatchClient(api_key="")
    except LeagueCollectionError as error:
        assert "STEAM_WEB_API_KEY" in str(error)
    else:
        raise AssertionError("expected LeagueCollectionError")


def test_steam_client_reports_a_bad_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    with SteamMatchClient(api_key="NOPE", transport=httpx.MockTransport(handler)) as client:
        try:
            client.tournament_match_ids(19905, max_matches=10)
        except LeagueCollectionError as error:
            assert "rejected the Web API key" in str(error)
        else:
            raise AssertionError("expected LeagueCollectionError")


class FakeSteamClient:
    def __init__(self, ids: list[int]) -> None:
        self.ids = ids
        self.calls: list[tuple[int, int]] = []

    def tournament_match_ids(self, league_id: int, *, max_matches: int) -> list[int]:
        self.calls.append((league_id, max_matches))
        return self.ids[:max_matches]


def test_tournament_mode_merges_valve_ids_with_the_manual_list(tmp_path: Path) -> None:
    manual_file = tmp_path / "matches.txt"
    manual_file.write_text("9081139900\n", encoding="utf-8")
    config = {
        **DEFAULT_CONFIG,
        "collect_mode": "tournament",
        "league_id": 19905,
        "manual_match_file": str(manual_file),
        "max_tournament_matches": 5,
    }
    tournament_game = make_match(9085584594, league_id=0)
    tournament_game["start_time"] = 1_800_000_500
    older = make_match(9081139900, league_id=0)
    older["start_time"] = 1_800_000_100
    client = FakeClient(
        {
            "/leagues/19905": {"name": "懂王杯", "tier": "excluded"},
            "/matches/9085584594": tournament_game,
            "/matches/9081139900": older,
        }
    )
    steam = FakeSteamClient([9085584594])

    payload = collect_tournament_data(
        config=config,
        steam_client=steam,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        output_path=tmp_path / "league.json",
    )

    assert steam.calls == [(19905, 5)]
    assert payload["collection_mode"] == "tournament"
    assert payload["league"]["name"] == "懂王杯"
    assert [match["match_id"] for match in payload["matches"]] == [9081139900, 9085584594]
    assert payload["metadata"]["match_ids_returned"] == 2
    # Valve-listed games keep the configured league id even when OpenDota reports 0.
    assert payload["matches"][1]["league_id"] == 19905


def test_tournament_mode_needs_a_steam_client(tmp_path: Path) -> None:
    config = {**DEFAULT_CONFIG, "collect_mode": "tournament", "league_id": 19905}

    try:
        collect_snapshot(
            config=config,
            client=FakeClient({}),  # type: ignore[arg-type]
            output_path=tmp_path / "league.json",
        )
    except LeagueCollectionError as error:
        assert "STEAM_WEB_API_KEY" in str(error)
    else:
        raise AssertionError("expected LeagueCollectionError")


def test_tournament_mode_keeps_matches_already_in_the_snapshot(tmp_path: Path) -> None:
    output = tmp_path / "league.json"
    old = make_match(9081139900, league_id=19905)
    old["start_time"] = 1_800_000_100
    write_snapshot(output, {"league_id": 19905, "matches": [old]})
    config = {
        **DEFAULT_CONFIG,
        "collect_mode": "tournament",
        "league_id": 19905,
        "refresh_recent": 1,
        "manual_match_file": str(tmp_path / "no-matches.txt"),
    }
    fresh = make_match(9085584594, league_id=19905)
    fresh["start_time"] = 1_800_000_500
    client = FakeClient(
        {
            "/leagues/19905": {"name": "懂王杯"},
            "/matches/9085584594": fresh,
        }
    )

    payload = collect_tournament_data(
        config=config,
        steam_client=FakeSteamClient([9085584594]),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        output_path=output,
    )

    assert [match["match_id"] for match in payload["matches"]] == [9081139900, 9085584594]
    # The cached match was reused, not re-fetched.
    assert "/matches/9081139900" not in client.requested


def test_normalise_match_treats_league_zero_as_unknown_only_when_allowed() -> None:
    raw = make_match(5, league_id=0)

    accepted = normalise_match(raw, 19905, accept_unknown_league=True)
    assert accepted["league_id"] == 19905

    try:
        normalise_match(raw, 19905)
    except LeagueCollectionError as error:
        assert "belongs to league 0" in str(error)
    else:
        raise AssertionError("expected LeagueCollectionError")


def test_load_config_accepts_tournament_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"collect_mode": "tournament", "max_tournament_matches": 40}),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config["collect_mode"] == "tournament"
    assert config["max_tournament_matches"] == 40
