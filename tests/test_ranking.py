"""排名标准的回归测试。

标准是联赛积分规则：初始 0 分，赢一局 +50，输一局 −40，按积分降序排；同分比胜场数；
胜、负两列完全相同的人并列同名次。测试不重写一份算法，而是把 site/app.js 里线上那份
pointsOf / comparePlayerRanking / rankNumbers 抠出来交给 node 跑，保证测的就是浏览器里那份代码。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "site" / "app.js"
INDEX_HTML = ROOT / "site" / "index.html"

POINTS = re.compile(r"function pointsOf\(player\) \{[\s\S]*?\n\}")
COMPARATOR = re.compile(r"function comparePlayerRanking\(left, right\) \{[\s\S]*?\n\}")
RANKS = re.compile(r"function rankNumbers\(players\) \{[\s\S]*?\n\}")
VISIBLE = re.compile(r"function getVisiblePlayers\(match\) \{[\s\S]*?\n\}")
CALCULATE = re.compile(r"function calculate\(matches\) \{[\s\S]*?\n\}")
READ_KDA = re.compile(r"function readKda\(source\) \{[\s\S]*?\n\}")
KDA_TOTALS = re.compile(r"function formatKdaTotals\(player\) \{[\s\S]*?\n\}")
WIN_VALUE = re.compile(r"const\s+POINTS_PER_WIN\s*=\s*(\d+)\s*;")
LOSS_VALUE = re.compile(r"const\s+POINTS_PER_LOSS\s*=\s*(\d+)\s*;")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="看板排序在前端，需要 node 才能跑这段代码"
)


def player(player_id: int, wins: int, losses: int) -> dict[str, object]:
    return {
        "id": player_id,
        "name": f"选手{player_id}",
        "wins": wins,
        "losses": losses,
        "total": wins + losses,
    }


def node(source: str) -> list[int]:
    result = subprocess.run(["node", "-e", source], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"node 执行失败：{result.stderr.strip()}"
    return json.loads(result.stdout)


def ranking_source(cases: list[dict[str, object]], expression: str) -> list[int]:
    """把 app.js 里那三个函数原样搬进 node，用给定表达式取结果。"""
    text = APP_JS.read_text("utf-8")
    blocks = [POINTS.search(text), COMPARATOR.search(text), RANKS.search(text)]
    assert all(blocks), "site/app.js 里找不到 pointsOf / comparePlayerRanking / rankNumbers"
    consts = re.findall(r"const\s+POINTS_PER_(?:WIN|LOSS)\s*=\s*\d+\s*;", text)
    assert len(consts) == 2, "site/app.js 里找不到每局加减分这两个群规数字"
    script = "\n".join(
        [
            *consts,
            blocks[0].group(0),
            blocks[1].group(0),
            blocks[2].group(0),
            f"const cases = {json.dumps(cases, ensure_ascii=False)};",
            f"console.log(JSON.stringify({expression}));",
        ]
    )
    return node(script)


def ranked_ids(cases: list[dict[str, object]]) -> list[int]:
    return ranking_source(cases, "cases.slice().sort(comparePlayerRanking).map((p) => p.id)")


def rank_labels(cases: list[dict[str, object]]) -> list[int]:
    return ranking_source(cases, "rankNumbers(cases.slice().sort(comparePlayerRanking))")


def points_of(cases: list[dict[str, object]]) -> list[int]:
    return ranking_source(cases, "cases.map((p) => pointsOf(p))")


def slot(
    account_id: int, player_slot: int, kills: object, deaths: object, assists: object
) -> dict[str, object]:
    return {
        "account_id": account_id,
        "player_slot": player_slot,
        "personaname": f"选手{account_id}",
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
    }


def game(match_id: int, radiant_win: bool, players: list[dict[str, object]]) -> dict[str, object]:
    return {
        "match_id": match_id,
        "start_time": 1_700_000_000 + match_id,
        "radiant_win": radiant_win,
        "players": players,
    }


def kda_totals(matches: list[dict[str, object]]) -> list[dict[str, object]]:
    """把 app.js 里线上那份 calculate() 与 formatKdaTotals() 交给 node 跑。"""
    text = APP_JS.read_text("utf-8")
    consts = re.findall(r"const\s+POINTS_PER_(?:WIN|LOSS)\s*=\s*\d+\s*;", text)
    blocks = [
        POINTS.search(text),
        COMPARATOR.search(text),
        VISIBLE.search(text),
        READ_KDA.search(text),
        CALCULATE.search(text),
        KDA_TOTALS.search(text),
    ]
    assert all(blocks), "site/app.js 里找不到 calculate / getVisiblePlayers / readKda / formatKdaTotals"
    expression = (
        "calculate(matches).players.map((p) => {"
        "  const totals = formatKdaTotals(p);"
        "  return {"
        "    id: p.accountId, wins: p.wins, losses: p.losses, games: p.total,"
        "    kdaGames: p.kdaGames, text: totals ? totals.text : null,"
        "    label: totals ? totals.label : null"
        "  };"
        "})"
    )
    script = "\n".join(
        [
            *consts,
            *[block.group(0) for block in blocks],
            f"const matches = {json.dumps(matches, ensure_ascii=False)};",
            f"console.log(JSON.stringify({expression}));",
        ]
    )
    return node(script)


def by_id(rows: list[dict[str, object]]) -> dict[int, dict[str, object]]:
    return {int(row["id"]): row for row in rows}


def test_kda_totals_sum_every_game() -> None:
    """「总 K/D/A」＝当前范围内每一局的杀/死/助分别相加，与胜负无关。"""
    matches = [
        game(1, True, [slot(1, 0, 2, 1, 3), slot(2, 1, 4, 2, 5), slot(3, 2, 0, 6, 1)]),
        game(2, True, [slot(1, 128, 4, 2, 5), slot(2, 129, 1, 1, 1), slot(3, 130, 9, 0, 2)]),
    ]
    rows = by_id(kda_totals(matches))
    assert rows[1]["text"] == "6/3/8", "1 号玩家的 K/D/A 合计不是两局相加"
    assert rows[1]["wins"] == 1 and rows[1]["losses"] == 1
    assert rows[2]["text"] == "5/3/6"
    assert rows[3]["text"] == "9/6/3"
    assert rows[1]["label"] == "K/D/A 合计 6 杀 3 死 8 助攻 · 已汇总 2/2 场"


def test_kda_missing_games_are_counted_but_not_summed() -> None:
    """老快照缺 KDA 字段时：场次照算、合计不进入，页面要说清覆盖了几场，不许造假 0。"""
    matches = [game(1, True, [slot(1, 0, 3, 1, 2)]), game(2, True, [slot(1, 0, None, None, None)])]
    row = by_id(kda_totals(matches))[1]
    assert row["games"] == 2 and row["kdaGames"] == 1
    assert row["text"] == "3/1/2"
    assert row["label"] == "K/D/A 合计 3 杀 1 死 2 助攻 · 已汇总 1/2 场"


def test_kda_does_not_change_the_ranking() -> None:
    """KDA 只是参考列：刷 KDA 不会改变积分与名次。"""
    matches = [
        game(1, True, [slot(1, 0, 1, 5, 1), slot(2, 129, 20, 0, 20)]),
        game(2, True, [slot(1, 0, 1, 5, 1), slot(2, 129, 20, 0, 20)]),
    ]
    rows = kda_totals(matches)
    assert rows[0]["wins"] == 2 and rows[0]["losses"] == 0
    assert rows[1]["wins"] == 0 and rows[1]["losses"] == 2
    assert rows[0]["text"] == "2/10/2" and rows[1]["text"] == "40/0/40"
    assert [int(row["id"]) for row in rows] == [1, 2], "刷 KDA 不该改变名次：排序只看积分与胜场数"


def test_points_rule_is_the_published_one() -> None:
    """积分规则是群规：赢 +50、输 −40，初始 0 分。这两个数字只在代码与测试里钉死，
    网页上不写规则说明（群友看表里的「积分」列就能复算），公示口径写在 README 与 docs。"""
    text = APP_JS.read_text("utf-8")
    assert WIN_VALUE.search(text).group(1) == "50", "赢一局加分不是 50 了"
    assert LOSS_VALUE.search(text).group(1) == "40", "输一局扣分不是 40 了"
    # 积分规则之外不许再出现别的可调系数，也不许做除法（除法会引入浮点误差）。
    leftovers = re.findall(
        r"RANKING_PRIOR_\w+|RANKING_WEIGHT|\w*(?:WEIGHT|LAMBDA|PRIOR|COEFF)\s*=\s*\d", text
    )
    assert not leftovers, f"site/app.js 里出现了积分规则之外的可调系数：{leftovers}"
    body = POINTS.search(text).group(0) + COMPARATOR.search(text).group(0)
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
    assert "/" not in code, "积分与排序不该做除法：只用整数乘减，就不会因浮点误差跳名次"
    assert "total" not in code, "积分只由胜、负两列决定，不读总场数之类的派生量"


def test_points_match_the_formula() -> None:
    """积分 = 50×胜 − 40×负 = 场数 × (90×胜率 − 40)。"""
    cases = [player(1, 6, 1), player(2, 3, 0), player(3, 6, 6), player(4, 1, 3)]
    assert points_of(cases) == [260, 150, 60, -70]


def test_more_games_beat_a_shorter_perfect_record() -> None:
    """最初那条抱怨：7 局 6 胜 1 负（260 分）要排在 3 局全胜（150 分）前面。"""
    assert ranked_ids([player(1, 6, 1), player(2, 3, 0)]) == [1, 2]


def test_equal_points_go_to_the_player_who_played_more() -> None:
    """5:4 会让不同战绩同分（7 胜 0 负与 11 胜 5 负都是 350 分）：同分比胜场数。

    同分 ⟺ (Δ胜, Δ负) = (4t, 5t)，也就是多打 9 场里 4 胜 5 负积分不变；同分之下
    胜场多 ⟺ 场数多（w = (积分 + 40n)/90），所以同分处奖励的是出勤。
    """
    assert points_of([player(1, 7, 0), player(2, 11, 5)]) == [350, 350]
    assert ranked_ids([player(1, 7, 0), player(2, 11, 5)]) == [2, 1]
    # 同分且胜场数也相等 ⇒ 负场必然相等 ⇒ 战绩完全相同，顺序与传入顺序无关。
    pair = [player(1, 4, 3), player(2, 4, 3)]
    assert ranked_ids(pair) == ranked_ids(list(reversed(pair)))


def test_a_single_lucky_win_cannot_reach_the_podium() -> None:
    """1 局全胜只有 50 分，靠规则本身就被压在多赢过的人后面，不需要人为门槛。"""
    cases = [
        player(1, 6, 1),   # 260 分
        player(2, 5, 2),   # 170 分
        player(3, 3, 0),   # 150 分
        player(4, 3, 1),   # 110 分
        player(5, 1, 0),   #  50 分，只有 1 场
        player(6, 5, 4),   #  90 分
        player(7, 6, 6),   #  60 分：五成胜率打满 12 场也只有 60 分，仍排在 3 胜 1 负后面
    ]
    assert ranked_ids(cases) == [1, 2, 3, 4, 6, 7, 5]


def test_break_even_win_rate_is_44_4_percent() -> None:
    """盈亏平衡胜率 40 ÷ 90 = 4/9 ≈ 44.4%：高于这条线的人多打涨分，低于的人多打掉分。"""
    # 1 胜 0 负（50 分）：按 60% 继续打到 3 胜 2 负（70 分）是涨的。
    assert points_of([player(1, 1, 0), player(2, 3, 2)]) == [50, 70]
    # 按 33% 继续打到 1 胜 3 负（−70 分）是掉的。
    assert points_of([player(1, 1, 0), player(2, 1, 3)]) == [50, -70]
    # 正好 44.4% 的人（4 胜 5 负）打完 9 场是 0 分，等于初始分。
    assert points_of([player(1, 4, 5)]) == [0]


def test_a_win_always_helps_and_a_loss_always_hurts() -> None:
    """任何战绩下，多赢一局名次只升不降（+50）、多输一局只降不升（−40），杠杆恒定。"""
    grid = [(w, l) for w in range(9) for l in range(9) if w + l > 0]
    order = ranked_ids([player(w * 100 + l, w, l) for w, l in grid])
    position = {player_id: index for index, player_id in enumerate(order)}

    for wins, losses in grid:
        here = position[wins * 100 + losses]
        if wins + 1 <= 8:
            assert position[(wins + 1) * 100 + losses] < here, f"{wins}胜{losses}负 多赢一局反而掉名次"
        if losses + 1 <= 8:
            assert position[wins * 100 + losses + 1] > here, f"{wins}胜{losses}负 多输一局反而涨名次"


def test_identical_records_share_one_rank() -> None:
    """战绩完全相同的人并列：昵称字典序只决定显示顺序，不决定谁的名次更低。"""
    cases = [player(1, 1, 0), player(2, 1, 0), player(3, 1, 0), player(4, 2, 1)]
    assert rank_labels(cases) == [1, 2, 2, 2]
    # 名次数字不因为并列而被跳过：并列之后紧接的名次就是它在队列里的位置。
    crowded = [player(1, 3, 0), player(2, 1, 0), player(3, 1, 0), player(4, 0, 3)]
    assert rank_labels(crowded) == [1, 2, 2, 4]
    # 并列人数不受上限约束：同战绩的 5 个人全都同一个名次。
    assert rank_labels([player(i, 2, 2) for i in range(1, 6)]) == [1, 1, 1, 1, 1]


def test_table_columns_and_rendering_match_the_code() -> None:
    """玩家表的列与渲染必须和代码一致：网页不写规则说明，但排序量本身要看得见。"""
    page = INDEX_HTML.read_text("utf-8")
    assert "个人胜率" not in page, "页面还留着已经去掉的那一列胜率"
    assert "组队胜率" not in page, "胜率那一列已经改名，页面还留着旧列名"
    assert "<th>胜率</th>" in page, "玩家表没有名为「胜率」的那一列"
    assert "<th>总 K/D/A</th>" in page, "玩家表没有总 K/D/A 那一列"
    render = re.search(
        r"function renderStats\(players, teamById\) \{[\s\S]*?\n\}", APP_JS.read_text("utf-8")
    )
    assert render and render.group(0).count("makeRateCell(") == 1, "玩家表应该只渲染一列胜率"
    assert render and "formatKdaTotals(player)" in render.group(0), "总 K/D/A 那一列没按累加值渲染"
    # 排序用的积分必须是表里看得见的一列，否则群友没法直接复算自己为什么排这里。
    assert '<th class="col-points">积分</th>' in page, "玩家表没有积分这一列"
    assert render and "pointsOf(player)" in render.group(0), "积分那一列没按 pointsOf() 渲染"
