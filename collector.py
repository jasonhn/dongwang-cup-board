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
STEAM_API_BASE_URL = "https://api.steampowered.com/IDOTA2Match_570"
STEAM_KEY_HELP = "https://steamcommunity.com/dev/apikey"
DEFAULT_OUTPUT = Path("site/data/league.json")
DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_TIME_ZONE = "Asia/Shanghai"
DEFAULT_AVATARS_FILE = "site/data/avatars.json"
DEFAULT_OVERRIDES_FILE = "overrides.json"
DEFAULT_MAX_TOURNAMENT_MATCHES = 100
# Valve's solo queue rooms (1v1 mid) carry the league tag too, but they are not
# 内战, so the board ignores those game modes unless the config says otherwise.
DEFAULT_EXCLUDE_GAME_MODES = [21]

DEFAULT_CONFIG: dict[str, Any] = {
    "collect_mode": "league",
    "league_id": 0,
    "manual_match_file": "matches.txt",
    "refresh_recent": 5,
    "output": str(DEFAULT_OUTPUT),
    "time_zone": DEFAULT_TIME_ZONE,
    "names_file": "names.json",
    "avatars_file": DEFAULT_AVATARS_FILE,
    "max_new_avatars": 80,
    # Matches typed in by hand, for games no public API can see (e.g. 完美世界/国服 servers).
    "overrides_file": DEFAULT_OVERRIDES_FILE,
    # Games the league aggregates but the board should ignore.
    "exclude_game_modes": DEFAULT_EXCLUDE_GAME_MODES,
    "exclude_match_ids": [],
    "max_tournament_matches": DEFAULT_MAX_TOURNAMENT_MATCHES,
    # Only ever read from the environment; never commit a key to this file.
    "steam_web_api_key": "",
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


class SteamMatchClient:
    """Valve's official Dota 2 match service, which knows the league's own games.

    OpenDota only aggregates leagues it tracks; an amateur league can be marked
    `excluded` and then report zero matches. Valve answers the same question if you
    have a (free) Steam Web API key, so tournament mode reads the league from Valve
    and lets OpenDota parse each match on demand.
    """

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LeagueCollectionError(
                "tournament mode needs STEAM_WEB_API_KEY "
                f"(get a free key at {STEAM_KEY_HELP} and put it in .env)"
            )
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=STEAM_API_BASE_URL,
            headers={"User-Agent": "ustc-dota2-hall-of-fame/1.0"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> SteamMatchClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.get(path, params={**params, "api_key": self._api_key})
        except httpx.RequestError as error:
            raise LeagueCollectionError(f"Steam request failed for {path}: {error}") from error
        if response.status_code in (401, 403):
            raise LeagueCollectionError(
                f"Steam rejected the Web API key ({response.status_code}); "
                f"check it at {STEAM_KEY_HELP}"
            )
        if response.status_code >= 400:
            raise LeagueCollectionError(f"Steam returned {response.status_code} for {path}")
        try:
            payload = response.json()
        except ValueError as error:
            raise LeagueCollectionError(f"Steam returned invalid JSON for {path}") from error
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise LeagueCollectionError("Steam returned a response without a result object")
        error = result.get("error")
        if error:
            raise LeagueCollectionError(f"Steam API error for {path}: {error}")
        return result

    def tournament_match_ids(self, league_id: int, *, max_matches: int) -> list[int]:
        """Every match Valve lists for this league, oldest first, capped at max_matches."""
        collected: set[int] = set()
        cursor: int | None = None
        for _ in range(20):
            params: dict[str, Any] = {"league_id": int(league_id)}
            if cursor is not None:
                params["start_at_match_id"] = cursor
            page = self._get("/GetMatchHistory/v1", params).get("matches")
            if not isinstance(page, list) or not page:
                break
            ids = [
                int(row.get("match_id") or 0)
                for row in page
                if isinstance(row, dict) and int(row.get("match_id") or 0) > 0
            ]
            if not ids:
                break
            collected.update(ids)
            if len(collected) >= max_matches:
                break
            # Valve pages downward, so the smallest id on the page is the next cursor.
            next_cursor = min(ids)
            if cursor is not None and next_cursor >= cursor:
                break
            cursor = next_cursor
        # Keep the newest games when the league has more than the cap.
        return sorted(collected)[-max_matches:]


def normalise_match(
    raw: dict[str, Any],
    league_id: int | None = None,
    *,
    names: dict[str, str] | None = None,
    accept_unknown_league: bool = False,
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
        # Valve reports tournament games that OpenDota sometimes stores without a
        # leagueid; treat 0 as "unknown" instead of rejecting the whole match.
        if accept_unknown_league and actual_league_id == 0:
            actual_league_id = league_id
        else:
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
        # Kept so the exclusions below also work on matches already in the snapshot.
        "game_mode": int(raw.get("game_mode") or 0),
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


def _int_list(value: Any, field: str, default: list[int]) -> list[int]:
    """Read a list-of-numbers config field into a sorted, de-duplicated list."""
    if value is None:
        value = default
    if not isinstance(value, list):
        raise LeagueCollectionError(f"config.json field {field} must be a list of numbers")
    try:
        return sorted({int(item) for item in value})
    except (TypeError, ValueError) as error:
        raise LeagueCollectionError(
            f"config.json field {field} has a non-numeric entry: {error}"
        ) from error


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
    if mode not in {"league", "manual", "both", "tournament"}:
        raise LeagueCollectionError(
            "collect_mode must be 'league', 'manual', 'both' or 'tournament', "
            f"got {raw.get('collect_mode')!r}"
        )
    config["collect_mode"] = mode

    try:
        config["league_id"] = int(config.get("league_id") or 0)
        config["refresh_recent"] = max(0, int(config.get("refresh_recent") or 0))
    except (TypeError, ValueError) as error:
        raise LeagueCollectionError(f"config.json has a non-numeric field: {error}") from error
    config["output"] = Path(config.get("output") or DEFAULT_OUTPUT)
    config["time_zone"] = str(config.get("time_zone") or DEFAULT_TIME_ZONE)
    config["avatars_file"] = str(config.get("avatars_file") or DEFAULT_AVATARS_FILE)
    try:
        config["max_new_avatars"] = max(0, int(config.get("max_new_avatars") or 0))
    except (TypeError, ValueError) as error:
        raise LeagueCollectionError(
            f"config.json has a non-numeric max_new_avatars field: {error}"
        ) from error
    try:
        config["max_tournament_matches"] = max(
            1, int(config.get("max_tournament_matches") or DEFAULT_MAX_TOURNAMENT_MATCHES)
        )
    except (TypeError, ValueError) as error:
        raise LeagueCollectionError(
            f"config.json has a non-numeric max_tournament_matches field: {error}"
        ) from error
    config["exclude_game_modes"] = _int_list(
        config.get("exclude_game_modes"), "exclude_game_modes", DEFAULT_EXCLUDE_GAME_MODES
    )
    config["exclude_match_ids"] = _int_list(
        config.get("exclude_match_ids"), "exclude_match_ids", []
    )
    config["steam_web_api_key"] = str(config.get("steam_web_api_key") or "").strip()
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


def load_avatar_cache(path: Path) -> dict[str, str]:
    """Load previously fetched avatar URLs; "" means "no avatar, do not retry"."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}


def collect_avatars(
    *,
    client: OpenDotaLeagueClient,
    account_ids: list[int],
    cache: dict[str, str],
    max_new: int = 80,
) -> tuple[dict[str, str], int]:
    """Fetch Steam avatar URLs through OpenDota, reusing the cached results.

    Players without an OpenDota profile are recorded as "" so we do not spend
    quota retrying them on every run.
    """
    avatars = dict(cache)
    fetched = 0
    for account_id in sorted({int(value) for value in account_ids if int(value) > 0}):
        key = str(account_id)
        if key in avatars:
            continue
        if fetched >= max_new:
            break
        fetched += 1
        try:
            payload = client.get_json(f"/players/{account_id}")
        except LeagueCollectionError:
            avatars[key] = ""
            continue
        profile = payload.get("profile") if isinstance(payload, dict) else None
        if not isinstance(profile, dict):
            avatars[key] = ""
            continue
        url = profile.get("avatarfull") or profile.get("avatar")
        avatars[key] = url if isinstance(url, str) and url.startswith("http") else ""
    return avatars, fetched


def write_avatars(path: Path, avatars: dict[str, str]) -> bool:
    payload = json.dumps(
        {key: avatars[key] for key in sorted(avatars)}, ensure_ascii=False, indent=2
    ) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
    return True


def account_ids_in_snapshot(payload: dict[str, Any]) -> list[int]:
    """Every positive account id that appears in the snapshot roster."""
    account_ids: set[int] = set()
    for match in payload.get("matches", []):
        if not isinstance(match, dict):
            continue
        for player in match.get("players", []):
            if not isinstance(player, dict):
                continue
            account_id = player.get("account_id")
            if isinstance(account_id, int) and account_id > 0:
                account_ids.add(account_id)
    return sorted(account_ids)


def refresh_avatars(
    *,
    config: dict[str, Any],
    client: OpenDotaLeagueClient,
    payload: dict[str, Any],
) -> tuple[Path, int, int]:
    """Cache Steam avatars for every player in the snapshot.

    Returns (path, total cached, newly fetched). The file is written beside the
    snapshot so GitHub Pages serves it straight from the repository.
    """
    path = Path(config.get("avatars_file") or DEFAULT_AVATARS_FILE)
    cache = load_avatar_cache(path)
    avatars, fetched = collect_avatars(
        client=client,
        account_ids=account_ids_in_snapshot(payload),
        cache=cache,
        max_new=int(config.get("max_new_avatars") or 0),
    )
    write_avatars(path, avatars)
    return path, len(avatars), fetched


def read_manual_match_ids(path: Path, *, require_non_empty: bool = True) -> list[int]:
    if not path.exists():
        if require_non_empty:
            raise LeagueCollectionError(f"Manual match file not found: {path}")
        return []
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
        if require_non_empty:
            raise LeagueCollectionError(f"No match id found in {path}")
        return []
    return sorted(match_ids)


def append_manual_match_ids(path: Path, match_ids: list[int]) -> list[int]:
    """Add match ids to the manual list, skipping duplicates. Keeps comments intact."""
    known = set(read_manual_match_ids(path, require_non_empty=False))
    new_ids = sorted({int(match_id) for match_id in match_ids if int(match_id) > 0} - known)
    if not new_ids:
        return []
    text = path.read_text(encoding="utf-8") if path.exists() else "# 手动登记的比赛 ID，每行一个。\n"
    if text and not text.endswith("\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "".join(f"{match_id}\n" for match_id in new_ids), encoding="utf-8")
    return new_ids


def load_match_overrides(path: Path, names: dict[str, str] | None = None) -> dict[int, dict[str, Any]]:
    """Read hand-entered matches from overrides.json.

    Games played on 完美世界/国服 servers are not exposed by Valve's public API or
    OpenDota, so the only way to put them on the board is to transcribe what the
    app shows. Each entry needs match_id, start_time (unix seconds), duration,
    radiant_win and the ten players with account_id, personaname, hero_id and
    player_slot (0-4 radiant, 128-132 dire).
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LeagueCollectionError(f"Overrides file {path} is not valid JSON: {error}") from error
    entries = raw.get("matches") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise LeagueCollectionError(f"Overrides file {path} must be a list or an object with a matches list")

    overrides: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise LeagueCollectionError(f"Overrides file {path} contains a non-object entry")
        raw_match = {
            "match_id": entry.get("match_id"),
            "leagueid": entry.get("league_id") or 0,
            "start_time": entry.get("start_time"),
            "duration": entry.get("duration"),
            "radiant_win": entry.get("radiant_win"),
            "players": entry.get("players"),
        }
        match_id = int(raw_match["match_id"] or 0)
        try:
            overrides[match_id] = normalise_match(raw_match, None, names=names)
        except LeagueCollectionError as error:
            raise LeagueCollectionError(f"Match override {match_id or '<unknown>'} in {path}: {error}") from error
    return overrides


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
    accept_unknown_league: bool = False,
    overrides: dict[int, dict[str, Any]] | None = None,
    exclude_ids: list[int] | None = None,
    exclude_game_modes: list[int] | None = None,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    existing = _load_existing(output_path, cache_league_id)
    refresh_ids = set(match_ids[-max(0, refresh_recent) :])
    matches: dict[int, dict[str, Any]] = {}
    unavailable: list[dict[str, Any]] = []
    skip_ids = {int(value) for value in (exclude_ids or [])}
    skip_modes = {int(value) for value in (exclude_game_modes or [])}
    # Blocked by id: drop before spending a request on them.
    excluded: list[dict[str, Any]] = [
        {"match_id": int(match_id), "reason": "config exclude_match_ids"}
        for match_id in match_ids
        if int(match_id) in skip_ids
    ]
    kept_ids = [match_id for match_id in match_ids if int(match_id) not in skip_ids]

    def skipped(match_id: int, match: dict[str, Any]) -> bool:
        game_mode = int(match.get("game_mode") or 0)
        if game_mode not in skip_modes:
            return False
        excluded.append(
            {"match_id": int(match_id), "reason": f"game_mode {game_mode} 不是内战（solo 房间）"}
        )
        return True

    for match_id in kept_ids:
        cached = existing.get(match_id)
        if cached is not None and match_id not in refresh_ids:
            if skipped(match_id, cached):
                continue
            matches[match_id] = _apply_names(cached, names)
            continue
        try:
            raw = client.get_json(f"/matches/{match_id}")
            if not isinstance(raw, dict):
                raise LeagueCollectionError("invalid match payload")
            normalised = normalise_match(
                raw,
                expected_league_id,
                names=names,
                accept_unknown_league=accept_unknown_league,
            )
            if skipped(match_id, normalised):
                continue
            matches[match_id] = normalised
        except LeagueCollectionError as error:
            if cached is not None:
                if skipped(match_id, cached):
                    continue
                matches[match_id] = _apply_names(cached, names)
            else:
                unavailable.append({"match_id": match_id, "reason": str(error)})

    # Hand-entered games win over whatever OpenDota had, and clear the "unavailable"
    # note so the board does not claim a match it actually has.
    if overrides:
        for match_id, match in overrides.items():
            matches[match_id] = match
        unavailable = [item for item in unavailable if int(item.get("match_id") or 0) not in overrides]
        excluded = [item for item in excluded if int(item.get("match_id") or 0) not in overrides]
    return matches, unavailable, excluded


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
    override_ids: list[int] | None = None,
    excluded: list[dict[str, Any]] | None = None,
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
            "manual_override_match_ids": sorted(override_ids or []),
            "excluded_matches": excluded or [],
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
    overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    league = client.get_json(f"/leagues/{league_id}")
    match_ids_raw = client.get_json(f"/leagues/{league_id}/matchIds")
    if not isinstance(league, dict):
        raise LeagueCollectionError("OpenDota returned an invalid league response")
    if not isinstance(match_ids_raw, list):
        raise LeagueCollectionError("OpenDota returned an invalid match-ID list")

    match_ids = sorted({int(match_id) for match_id in match_ids_raw if int(match_id) > 0})
    resolved = _resolve_config(config or DEFAULT_CONFIG, league_id, output_path, refresh_recent)
    matches, unavailable, excluded = _collect_matches(
        match_ids=match_ids,
        output_path=output_path,
        refresh_recent=refresh_recent,
        client=client,
        names=names or {},
        expected_league_id=league_id,
        cache_league_id=league_id,
        overrides=overrides,
        exclude_ids=resolved.get("exclude_match_ids", []),
        exclude_game_modes=resolved.get("exclude_game_modes", DEFAULT_EXCLUDE_GAME_MODES),
    )

    return _build_payload(
        config=resolved,
        league_info={
            "name": str(league.get("name") or f"League {league_id}"),
            "tier": str(league.get("tier") or ""),
            "banner": str(league.get("banner") or ""),
        },
        ordered_matches=_order_matches(matches),
        match_ids_returned=len(set(match_ids) | set(overrides or {})),
        unavailable=unavailable,
        client=client,
        override_ids=list(overrides or {}),
        excluded=excluded,
    )


def collect_manual_data(
    *,
    match_ids: list[int],
    output_path: Path,
    refresh_recent: int = 5,
    client: OpenDotaLeagueClient,
    names: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
    overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved = _resolve_config(config or DEFAULT_CONFIG, None, output_path, refresh_recent)
    matches, unavailable, excluded = _collect_matches(
        match_ids=match_ids,
        output_path=output_path,
        refresh_recent=refresh_recent,
        client=client,
        names=names or {},
        expected_league_id=None,
        cache_league_id=int(resolved["league_id"]),
        overrides=overrides,
        exclude_ids=resolved.get("exclude_match_ids", []),
        exclude_game_modes=resolved.get("exclude_game_modes", DEFAULT_EXCLUDE_GAME_MODES),
    )

    return _build_payload(
        config=resolved,
        league_info={
            "name": str(resolved["site"].get("title") or "内战手动收录"),
            "tier": "manual",
            "banner": "",
        },
        ordered_matches=_order_matches(matches),
        match_ids_returned=len(set(match_ids) | set(overrides or {})),
        unavailable=unavailable,
        client=client,
        override_ids=list(overrides or {}),
        excluded=excluded,
    )


def _league_info(
    client: OpenDotaLeagueClient, league_id: int, fallback_name: str
) -> dict[str, str]:
    try:
        league = client.get_json(f"/leagues/{league_id}")
    except LeagueCollectionError:
        league = None
    if not isinstance(league, dict):
        return {"name": fallback_name, "tier": "tournament", "banner": ""}
    return {
        "name": str(league.get("name") or fallback_name),
        "tier": str(league.get("tier") or "tournament"),
        "banner": str(league.get("banner") or ""),
    }


def collect_tournament_data(
    *,
    config: dict[str, Any],
    steam_client: SteamMatchClient,
    client: OpenDotaLeagueClient,
    output_path: Path,
    names: dict[str, str] | None = None,
    overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Import the league's own games from Valve, plus anything listed in matches.txt.

    Valve knows which matches belong to the league even when OpenDota marks the
    league `excluded` and aggregates none of them, which is what makes this mode
    worth having for an amateur league.
    """
    league_id = int(config["league_id"])
    if league_id <= 0:
        raise LeagueCollectionError("league_id is required when collect_mode is 'tournament'")

    tournament_ids = steam_client.tournament_match_ids(
        league_id, max_matches=int(config["max_tournament_matches"])
    )
    manual_ids = read_manual_match_ids(
        Path(config["manual_match_file"]), require_non_empty=False
    )
    # Games already in the snapshot stay there even after they fall out of the cap.
    known_ids = set(_load_existing(output_path, league_id))
    match_ids = sorted(set(tournament_ids) | set(manual_ids) | known_ids)

    matches, unavailable, excluded = _collect_matches(
        match_ids=match_ids,
        output_path=output_path,
        refresh_recent=int(config["refresh_recent"]),
        client=client,
        names=names or {},
        expected_league_id=league_id,
        cache_league_id=league_id,
        accept_unknown_league=True,
        overrides=overrides,
        exclude_ids=_int_list(config.get("exclude_match_ids"), "exclude_match_ids", []),
        exclude_game_modes=_int_list(
            config.get("exclude_game_modes"), "exclude_game_modes", DEFAULT_EXCLUDE_GAME_MODES
        ),
    )

    return _build_payload(
        config=config,
        league_info=_league_info(
            client, league_id, str(config["site"].get("title") or f"League {league_id}")
        ),
        ordered_matches=_order_matches(matches),
        match_ids_returned=len(set(match_ids) | set(overrides or {})),
        unavailable=unavailable,
        client=client,
        override_ids=list(overrides or {}),
        excluded=excluded,
    )


def collect_snapshot(
    *,
    config: dict[str, Any],
    client: OpenDotaLeagueClient,
    output_path: Path,
    names: dict[str, str] | None = None,
    steam_client: SteamMatchClient | None = None,
    overrides: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Collect according to collect_mode: league, manual, both, or tournament."""
    mode = config["collect_mode"]
    if mode == "tournament":
        if steam_client is None:
            raise LeagueCollectionError(
                "tournament mode needs STEAM_WEB_API_KEY "
                f"(get a free key at {STEAM_KEY_HELP})"
            )
        return collect_tournament_data(
            config=config,
            steam_client=steam_client,
            client=client,
            output_path=output_path,
            names=names,
            overrides=overrides,
        )
    league_enabled = mode in {"league", "both"}
    manual_enabled = mode in {"manual", "both"}

    if league_enabled and int(config["league_id"]) <= 0:
        raise LeagueCollectionError(
            "league_id is required when collect_mode is 'league' or 'both'"
        )

    if league_enabled and not manual_enabled:
        return collect_league_data(
            league_id=int(config["league_id"]),
            output_path=output_path,
            refresh_recent=int(config["refresh_recent"]),
            client=client,
            names=names,
            config=config,
            overrides=overrides,
        )
    if manual_enabled and not league_enabled:
        return collect_manual_data(
            match_ids=read_manual_match_ids(Path(config["manual_match_file"])),
            output_path=output_path,
            refresh_recent=int(config["refresh_recent"]),
            client=client,
            names=names,
            config=config,
            overrides=overrides,
        )

    league_payload = collect_league_data(
        league_id=int(config["league_id"]),
        output_path=output_path,
        refresh_recent=int(config["refresh_recent"]),
        client=client,
        names=names,
        config=config,
        overrides=overrides,
    )
    manual_ids = read_manual_match_ids(
        Path(config["manual_match_file"]), require_non_empty=False
    )
    known = {int(match["match_id"]) for match in league_payload["matches"]}
    extra_ids = [match_id for match_id in manual_ids if match_id not in known]
    if not extra_ids:
        return league_payload

    manual_payload = collect_manual_data(
        match_ids=extra_ids,
        output_path=output_path,
        refresh_recent=int(config["refresh_recent"]),
        client=client,
        names=names,
        config=config,
        overrides=overrides,
    )
    merged: dict[int, dict[str, Any]] = {
        int(match["match_id"]): match for match in league_payload["matches"]
    }
    merged.update({int(match["match_id"]): match for match in manual_payload["matches"]})

    league_meta = league_payload["metadata"]
    manual_meta = manual_payload["metadata"]
    excluded: dict[int, str] = {}
    for item in list(league_meta["excluded_matches"]) + list(manual_meta["excluded_matches"]):
        excluded[int(item["match_id"])] = str(item.get("reason") or "")

    return _build_payload(
        config=config,
        league_info=league_payload["league"],
        ordered_matches=_order_matches(merged),
        match_ids_returned=int(league_payload["metadata"]["match_ids_returned"]) + len(extra_ids),
        unavailable=league_payload["metadata"]["unavailable_matches"]
        + manual_payload["metadata"]["unavailable_matches"],
        client=client,
        override_ids=sorted(
            {int(value) for value in league_meta["manual_override_match_ids"]}
            | {int(value) for value in manual_meta["manual_override_match_ids"]}
        ),
        excluded=[
            {"match_id": match_id, "reason": reason} for match_id, reason in sorted(excluded.items())
        ],
    )


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _stable_snapshot_timestamp(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the old timestamp when nothing but the timestamp would change.

    Otherwise the daily scheduled run commits a diff every single day, which
    clutters history and collides with local refreshes.
    """
    if not path.exists():
        return payload
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return payload
    if not isinstance(existing, dict):
        return payload

    def content(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in snapshot.items() if key != "generated_at_utc"}

    previous_stamp = existing.get("generated_at_utc")
    if isinstance(previous_stamp, str) and content(existing) == content(payload):
        return {**payload, "generated_at_utc": previous_stamp}
    return payload


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
        choices=("league", "manual", "both", "tournament"),
        default=None,
        help="Override config.json collect_mode",
    )
    parser.add_argument("--output", type=Path, default=None, help="Override config.json output")
    parser.add_argument(
        "--refresh-recent", type=int, default=None, help="Override config.json refresh_recent"
    )
    parser.add_argument(
        "--skip-avatars",
        action="store_true",
        help="Do not fetch Steam avatars for the players in this snapshot",
    )
    parser.add_argument(
        "--max-paid-calls",
        type=int,
        default=int(os.getenv("OPENDOTA_MAX_PAID_CALLS", "250")),
    )
    parser.add_argument(
        "--steam-api-key",
        default=os.getenv("STEAM_WEB_API_KEY", ""),
        help="Steam Web API key for tournament mode (default: STEAM_WEB_API_KEY)",
    )
    parser.add_argument(
        "--max-tournament-matches",
        type=int,
        default=None,
        help="Cap how many league matches tournament mode imports (default 100)",
    )
    parser.add_argument(
        "--add-match",
        type=int,
        action="append",
        dest="added_matches",
        default=[],
        metavar="MATCH_ID",
        help="Register one or more match ids in matches.txt before collecting",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    config = _resolve_config(config, args.league_id, args.output, args.refresh_recent)
    if args.mode is not None:
        config["collect_mode"] = args.mode
    if args.max_tournament_matches is not None:
        config["max_tournament_matches"] = max(1, int(args.max_tournament_matches))
    names = load_display_names(Path(config["names_file"]))
    overrides = load_match_overrides(Path(config["overrides_file"]), names=names)
    if overrides:
        print(f"手工补录 {len(overrides)} 场：{', '.join(str(match_id) for match_id in sorted(overrides))}")

    if args.added_matches:
        added = append_manual_match_ids(Path(config["manual_match_file"]), args.added_matches)
        if added:
            print(f"registered {', '.join(str(match_id) for match_id in added)} in {config['manual_match_file']}")
        else:
            print("those match ids are already registered")

    with OpenDotaLeagueClient(
        api_key=os.getenv("OPENDOTA_API_KEY"),
        max_paid_calls=args.max_paid_calls,
    ) as client:
        if args.league_name:
            config["league_id"] = find_league_id(client, args.league_name)
            if config["collect_mode"] != "tournament":
                config["collect_mode"] = "league"
            print(f"Resolved {args.league_name!r} to league id {config['league_id']}")
        if config["collect_mode"] == "tournament":
            with SteamMatchClient(api_key=args.steam_api_key) as steam_client:
                payload = collect_snapshot(
                    config=config,
                    client=client,
                    output_path=config["output"],
                    names=names,
                    steam_client=steam_client,
                    overrides=overrides,
                )
        else:
            payload = collect_snapshot(
                config=config,
                client=client,
                output_path=config["output"],
                names=names,
                overrides=overrides,
            )
        if args.skip_avatars:
            avatar_line = "avatars skipped"
        else:
            avatar_path, avatar_total, avatar_new = refresh_avatars(
                config=config, client=client, payload=payload
            )
            avatar_line = f"{avatar_total} avatars cached ({avatar_new} new) in {avatar_path}"

    # Compare against the file on disk before overwriting it.
    payload = _stable_snapshot_timestamp(config["output"], payload)
    write_snapshot(config["output"], payload)
    metadata = payload["metadata"]
    label = (
        {
            "league": f"league {config['league_id']}",
            "manual": f"manual list ({metadata['match_ids_returned']} ids)",
            "both": f"league {config['league_id']} + manual list",
            "tournament": f"league {config['league_id']} via Valve",
        }[config["collect_mode"]]
    )
    print(
        f"{label}: wrote {metadata['matches_available']} matches to {config['output']} "
        f"({len(metadata['unavailable_matches'])} unavailable, "
        f"{metadata['anonymous_player_slots']} anonymous slots, "
        f"{metadata['paid_calls']} paid calls)"
    )
    print(avatar_line)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LeagueCollectionError as error:
        print(f"Collection failed: {error}")
        raise SystemExit(1)
