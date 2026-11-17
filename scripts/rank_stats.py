#!/usr/bin/env python3
"""积分规则的数据体检：复现 docs 里的数字，并检查统计口径是否客观。

只依赖标准库，读仓库里已采集好的快照，不联网：

    .venv/bin/python scripts/rank_stats.py

赛季每多打十几场就值得重跑一次：置换检验的结论、出勤分布都会随之变化。
"""

from __future__ import annotations

import collections
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data" / "league.json"

# 现行规则 v6（群规）：初始 0 分，赢一局 +50，输一局 −40。与 site/app.js 里那两个常量必须
# 一致，tests/test_ranking.py 会钉住它们。只有比值有意义：50 : 40 最简是 5 : 4。
WIN_POINTS = 50
LOSS_POINTS = 40

# 只用于新旧对照，不是现行标准。
V1 = (1, 1)          # (w+1)/(n+2)：往 50% 收缩，先验 k = 2
V2 = (3, 3)          # (w+3)/(n+6)：先验 k = 6，需要人拍板，已废弃
SEED = 20261117
PERMUTATIONS = 3000


def load(path: Path = DATA):
    """返回 {account_id: {wins, games, name}}、逐场选手列表、人次、原始 metadata。"""
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
            kda = [player.get("kills"), player.get("deaths"), player.get("assists")]
            if all(isinstance(value, int) for value in kda):
                # 与 site/app.js 的 calculate() 同一口径：缺字段的场次不计入合计，但仍计入场次
                entry["kills"] = entry.get("kills", 0) + kda[0]
                entry["deaths"] = entry.get("deaths", 0) + kda[1]
                entry["assists"] = entry.get("assists", 0) + kda[2]
                entry["kda_games"] = entry.get("kda_games", 0) + 1
            else:
                entry.setdefault("kills", 0)
                entry.setdefault("deaths", 0)
                entry.setdefault("assists", 0)
                entry.setdefault("kda_games", 0)
            roster.append((account, won))
            slots += 1
        rounds.append(roster)
    return players, rounds, slots, payload.get("metadata", {})


def name_of(players: dict[int, dict[str, float]], account: int) -> str:
    return str(players[account]["name"])[:18]


def points_of(entry: dict[str, float]) -> int:
    wins = int(entry["wins"])
    losses = int(entry["games"]) - wins
    return wins * WIN_POINTS - losses * LOSS_POINTS


def rank_points(players: dict[int, dict[str, float]]) -> list[int]:
    """现行标准 v6：积分 → 胜场数 → 昵称 → Account ID（后两级只是显示顺序）。"""
    return sorted(
        players,
        key=lambda account: (
            -points_of(players[account]),
            -int(players[account]["wins"]),
            name_of(players, account),
            account,
        ),
    )


def rank_smoothed(players: dict[int, dict[str, float]], prior: tuple[int, int]) -> list[int]:
    """已废弃的收缩式，只为对照名次。"""
    pw, pl = prior
    k = pw + pl
    return sorted(
        players,
        key=lambda account: (
            -(players[account]["wins"] + pw) / (players[account]["games"] + k),
            -players[account]["wins"],
            -players[account]["games"],
            name_of(players, account),
            account,
        ),
    )


def rank_net(players: dict[int, dict[str, float]]) -> list[int]:
    """上一版标准 v3：净胜场 → 胜场数。零参数，但没有积分规则那样偏重出勤。"""
    return sorted(
        players,
        key=lambda account: (
            -(2 * int(players[account]["wins"]) - int(players[account]["games"])),
            -int(players[account]["wins"]),
            name_of(players, account),
            account,
        ),
    )


def rank_labels(order: list[int], players: dict[int, dict[str, float]]) -> list[int]:
    """并列名次：胜、负两列完全相同的人共用一个名次（与 app.js 的 rankNumbers 同口径）。"""
    labels: list[int] = []
    for index, account in enumerate(order):
        wins, games = int(players[account]["wins"]), int(players[account]["games"])
        previous = order[index - 1] if index else None
        tied = previous is not None and (
            int(players[previous]["wins"]) == wins and int(players[previous]["games"]) == games
        )
        labels.append(labels[index - 1] if tied else index + 1)
    return labels


def header(title: str) -> None:
    print(f"\n## {title}")


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
    current = rank_points(players)
    labels = rank_labels(current, players)
    pos_v1 = {account: i + 1 for i, account in enumerate(rank_smoothed(players, V1))}
    pos_v2 = {account: i + 1 for i, account in enumerate(rank_smoothed(players, V2))}
    pos_net = {account: i + 1 for i, account in enumerate(rank_net(players))}
    header(f"现行标准 v6：积分 = {WIN_POINTS}×胜 − {LOSS_POINTS}×负，同分比胜场数")
    print("   名次 玩家                 胜 负 场  胜率   积分  净胜场   v1  v2  净胜场版")
    for i, account in enumerate(current[:14]):
        entry = players[account]
        wins, games = int(entry["wins"]), int(entry["games"])
        losses = games - wins
        print(
            f"  {labels[i]:>5} {name_of(players, account):<18} {wins:>2} {losses:>2} {games:>3} "
            f"{wins / games * 100:>4.0f}%  {wins * WIN_POINTS - losses * LOSS_POINTS:>5}   {wins - losses:>3}   "
            f"{pos_v1[account]:>3} {pos_v2[account]:>3} {pos_net[account]:>4}"
        )
    groups = collections.Counter(
        (int(e["wins"]), int(e["games"]) - int(e["wins"])) for e in players.values()
    )
    tied = {record: n for record, n in groups.items() if n > 1}
    print(f"  并列：{len(tied)} 组完全相同的战绩，涉及 {sum(tied.values())} 人"
          f"（最大一组 {max(tied.values()) if tied else 0} 人共用一个名次）")
    same_score = collections.Counter(points_of(e) for e in players.values())
    collide = sum(n for n in same_score.values() if n > 1)
    print(f"  同分：{collide} 人落在有并列分数的分值上，同分内部按胜场数（也就是场数）排序")
    moved = sum(1 for i, account in enumerate(current) if pos_net[account] != i + 1)
    shift = max(abs(pos_net[account] - i - 1) for i, account in enumerate(current))
    print(f"  与上一版「净胜场」相比：{moved} 人名次不同，最大位移 {shift} 名")
    print(f"  前三名的场数：{[int(players[a]['games']) for a in current[:3]]}")


def report_properties(players) -> None:
    order = rank_points(players)
    labels = rank_labels(order, players)
    rank_of = {account: labels[i] for i, account in enumerate(order)}
    break_even = LOSS_POINTS / (WIN_POINTS + LOSS_POINTS)
    header("积分规则的四条性质")
    print(f"  1) 积分 = 场数 × ({WIN_POINTS + LOSS_POINTS}×胜率 − {LOSS_POINTS})，盈亏平衡胜率 = {break_even:.1%}")
    above = sum(1 for e in players.values() if e["wins"] / e["games"] > break_even)
    below = sum(1 for e in players.values() if e["wins"] / e["games"] < break_even)
    zero = [name_of(players, a) for a, e in players.items() if points_of(e) == 0]
    print(f"     高于这条线 {above} 人（多打就涨分），低于 {below} 人（多打会掉分）")
    print(f"     积分正好 0 分（等于初始分）的有 {len(zero)} 人：{zero[:6] or '无'}")
    print(f"  2) 每打一局的得失对每个人恒定：赢 +{WIN_POINTS}、输 −{LOSS_POINTS}，与当前名次、场数无关")
    for wins, losses in ((6, 1), (3, 0), (5, 4), (0, 3)):
        here = wins * WIN_POINTS - losses * LOSS_POINTS
        print(f"     {wins}胜{losses}负 = {here} 分：赢一局 → {here + WIN_POINTS}（+{WIN_POINTS}）；输一局 → {here - LOSS_POINTS}（−{LOSS_POINTS}）")
    print(f"     期望增益 = 场数 × ({WIN_POINTS + LOSS_POINTS}p − {LOSS_POINTS}) / 场数 = ({WIN_POINTS + LOSS_POINTS}p − {LOSS_POINTS})，只跟真实胜率 p 有关，不随场数衰减")
    g = math.gcd(WIN_POINTS, LOSS_POINTS)
    dw, dl = LOSS_POINTS // g, WIN_POINTS // g      # 同分 ⟺ a·Δw = b·Δl ⟺ (Δw, Δl) = (b/g, a/g)·t
    print(f"  3) 同分时比胜场数；同分 ⟺ (Δ胜, Δ负) = ({dw}, {dl})×t，也就是多打 {dw + dl} 场里 {dw} 胜 {dl} 负积分一分不变")
    print(f"     同分之下胜场多 ⟺ 场数多（w = (积分 + {LOSS_POINTS}n)/{WIN_POINTS + LOSS_POINTS}），所以同分处奖励出勤；比值 {WIN_POINTS}:{LOSS_POINTS} 最简为 {WIN_POINTS // g}:{LOSS_POINTS // g}")
    by_score: dict[int, list[int]] = collections.defaultdict(list)
    for account in order:
        by_score[points_of(players[account])].append(account)
    pairs = [v for v in by_score.values() if len(v) > 1]
    print(f"     本快照有 {len(pairs)} 个分值上有多人，例如：")
    for group in pairs[:3]:
        detail = "、".join(
            f"{name_of(players, a)} {int(players[a]['wins'])}胜{int(players[a]['games']) - int(players[a]['wins'])}负（第 {rank_of[a]} 名）"
            for a in group[:3]
        )
        print(f"     {points_of(players[group[0]])} 分：{detail}")
    print(f"  4) 只打 1 场且全胜的人只有 {WIN_POINTS} 分，靠规则本身就在榜尾，不需要人为门槛")
    one_game = [a for a, e in players.items() if e["games"] == 1 and e["wins"] == 1]
    print(f"     本快照 1 胜 0 负的 {len(one_game)} 人，名次 {sorted(rank_of[a] for a in one_game)}")


def report_rule_sensitivity(players) -> None:
    """把两个群规数字换成别的比例，榜单会怎么变：这就是「改群规」的实际后果。"""
    header("积分比例的敏感性（这两个数字是唯一的自由度，必须写进群规）")
    baseline = rank_points(players)
    pos = {account: i + 1 for i, account in enumerate(baseline)}
    for win, loss in (
        (WIN_POINTS, LOSS_POINTS),        # 现行
        (2 * WIN_POINTS, 2 * LOSS_POINTS),# 齐次性：同乘 2，榜应当完全一样
        (50, 35),                          # 上一版 v5 的比值
        (50, 30),                          # v4 的比值
        (50, 50),                          # 等价于净胜场（盈亏平衡 50%）
        (50, 20),
        (3, 1),
    ):
        order = sorted(
            players,
            key=lambda a: (-(int(players[a]["wins"]) * win - int(players[a]["games"] - int(players[a]["wins"])) * loss),
                           -int(players[a]["wins"]), name_of(players, a), a),
        )
        moved = sum(1 for i, a in enumerate(order) if pos[a] != i + 1)
        top = order[0]
        be = loss / (win + loss)
        print(f"     +{win}/−{loss}（盈亏平衡 {be:.1%}）：与现行榜不同 {moved:>2} 人，榜首 {name_of(players, top)} "
              f"{int(players[top]['wins'])}胜{int(players[top]['games']) - int(players[top]['wins'])}负")
    a, b = WIN_POINTS, LOSS_POINTS
    six_one = next(a_ for a_, e in players.items() if int(e["wins"]) == 6 and int(e["games"]) == 7)
    p61, p84 = 6 * a - 1 * b, 8 * a - 4 * b
    print(f"     判据一：盈亏平衡胜率 p* = b/(a+b) = {b}/{a + b} = {b / (a + b):.4f}")
    print(f"     判据二：8胜4负 压过 6胜1负 需要 a/b > 3/2。现在 a/b = {a / b:.4f}"
          f"（{'满足' if a / b > 1.5 else '不满足'}），于是 6胜1负({p61}) 与 8胜4负({p84}) 里 "
          f"{('8胜4负' if p84 > p61 else '6胜1负')} 在前")
    print(f"     现行规则下：力量护腕（6胜1负）第 {pos[six_one]} 名")


def report_why_not_smoothed() -> None:
    def kstar(w1, n1, w2, n2):
        return 2 * (w2 * n1 - w1 * n2) / (2 * (w1 - w2) + (n2 - n1))

    header("为什么放弃带先验常数的 v1/v2（(w + k/2)/(n + k)）")
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
    print("  d) 1 胜 0 负 与 2 胜 1 负：收缩式里两者的交叉相乘之差恒等于 1，1 胜 0 负 永远在前，与 k 无关；")
    print(f"     积分规则下是 {WIN_POINTS} 分 vs {2 * WIN_POINTS - LOSS_POINTS} 分，2 胜 1 负 在前，不需要任何系数。")


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


def report_kda(players) -> None:
    """参考列「总 K/D/A」：当前快照里每一局的杀/死/助分别相加，与排序无关。"""
    header("参考列：总 K/D/A（不参与积分与排名）")
    killed = sum(int(e.get("kills", 0)) for e in players.values())
    died = sum(int(e.get("deaths", 0)) for e in players.values())
    assisted = sum(int(e.get("assists", 0)) for e in players.values())
    covered = sum(int(e.get("kda_games", 0)) for e in players.values())
    slots_total = sum(int(e["games"]) for e in players.values())
    print(f"  联盟合计：杀 {killed} · 死 {died} · 助 {assisted}；覆盖 {covered}/{slots_total} 个可归属位置")
    order = rank_points(players)
    labels = rank_labels(order, players)
    rank_of = {account: labels[i] for i, account in enumerate(order)}
    top = sorted(
        players,
        key=lambda a: (-int(players[a].get("kills", 0)), -int(players[a].get("assists", 0)), a),
    )[:5]
    print("  按总击杀前五（括号里是积分榜名次与积分）：")
    for account in top:
        entry = players[account]
        kills, deaths, assists = (int(entry["kills"]), int(entry["deaths"]), int(entry["assists"]))
        print(f"     {name_of(players, account):<18} {kills}/{deaths}/{assists}"
              f"  （第 {rank_of[account]} 名，{points_of(entry)} 分）")
    print("  这一列只做展示：K/D/A 与胜负不是线性关系，把它加进积分就会把主观判断塞进排名。")


def report_attendance(players) -> None:
    busiest = max(e["games"] for e in players.values())
    header("出勤与主榜的边界")
    print(f"  积分本身已经奖励出勤（积分 = 场数 × ({WIN_POINTS + LOSS_POINTS}×胜率 − {LOSS_POINTS})），不需要再加出勤权重，也不需要人为资格线。")
    print(f"  最活跃者 {busiest} 场；积分榜前五名的场数：{[int(players[a]['games']) for a in rank_points(players)[:5]]}")
    print(f"  再把出勤按每场加分塞进主榜，榜就会变成「谁更闲」：积分 + μ × 场数")
    for mu in (0, 5, 10, 20, 30):
        order = sorted(players, key=lambda a: -(points_of(players[a]) + mu * players[a]["games"]))[:6]
        worst = min(players[a]["wins"] / players[a]["games"] for a in order)
        print(f"     μ={mu:<3} 前六名里最低胜率 {worst:.0%}")
    iron = sorted(players, key=lambda a: (-int(players[a]["games"]), -int(players[a]["wins"])))[:6]
    print("  独立的出勤榜（只比场数，只增不减）前列：")
    for account in iron:
        entry = players[account]
        print(f"     {name_of(players, account):<18} {int(entry['games']):>2} 场（{int(entry['wins'])}胜{int(entry['games']) - int(entry['wins'])}负）")


def main() -> None:
    players, rounds, slots, metadata = load()
    report_scope(players, rounds, slots, metadata)
    report_standings(players)
    report_properties(players)
    report_rule_sensitivity(players)
    report_why_not_smoothed()
    report_permutation(players, rounds)
    report_power()
    report_attendance(players)
    report_kda(players)


if __name__ == "__main__":
    main()
