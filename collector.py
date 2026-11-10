from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

API_BASE_URL = "https://api.opendota.com/api"
DEFAULT_OUTPUT = Path("site/data/league.json")
DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_TIME_ZONE = "Asia/Shanghai"

DEFAULT_CONFIG: dict[str, Any] = {
    "collect_mode": "league",
    "league_id": 0,
    "manual_match_file": "matches.txt",
    "refresh_recent": 5,
    "output": str(DEFAULT_OUTPUT),
    "time_zone": DEFAULT_TIME_ZONE,
    "names_file": "names.json",
    "site": {
        "title": "内战光荣榜",
        "subtitle": "Dota 2 内战战绩",
        "kicker": "内战有喜 · 荣耀登榜",
        "footer": "内战光荣榜 · 胜负皆尽兴，开黑常有喜",
        "description": "Dota 2 内战战绩看板：按时间筛选玩家胜负与组队胜率。",
    },
}


class LeagueCollectionError(RuntimeError):
    """Raised when a league snapshot cannot be collected safely."""


class OpenDotaLeagueClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        max_paid_calls: int = 250,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key or None
        self._max_paid_calls = max_paid_calls
        self._paid_calls = 0
        self._free_exhausted = False
        self._client = httpx.Client(
            base_url=API_BASE_URL,
            headers={"User-Agent": "ustc-dota2-hall-of-fame/1.0"},
            timeout=timeout_seconds,
            transport=transport,
        )

    @property
    def paid_calls(self) -> int:
        return self._paid_calls

    @property
    def free_exhausted(self) -> bool:
        return self._free_exhausted

    def __enter__(self) -> OpenDotaLeagueClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip().lower()
        if isinstance(payload, dict):
            return str(payload.get("error") or payload.get("message") or payload).lower()
        return str(payload).lower()

    def _sleep_until_next_minute(self) -> None:
        remaining = 61.0 - (time.time() % 60.0)
        time.sleep(max(2.0, remaining) + random.uniform(0.2, 1.2))

    def _request(self, path: str, *, paid: bool) -> Any:
        headers: dict[str, str] = {}
        if paid:
            if not self._api_key:
                raise LeagueCollectionError(
                    "OpenDota anonymous daily quota was exhausted and no API key is configured"
                )
            if self._paid_calls >= self._max_paid_calls:
                raise LeagueCollectionError(
                    f"OpenDota paid-call safety limit reached ({self._max_paid_calls})"
                )
            headers["Authorization"] = f"Bearer {self._api_key}"

        for attempt in range(4):
            if paid:
                self._paid_calls += 1
            try:
                response = self._client.get(path, headers=headers)
            except httpx.RequestError as error:
                if attempt == 3:
                    raise LeagueCollectionError(
                        f"OpenDota request failed for {path}: {error}"
                    ) from error
                time.sleep(2**attempt + random.uniform(0.1, 0.8))
                continue

            message = self._error_message(response)
            if response.status_code == 429 and "minute rate limit exceeded" in message:
                if attempt == 3:
                    raise LeagueCollectionError(f"OpenDota minute rate limit persisted for {path}")
                self._sleep_until_next_minute()
                continue
            if response.status_code == 429 and "daily api limit exceeded" in message:
                if paid:
                    raise LeagueCollectionError("OpenDota API-key daily limit was exhausted")
                self._free_exhausted = True
                return self._request(path, paid=True)
            if response.status_code >= 500:
                if attempt == 3:
                    raise LeagueCollectionError(
                        f"OpenDota server error {response.status_code} for {path}"
                    )
                time.sleep(2**attempt + random.uniform(0.1, 0.8))
                continue
            if response.status_code >= 400:
                raise LeagueCollectionError(
                    f"OpenDota returned {response.status_code} for {path}: {message}"
                )
            try:
                return response.json()
            except ValueError as error:
                raise LeagueCollectionError(f"OpenDota returned invalid JSON for {path}") from error

        raise AssertionError("unreachable")

    def get_json(self, path: str) -> Any:
        return self._request(path, paid=self._free_exhausted)


def normalise_match(
    raw: dict[str, Any],
    league_id: int | None = None,
    *,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    match_id = int(raw.get("match_id") or 0)
    actual_league_id = int(raw.get("leagueid") or 0)
    start_time = int(raw.get("start_time") or 0)
    duration = int(raw.get("duration") or 0)
    radiant_win = raw.get("radiant_win")
    players = raw.get("players")

    if match_id <= 0 or start_time <= 0 or duration < 0:
        raise LeagueCollectionError(f"Match {match_id or '<unknown>'} has invalid core fields")
    if league_id is not None and actual_league_id != league_id:
        raise LeagueCollectionError(
            f"Match {match_id} belongs to league {actual_league_id}, expected {league_id}"
        )
    if not isinstance(radiant_win, bool):
        raise LeagueCollectionError(f"Match {match_id} has no scored winner")
    if not isinstance(players, list):
        raise LeagueCollectionError(f"Match {match_id} has no player roster")

    normalised_players: list[dict[str, Any]] = []
    for player in players:
        if not isinstance(player, dict):
            continue
        player_slot = player.get("player_slot")
        if not isinstance(player_slot, int):
            continue
        active_slot = 0 <= player_slot <= 4 or 128 <= player_slot <= 132
        if not active_slot:
            continue
        account_id = player.get("account_id")
        resolved_account_id = (
            int(account_id) if isinstance(account_id, int) and account_id > 0 else None
        )
        normalised_players.append(
            {
                "account_id": resolved_account_id,
                "personaname": str(player.get("personaname") or "").strip() or None,
                "display_name": (
                    (names or {}).get(str(resolved_account_id)) if resolved_account_id else None
                ),
                "player_slot": player_slot,
                "hero_id": int(player.get("hero_id") or 0),
            }
        )

    return {
        "match_id": match_id,
        "league_id": actual_league_id,
        "start_time": start_time,
        "duration": duration,
        "radiant_win": radiant_win,
        "players": normalised_players,
    }


def _load_existing(path: Path, league_id: int) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if int(payload.get("league_id") or 0) != league_id:
        return {}
    result: dict[int, dict[str, Any]] = {}
    for match in payload.get("matches", []):
        if isinstance(match, dict) and int(match.get("match_id") or 0) > 0:
            result[int(match["match_id"])] = match
    return result


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LeagueCollectionError(f"Config file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LeagueCollectionError(f"Config file {path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise LeagueCollectionError(f"Config file {path} must be a JSON object")

    config = {**DEFAULT_CONFIG, **raw}
    site = raw.get("site")
    config["site"] = {**DEFAULT_CONFIG["site"], **(site if isinstance(site, dict) else {})}

    mode = str(config.get("collect_mode") or "league").strip().lower()
    if mode not in {"league", "manual"}:
        raise LeagueCollectionError(
            f"collect_mode must be 'league' or 'manual', got {raw.get('collect_mode')!r}"
        )
    config["collect_mode"] = mode

    try:
        config["league_id"] = int(config.get("league_id") or 0)
        config["refresh_recent"] = max(0, int(config.get("refresh_recent") or 0))
    except (TypeError, ValueError) as error:
        raise LeagueCollectionError(f"config.json has a non-numeric field: {error}") from error
    config["output"] = Path(config.get("output") or DEFAULT_OUTPUT)
    config["time_zone"] = str(config.get("time_zone") or DEFAULT_TIME_ZONE)
    return config


def load_display_names(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LeagueCollectionError(f"Names file {path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise LeagueCollectionError(
            f"Names file {path} must be a JSON object of account id -> name"
        )

    names: dict[str, str] = {}
    for key, value in raw.items():
        name = str(value).strip()
        if not name:
            continue
        try:
            account_id = int(str(key).strip())
        except ValueError:
            raise LeagueCollectionError(
                f"Names file {path} has a non-numeric account id: {key!r}"
            ) from None
        names[str(account_id)] = name[:40]
    return names


def read_manual_match_ids(path: Path) -> list[int]:
    if not path.exists():
        raise LeagueCollectionError(f"Manual match file not found: {path}")
    match_ids: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip().replace(",", "")
        if not entry:
            continue
        try:
            match_id = int(entry)
        except ValueError:
            raise LeagueCollectionError(f"Invalid match id {entry!r} in {path}") from None
        if match_id > 0:
            match_ids.add(match_id)
    if not match_ids:
        raise LeagueCollectionError(f"No match id found in {path}")
    return sorted(match_ids)


def find_league_id(client: OpenDotaLeagueClient, name: str) -> int:
    """Resolve a league name to a league id using OpenDota's league listing."""
    wanted = name.strip()
    if not wanted:
        raise LeagueCollectionError("League name is empty")
    rows = client.get_json("/leagues")
    if not isinstance(rows, list):
        raise LeagueCollectionError("OpenDota returned an invalid league listing")

    by_name: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        league_name = str(row.get("name") or "").strip()
        league_id = int(row.get("leagueid") or 0)
        if league_id > 0 and league_name:
            by_name.setdefault(league_name, league_id)

    if wanted in by_name:
        return by_name[wanted]

    candidates = [
        (league_name, league_id)
        for league_name, league_id in by_name.items()
        if wanted.lower() in league_name.lower()
    ]
    if not candidates:
        raise LeagueCollectionError(f"No league found for name {name!r}")
    preview = ", ".join(f"{league_name} ({league_id})" for league_name, league_id in candidates[:8])
    raise LeagueCollectionError(
        f"Name {name!r} is not an exact league name. Candidates: {preview}"
    )


def _resolve_config(
    config: dict[str, Any],
    league_id: int | None,
    output_path: Path | None,
    refresh_recent: int | None,
) -> dict[str, Any]:
    resolved = dict(config)
    if league_id is not None:
        resolved["league_id"] = int(league_id)
    if output_path is not None:
        resolved["output"] = output_path
    if refresh_recent is not None:
        resolved["refresh_recent"] = max(0, int(refresh_recent))
    return resolved


def _apply_names(match: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    for player in match.get("players", []):
        if not isinstance(player, dict):
            continue
        account_id = player.get("account_id")
        player["display_name"] = names.get(str(account_id)) if account_id else None
    return match


def _collect_matches(
    *,
    match_ids: list[int],
    output_path: Path,
    refresh_recent: int,
    client: OpenDotaLeagueClient,
    names: dict[str, str],
    expected_league_id: int | None,
    cache_league_id: int,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    existing = _load_existing(output_path, cache_league_id)
    refresh_ids = set(match_ids[-max(0, refresh_recent) :])
    matches: dict[int, dict[str, Any]] = {}
    unavailable: list[dict[str, Any]] = []

    for match_id in match_ids:
        if match_id in existing and match_id not in refresh_ids:
            matches[match_id] = _apply_names(existing[match_id], names)
            continue
        try:
            raw = client.get_json(f"/matches/{match_id}")
            if not isinstance(raw, dict):
                raise LeagueCollectionError("invalid match payload")
            matches[match_id] = normalise_match(raw, expected_league_id, names=names)
        except LeagueCollectionError as error:
            if match_id in existing:
                matches[match_id] = _apply_names(existing[match_id], names)
            else:
                unavailable.append({"match_id": match_id, "reason": str(error)})
    return matches, unavailable


def _order_matches(matches: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        matches.values(),
        key=lambda match: (int(match.get("start_time") or 0), int(match.get("match_id") or 0)),
    )


def _build_payload(
    *,
    config: dict[str, Any],
    league_info: dict[str, str],
    ordered_matches: list[dict[str, Any]],
    match_ids_returned: int,
    unavailable: list[dict[str, Any]],
    client: OpenDotaLeagueClient,
) -> dict[str, Any]:
    anonymous_slots = sum(
        1
        for match in ordered_matches
        for player in match.get("players", [])
        if player.get("account_id") is None
    )
    incomplete_rosters = [
        int(match["match_id"])
        for match in ordered_matches
        if len(match.get("players", [])) != 10
    ]

    return {
        "schema_version": 2,
        "league_id": int(config["league_id"]),
        "collection_mode": config["collect_mode"],
        "league": league_info,
        "site": config["site"],
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "time_zone": config["time_zone"],
        "source": "OpenDota public API",
        "metadata": {
            "match_ids_returned": match_ids_returned,
            "matches_available": len(ordered_matches),
            "unavailable_matches": unavailable,
            "anonymous_player_slots": anonymous_slots,
            "incomplete_roster_match_ids": incomplete_rosters,
            "paid_calls": client.paid_calls,
            "anonymous_daily_quota_exhausted": client.free_exhausted,
        },
        "matches": ordered_matches,
    }


def collect_league_data(
    *,
    league_id: int,
    output_path: Path,
    refresh_recent: int = 5,
    client: OpenDotaLeagueClient,
    names: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    league = client.get_json(f"/leagues/{league_id}")
    match_ids_raw = client.get_json(f"/leagues/{league_id}/matchIds")
    if not isinstance(league, dict):
        raise LeagueCollectionError("OpenDota returned an invalid league response")
    if not isinstance(match_ids_raw, list):
        raise LeagueCollectionError("OpenDota returned an invalid match-ID list")

    match_ids = sorted({int(match_id) for match_id in match_ids_raw if int(match_id) > 0})
    matches, unavailable = _collect_matches(
        match_ids=match_ids,
        output_path=output_path,
        refresh_recent=refresh_recent,
        client=client,
        names=names or {},
        expected_league_id=league_id,
        cache_league_id=league_id,
    )
    resolved = _resolve_config(config or DEFAULT_CONFIG, league_id, output_path, refresh_recent)

    return _build_payload(
        config=resolved,
        league_info={
            "name": str(league.get("name") or f"League {league_id}"),
            "tier": str(league.get("tier") or ""),
            "banner": str(league.get("banner") or ""),
        },
        ordered_matches=_order_matches(matches),
        match_ids_returned=len(match_ids),
        unavailable=unavailable,
        client=client,
    )


def collect_manual_data(
    *,
    match_ids: list[int],
    output_path: Path,
    refresh_recent: int = 5,
    client: OpenDotaLeagueClient,
    names: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = _resolve_config(config or DEFAULT_CONFIG, None, output_path, refresh_recent)
    matches, unavailable = _collect_matches(
        match_ids=match_ids,
        output_path=output_path,
        refresh_recent=refresh_recent,
        client=client,
        names=names or {},
        expected_league_id=None,
        cache_league_id=int(resolved["league_id"]),
    )

    return _build_payload(
        config=resolved,
        league_info={
            "name": str(resolved["site"].get("title") or "内战手动收录"),
            "tier": "manual",
            "banner": "",
        },
        ordered_matches=_order_matches(matches),
        match_ids_returned=len(match_ids),
        unavailable=unavailable,
        client=client,
    )


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect an OpenDota snapshot for the dashboard"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--league-id", type=int, default=None, help="Override config.json")
    parser.add_argument(
        "--league-name",
        default=None,
        help="Resolve a league name to its id (exact match) and use league mode",
    )
    parser.add_argument(
        "--mode",
        choices=("league", "manual"),
        default=None,
        help="Override config.json collect_mode",
    )
    parser.add_argument("--output", type=Path, default=None, help="Override config.json output")
    parser.add_argument(
        "--refresh-recent", type=int, default=None, help="Override config.json refresh_recent"
    )
    parser.add_argument(
        "--max-paid-calls",
        type=int,
        default=int(os.getenv("OPENDOTA_MAX_PAID_CALLS", "250")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    config = _resolve_config(config, args.league_id, args.output, args.refresh_recent)
    if args.mode is not None:
        config["collect_mode"] = args.mode
    names = load_display_names(Path(config["names_file"]))

    with OpenDotaLeagueClient(
        api_key=os.getenv("OPENDOTA_API_KEY"),
        max_paid_calls=args.max_paid_calls,
    ) as client:
        if args.league_name:
            config["collect_mode"] = "league"
            config["league_id"] = find_league_id(client, args.league_name)
            print(f"Resolved {args.league_name!r} to league id {config['league_id']}")
        if config["collect_mode"] == "manual":
            payload = collect_manual_data(
                match_ids=read_manual_match_ids(Path(config["manual_match_file"])),
                output_path=config["output"],
                refresh_recent=config["refresh_recent"],
                client=client,
                names=names,
                config=config,
            )
        else:
            if not config["league_id"]:
                raise LeagueCollectionError(
                    "league_id is required in league mode (set it in config.json)"
                )
            payload = collect_league_data(
                league_id=config["league_id"],
                output_path=config["output"],
                refresh_recent=config["refresh_recent"],
                client=client,
                names=names,
                config=config,
            )

    write_snapshot(config["output"], payload)
    metadata = payload["metadata"]
    label = (
        f"league {config['league_id']}"
        if config["collect_mode"] == "league"
        else f"manual list ({metadata['match_ids_returned']} ids)"
    )
    print(
        f"{label}: wrote {metadata['matches_available']} matches to {config['output']} "
        f"({len(metadata['unavailable_matches'])} unavailable, "
        f"{metadata['anonymous_player_slots']} anonymous slots, "
        f"{metadata['paid_calls']} paid calls)"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LeagueCollectionError as error:
        print(f"Collection failed: {error}")
        raise SystemExit(1)
