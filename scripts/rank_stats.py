#!/usr/bin/env python3
"""排名标准的数据体检：复现 docs 里的数字，并检查口径是否客观。

只依赖标准库，读仓库里已采集好的快照，不联网：

    .venv/bin/python scripts/rank_stats.py

赛季每多打十几场就值得重跑一次：置换检验的结论、出勤分布都会随之变化。
"""

from __future__ import annotations

import json
import math
import random
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data" / "league.json"

# 只用于"为什么要放弃收缩式"的对照，不是现行标准。
V1 = (1, 1)          # (w+1)/(n+2)
V2 = (3, 3)          # (w+3)/(n+6)，需要人拍板先验强度，已废弃
SEED = 20261117
PERMUTATIONS = 3000


def load(path: Path = DATA):
    """返回 {account_id: {wins, games, name}}、逐场选手列表、原始 metadata。"""
    payload = json.loads(path.read_text("utf-8"))
    players: dict[int, dict[str, float]] = {}
    rounds: list[list[tuple[int, bool]]] = []
    slots = 0
    for match in payload.get("matches", []):
        roster = []
        for player in match.get("players") or []:
            account, slot = player.get("account_id"), player.get("player_slot")
            if not isinstance(account, int) or not isinstance(slot, int):
                continue          # 匿名位置不计入任何人
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
    return players, rounds, slots, payload.get("metadata", {})


def name_of(players: dict[int, dict[str, float]], account: int) -> str:
    return str(players[account]["name"])[:18]


def rank_smoothed(players: dict[int, dict[str, float]], prior: tuple[int, int]) -> list[int]:
    pw, pl = prior
    return sorted(
        players,
        key=lambda account: (
            -(players[account]["wins"] + pw) / (players[account]["games"] + pw + pl),
            -players[account]["wins"],
            -players[account]["games"],
            name_of(players, account),
            account,
        ),
    )


def rank_net(players: dict[int, dict[str, float]]) -> list[int]:
    """现行标准 v3：净胜场 → 胜场数 → 昵称 → Account ID。零参数、纯整数。"""
    return sorted(
        players,
        key=lambda account: (
            -(players[account]["wins"] - (players[account]["games"] - players[account]["wins"])),
            -players[account]["wins"],
            name_of(players, account),
            account,
        ),
    )


def header(title: str) -> None:
    print(f"\n## {title}")


def rank_labels(order: list[int], players: dict[int, dict[str, float]]) -> list[int]:
    """并列名次：净胜场与胜场数都相同（即胜负两列完全相同）的人共用一个名次。

    与前端 app.js 的 rankNumbers() 同一口径，页面上显示的数字就是这里算出来的。
    """
    labels: list[int] = []
    for index, account in enumerate(order):
        wins, games = int(players[account]["wins"]), int(players[account]["games"])
        previous = order[index - 1] if index else None
        tied = previous is not None and (
            int(players[previous]["wins"]) == wins
            and int(players[previous]["games"]) == games
        )
        labels.append(labels[index - 1] if tied else index + 1)
    return labels


def report_scope(players, rounds, slots, metadata) -> None:
    games = sorted(entry["games"] for entry in players.values())
    wins = sum(entry["wins"] for entry in players.values())
    uneven = [i for i, r in enumerate(rounds) if sum(1 for _, won in r if won) * 2 != len(r)]
    header("口径体检（客观性检查）")
    print(f"  场数 {len(rounds)} 人次 {slots} 人数 {len(players)} 人均 {slots / len(players):.2f} 场数中位数 {games[len(games) // 2]}")
    print(f"  Σ胜 {wins} Σ负 {slots - wins} 加权平均胜率 {wins / slots:.6f}（每场两队人数相等时应为 0.5）")
    print(f"  两队人数不等的场次：{uneven or '无'}")
    print(f"  metadata.anonymous_player_slots = {metadata.get('anonymous_player_slots')}（匿名位置不计入任何人）")
    print(f"  metadata.incomplete_roster_match_ids = {metadata.get('incomplete_roster_match_ids') or '无'}")
    print(f"  metadata.manual_override_match_ids = {metadata.get('manual_override_match_ids') or '无'}（手工补录必须留痕）")
    print(f"  metadata.excluded_matches = {metadata.get('excluded_matches') or '无'}")


def report_standings(players) -> None:
    v3 = rank_net(players)
    labels = rank_labels(v3, players)
    pos_v1 = {account: i + 1 for i, account in enumerate(rank_smoothed(players, V1))}
    pos_v2 = {account: i + 1 for i, account in enumerate(rank_smoothed(players, V2))}
    header("现行标准 v3：净胜场 = 胜场 − 负场，同分比胜场数")
    print("   名次 玩家                 胜 负 场  胜率  净胜场   v1名次 v2名次")
    for i, account in enumerate(v3[:12]):
        entry = players[account]
        wins, games = int(entry["wins"]), int(entry["games"])
        losses = games - wins
        print(
            f"  {labels[i]:>5} {name_of(players, account):<18} {wins:>2} {losses:>2} {games:>3} "
            f"{wins / games * 100:>4.0f}%   {wins - losses:>3}    {pos_v1[account]:>5} {pos_v2[account]:>5}"
        )
    groups = collections.Counter((int(e["wins"]), int(e["games"]) - int(e["wins"])) for e in players.values())
    tied_groups = {record: n for record, n in groups.items() if n > 1}
    print(f"  并列：{len(tied_groups)} 组完全相同的战绩，涉及 {sum(tied_groups.values())} 人"
          f"（最常见的一组 {max(tied_groups.values()) if tied_groups else 0} 人同战绩，共用一个名次）")
    print("  名次数字按队列位置递增，并列不跳号；v1/v2 那两列是旧算法的严格先后，仅供对照")
    moved_v2 = sum(1 for i, account in enumerate(v3) if pos_v2[account] != i + 1)
    shift = max(abs(pos_v2[account] - i - 1) for i, account in enumerate(v3))
    print(f"  与已废弃的 v2 相比：{moved_v2} 人名次不同，最大位移 {shift} 名")
    top3 = v3[:3]
    print(f"  前三名的场数：{[int(players[a]['games']) for a in top3]}（只打一场的人净胜场只有 1，无需人为场次门槛）")


def report_properties(players) -> None:
    header("零参数标准的三条性质")
    print("  1) 净胜场 = 场数 × (2×胜率 − 1)：五成选手净胜场恒为 0，刷场次不涨")
    flat = [account for account, e in players.items() if e["wins"] * 2 == e["games"]]
    print(f"     本快照里胜率正好五成的 {len(flat)} 人，净胜场都是 {sorted({int(e['wins'] * 2 - e['games']) for e in (players[a] for a in flat)})}")
    print("  2) 再打一局的得失对每个人都是同一个 ±1，与当前名次、样本大小无关")
    for wins, games in ((6, 7), (3, 3), (5, 9), (0, 3)):
        losses = games - wins
        print(f"     {wins}胜{losses}负：赢一局净胜场 {wins - losses} → {wins + 1 - losses}（+1）；输一局 → {wins - losses - 1}（−1）")
    print("  3) 只打 1 场的人净胜场最多 1，靠规则本身就被压在榜尾，不需要管理员设门槛")
    one_game = [account for account, e in players.items() if e["games"] == 1 and e["wins"] == 1]
    order = rank_net(players)
    labels = rank_labels(order, players)
    rank_of = {account: labels[i] for i, account in enumerate(order)}
    print(f"     本快照 1 胜 0 负的 {len(one_game)} 人，名次 {sorted(rank_of[a] for a in one_game)}")


def report_why_not_smoothed() -> None:
    def kstar(w1, n1, w2, n2):
        return 2 * (w2 * n1 - w1 * n2) / (2 * (w1 - w2) + (n2 - n1))

    header("为什么放弃需要参数的收缩式（v2 = (w+3)/(n+6)）")
    print("  a) 反超所需的先验强度没有统一取值，随场数线性上涨：")
    for (w1, n1), (w2, n2) in [((3, 3), (6, 7)), ((5, 5), (12, 14)), ((10, 10), (25, 30)), ((15, 15), (30, 36))]:
        print(f"     {w2}胜{n2 - w2}负 压过 {w1}胜{n1 - w1}负 → 需要 k ≥ {kstar(w1, n1, w2, n2):.1f}")
    print("     齐次性：同一组对比放大 1×/2×/4×/10× →", [round(kstar(3 * c, 3 * c, 6 * c, 7 * c)) for c in (1, 2, 4, 10)])
    print("  b) k 越大，同场数的人之间区分度越平（分差 = n·Δp/(n+k)）：")
    for k in (2, 6, 12):
        pw = k / 2
        gap = (15 + pw) / (20 + k) - (12 + pw) / (20 + k)
        print(f"     k={k:>2} → 20 场 75% 与 60% 的分数差 {gap:.4f}")
    print("  c) 收缩式里高于平均的人再打一局期望要掉分，会催生「保住名次就别打」：")
    for wins, games in ((3, 3), (6, 7), (5, 9), (0, 3)):
        losses = games - wins
        for prior in V1, V2:
            k = prior[0] + prior[1]
            delta = (games / 2 - wins) / ((games + k) * (games + k + 1))
            print(f"     {wins}胜{losses}负 k={k}: 期望 Δ = {delta:+.4f}")
    print("  d) 净胜场没有上面三条问题：参数为 0，且每局的期望增益 = 2p − 1，只跟真实胜率有关。")


def report_permutation(players, rounds) -> None:
    def statistic(rosters):
        tally: dict[int, list[int]] = {}
        for roster in rosters:
            for account, won in roster:
                cell = tally.setdefault(account, [0, 0])
                cell[0] += won
                cell[1] += 1
        chi = sum((w - g * 0.5) ** 2 / (g * 0.25) for w, g in tally.values())
        return chi / (len(tally) - 1)

    observed = statistic(rounds)
    rng = random.Random(SEED)
    null = []
    for _ in range(PERMUTATIONS):
        shuffled = []
        for roster in rounds:
            accounts = [a for a, _ in roster]
            results = [won for _, won in roster]
            rng.shuffle(results)
            shuffled.append(list(zip(accounts, results)))
        null.append(statistic(shuffled))
    null.sort()
    exceed = sum(1 for v in null if v >= observed)
    header(f"置换检验（H₀：胜负与场上是谁无关；{PERMUTATIONS} 次重排，seed={SEED}）")
    print(f"  观测 χ²/df = {observed:.3f}")
    print(f"  零分布：中位数 {null[len(null) // 2]:.3f}，2.5%~97.5% 分位 [{null[int(len(null) * .025)]:.3f}, {null[int(len(null) * .975)]:.3f}]")
    print(f"  置换 p 值 = {exceed / len(null):.3f}（> 0.05 ⇒ 还分不清实力与运气，只能靠场数积累）")


def report_power() -> None:
    header("功效分析：识别真实胜率 p（单侧 α=5%、功效 80%）所需场数")
    za, zb = 1.6449, 0.8416
    for p in (0.55, 0.58, 0.60, 0.65):
        print(f"  p={p:.0%} → {math.ceil((za + zb) ** 2 * p * (1 - p) / (p - 0.5) ** 2)} 场/人")


def report_attendance(players) -> None:
    busiest = max(e["games"] for e in players.values())
    header("出勤与主榜的边界")
    print("  净胜场本身已经在奖励出勤（净胜场 = 场数 × 超额胜率），所以不需要人为资格线。")
    print(f"  最活跃者 {busiest} 场；净胜场前五名的场数：{[int(players[a]['games']) for a in rank_net(players)[:5]]}")
    print(f"  把出勤再加进主榜分数会立刻污染排序：score_v2 + λ × (场数 / {busiest})")
    for lam in (0.0, 0.02, 0.06, 0.10):
        order = sorted(
            players,
            key=lambda a: -(
                (players[a]["wins"] + V2[0]) / (players[a]["games"] + V2[0] + V2[1])
                + lam * players[a]["games"] / busiest
            ),
        )[:6]
        worst = min(players[a]["wins"] / players[a]["games"] for a in order)
        print(f"     λ={lam:<5} 前六名里最低胜率 {worst:.0%}")
    iron = sorted(players, key=lambda a: (-players[a]["games"], a))[:6]
    print("  独立的出勤榜（只比场数，只增不减）前列：")
    for a in iron:
        e = players[a]
        print(f"     {name_of(players, a):<18} {int(e['games']):>2} 场（{int(e['wins'])}胜{int(e['games'] - e['wins'])}负）")


def main() -> None:
    players, rounds, slots, metadata = load()
    report_scope(players, rounds, slots, metadata)
    report_standings(players)
    report_properties(players)
    report_why_not_smoothed()
    report_permutation(players, rounds)
    report_power()
    report_attendance(players)


if __name__ == "__main__":
    main()
