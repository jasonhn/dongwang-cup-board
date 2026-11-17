#!/usr/bin/env python3
"""排名算法的数据体检：复现 docs/排名算法.md 里的每一个数字。

只依赖标准库，读的是仓库里已经采集好的快照，不联网：

    .venv/bin/python scripts/rank_stats.py

赛季每多打十几场就值得重跑一次：临界 k*、区分度损失、出勤资格线逐档、
以及"现在的排名到底是实力还是运气"的置换检验都会随之变化。
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data" / "league.json"
PRIOR_WINS = 3          # 与 site/app.js 的 RANKING_PRIOR_WINS 保持一致
PRIOR_LOSSES = 3
SEED = 20261117
PERMUTATIONS = 3000


def load_standings(path: Path = DATA) -> tuple[dict[int, dict[str, float]], list[dict], int]:
    """返回 {account_id: {wins, games, name}}、逐场选手列表、人次总数。"""
    payload = json.loads(path.read_text("utf-8"))
    players: dict[int, dict[str, float]] = {}
    rounds: list[list[tuple[int, bool]]] = []
    slots = 0
    for match in payload.get("matches", []):
        roster = []
        for player in match.get("players") or []:
            account, slot = player.get("account_id"), player.get("player_slot")
            if not isinstance(account, int) or not isinstance(slot, int):
                continue
            won = (slot < 128) == bool(match.get("radiant_win"))
            entry = players.setdefault(
                account,
                {
                    "wins": 0,
                    "games": 0,
                    "name": player.get("display_name") or player.get("personaname") or str(account),
                },
            )
            entry["wins"] += won
            entry["games"] += 1
            roster.append((account, won))
            slots += 1
        rounds.append(roster)
    return players, rounds, slots


def score(wins: int, games: int, prior_wins: float, prior_losses: float) -> float:
    return (wins + prior_wins) / (games + prior_wins + prior_losses)


def rank(players: dict[int, dict[str, float]], prior_wins: float, prior_losses: float) -> list[int]:
    """复刻 comparePlayerRanking：分数 → 胜场 → 场数 → 昵称 → account_id。"""
    return sorted(
        players,
        key=lambda account: (
            -score(players[account]["wins"], players[account]["games"], prior_wins, prior_losses),
            -players[account]["wins"],
            -players[account]["games"],
            str(players[account]["name"]),
            account,
        ),
    )


def header(title: str) -> None:
    print(f"\n## {title}")


def report_basics(players: dict[int, dict[str, float]], rounds: list, slots: int) -> None:
    games = sorted(entry["games"] for entry in players.values())
    wins = sum(entry["wins"] for entry in players.values())
    uneven = [
        index
        for index, roster in enumerate(rounds)
        if sum(1 for _, won in roster if won) * 2 != len(roster)
    ]
    header("数据体检")
    print(f"  场数 {len(rounds)} 人次 {slots} 人数 {len(players)} 人均 {slots / len(players):.2f} 场数中位数 {games[len(games) // 2]}")
    print(f"  Σ胜 {wins} Σ负 {slots - wins} 加权平均胜率 {wins / slots:.6f}（命题 1：应为 0.5）")
    print(f"  两队人数不等的场次：{uneven or '无'}（前提 P1 成立）")


def report_standings(players: dict[int, dict[str, float]]) -> None:
    old = rank(players, 1, 1)
    new = rank(players, PRIOR_WINS, PRIOR_LOSSES)
    position = {account: index + 1 for index, account in enumerate(old)}
    header("排名对照（v1 = (w+1)/(n+2) → v2 = (w+3)/(n+6)）")
    print("   v2  v1  变化  玩家                 胜 负 场   胜率")
    for index, account in enumerate(new[:12]):
        entry = players[account]
        delta = position[account] - (index + 1)
        wins, games = int(entry["wins"]), int(entry["games"])
        print(
            f"  {index + 1:>3} {position[account]:>3}  {delta:>+3}  {str(entry['name'])[:18]:<18} "
            f"{wins:>2} {games - wins:>2} {games:>3}  {wins / games * 100:>4.0f}%"
        )
    moved = sum(1 for account in new if position[account] != new.index(account) + 1)
    shift = max(abs(position[account] - index - 1) for index, account in enumerate(new))
    print(f"  共 {len(players)} 人：名次变化 {moved} 人，最大位移 {shift} 名")


def report_critical_k() -> None:
    def kstar(wins_1: int, games_1: int, wins_2: int, games_2: int) -> float:
        return 2 * (wins_2 * games_1 - wins_1 * games_2) / (
            2 * (wins_1 - wins_2) + (games_2 - games_1)
        )

    header("命题 4.1：场多的人反超所需的临界 k*")
    for short, long in [((3, 3), (6, 7)), ((5, 5), (12, 14)), ((10, 10), (25, 30)), ((15, 15), (30, 36))]:
        (w1, n1), (w2, n2) = short, long
        print(
            f"  {w2}胜{n2 - w2}负 压过 {w1}胜{n1 - w1}负 → k ≥ {kstar(w1, n1, w2, n2):.1f}"
        )
    print("  齐次性：同一组对比放大 c 倍，临界 k* 也放大 c 倍 →",
          [round(kstar(3 * c, 3 * c, 6 * c, 7 * c)) for c in (1, 2, 4, 10)])


def report_discrimination() -> None:
    header("命题 4.2：同场数下 75% 与 60% 的分数差 = 20×0.15/(20+k)")
    for k in (2, 4, 6, 8, 12):
        print(f"  k={k:>2} → {score(15, 20, k / 2, k / 2) - score(12, 20, k / 2, k / 2):.4f}")


def report_permutation(players: dict[int, dict[str, float]], rounds: list) -> None:
    def statistic(rosters: list[list[tuple[int, bool]]]) -> float:
        tally: dict[int, list[int]] = {}
        for roster in rosters:
            for account, won in roster:
                cell = tally.setdefault(account, [0, 0])
                cell[0] += won
                cell[1] += 1
        chi_squared = sum(
            (wins - games * 0.5) ** 2 / (games * 0.25) for wins, games in tally.values()
        )
        return chi_squared / (len(tally) - 1)

    observed = statistic(rounds)
    rng = random.Random(SEED)
    null = []
    for _ in range(PERMUTATIONS):
        shuffled = []
        for roster in rounds:
            accounts = [account for account, _ in roster]
            results = [won for _, won in roster]
            rng.shuffle(results)          # 每场的胜负随机重分配给同一批人
            shuffled.append(list(zip(accounts, results)))
        null.append(statistic(shuffled))
    null.sort()
    exceed = sum(1 for value in null if value >= observed)
    header(f"置换检验（H₀：胜负与场上是谁无关；{PERMUTATIONS} 次重排，seed={SEED}）")
    print(f"  观测 χ²/df = {observed:.3f}")
    print(
        f"  零分布：中位数 {null[len(null) // 2]:.3f}，2.5%~97.5% 分位 "
        f"[{null[int(len(null) * 0.025)]:.3f}, {null[int(len(null) * 0.975)]:.3f}]"
    )
    print(f"  置换 p 值 = {exceed / len(null):.3f} → p > 0.05 时名次差异不能归因于实力")


def report_power() -> None:
    header("功效分析：识别真实胜率 p（单侧 α=5%、功效 80%）所需场数")
    z_alpha, z_beta = 1.6449, 0.8416
    for p in (0.55, 0.58, 0.60, 0.65):
        need = (z_alpha + z_beta) ** 2 * p * (1 - p) / (p - 0.5) ** 2
        print(f"  p={p:.0%} → {math.ceil(need)} 场/人")


def report_attendance(players: dict[int, dict[str, float]]) -> None:
    busiest = max(entry["games"] for entry in players.values())
    header(f"出勤资格线逐档（最活跃者 {busiest} 场）：n ≥ ⌈ρ × {busiest}⌉")
    for ratio in (0.4, 0.5, 0.6, 0.7):
        threshold = math.ceil(ratio * busiest)
        qualified = [account for account, e in players.items() if e["games"] >= threshold]
        best = rank(players, PRIOR_WINS, PRIOR_LOSSES)
        champion = next(account for account in best if account in qualified)
        entry = players[champion]
        wins, games = int(entry["wins"]), int(entry["games"])
        print(
            f"  ρ={ratio:.1f} → 门槛 {threshold} 场，达标 {len(qualified):>2} 人，"
            f"冠军 {str(entry['name'])[:16]} {wins}胜{games - wins}负（{wins / games * 100:.0f}%）"
        )

    header("把出勤加进主榜分数的污染点：score_v2 + λ × (n / 最活跃者场数)")
    for lam in (0.0, 0.02, 0.04, 0.06, 0.10):
        order = sorted(
            players,
            key=lambda account: -(
                score(players[account]["wins"], players[account]["games"], PRIOR_WINS, PRIOR_LOSSES)
                + lam * players[account]["games"] / busiest
            ),
        )[:6]
        worst = min(order, key=lambda account: players[account]["wins"] / players[account]["games"])
        rate = players[worst]["wins"] / players[worst]["games"]
        print(f"  λ={lam:<5} 前六名里最低胜率 {rate:.0%}")


def report_expected_shift(players: dict[int, dict[str, float]]) -> None:
    header("定理 5.2：再打一局的期望分数变化 (n/2 − w)/((n+k)(n+k+1))")
    for wins, games in ((3, 3), (6, 7), (5, 9), (0, 3)):
        for prior in (1, PRIOR_WINS):
            closed = (games / 2 - wins) / (
                (games + 2 * prior) * (games + 2 * prior + 1)
            )
            print(
                f"  {wins}胜{games - wins}负 k={2 * prior}: 期望 Δ = {closed:+.4f}"
                f"{'  ← 涨' if closed > 0 else '  ← 跌' if closed < 0 else '  ← 不变'}"
            )


def main() -> None:
    players, rounds, slots = load_standings()
    report_basics(players, rounds, slots)
    report_standings(players)
    report_critical_k()
    report_discrimination()
    report_expected_shift(players)
    report_permutation(players, rounds)
    report_power()
    report_attendance(players)


if __name__ == "__main__":
    main()
