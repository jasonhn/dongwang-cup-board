"""排名标准的回归测试。

标准是联赛积分规则：初始 0 分，赢一局 +50，输一局 −30，按积分降序排；同分比胜场数；
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


def test_points_rule_is_the_published_one() -> None:
    """积分规则是群规：赢 +50、输 −30，初始 0 分。改数字必须连页面说明一起改。"""
    text = APP_JS.read_text("utf-8")
    assert WIN_VALUE.search(text).group(1) == "50", "赢一局加分不是 50 了"
    assert LOSS_VALUE.search(text).group(1) == "30", "输一局扣分不是 30 了"
    page = INDEX_HTML.read_text("utf-8")
    assert "赢一局 +50 分、输一局 −30 分" in page, "页面说明没跟上积分规则"
    assert "37.5%" in page, "页面说明没写盈亏平衡胜率（30 ÷ 80 = 37.5%）"

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
    """积分 = 50×胜 − 30×负 = 场数 × (80×胜率 − 30)。"""
    cases = [player(1, 6, 1), player(2, 3, 0), player(3, 6, 6), player(4, 1, 3)]
    assert points_of(cases) == [270, 150, 120, -40]


def test_more_games_beat_a_shorter_perfect_record() -> None:
    """最初那条抱怨：7 局 6 胜 1 负（270 分）要排在 3 局全胜（150 分）前面。"""
    assert ranked_ids([player(1, 6, 1), player(2, 3, 0)]) == [1, 2]


def test_equal_points_go_to_the_player_who_played_more() -> None:
    """50:30 会让不同战绩同分（3 胜 0 负与 6 胜 5 负都是 150 分）：同分比胜场数。

    同分之下胜场多 ⟺ 场数多（w = (积分 + 30n)/80），所以同分处奖励的是出勤。
    """
    assert ranked_ids([player(1, 3, 0), player(2, 6, 5)]) == [2, 1]
    # 同分且胜场数也相等 ⇒ 负场必然相等 ⇒ 战绩完全相同，顺序与传入顺序无关。
    pair = [player(1, 4, 3), player(2, 4, 3)]
    assert ranked_ids(pair) == ranked_ids(list(reversed(pair)))


def test_a_single_lucky_win_cannot_reach_the_podium() -> None:
    """1 局全胜只有 50 分，靠规则本身就被压在多赢过的人后面，不需要人为门槛。"""
    cases = [
        player(1, 6, 1),   # 270 分
        player(2, 5, 2),   # 190 分
        player(3, 3, 0),   # 150 分
        player(4, 3, 1),   # 120 分，胜场比下面那位少
        player(5, 1, 0),   #  50 分，只有 1 场
        player(6, 5, 4),   # 130 分
        player(7, 6, 6),   # 120 分：五成选手打满也只有 120 分
    ]
    assert ranked_ids(cases) == [1, 2, 3, 6, 7, 4, 5]


def test_break_even_win_rate_is_37_5_percent() -> None:
    """盈亏平衡胜率 30 ÷ 80 = 37.5%：高于这条线的人多打涨分，低于的人多打掉分。"""
    # 1 胜 0 负（50 分）：按 60% 继续打到 3 胜 2 负（90 分）是涨的。
    assert points_of([player(1, 1, 0), player(2, 3, 2)]) == [50, 90]
    # 按 33% 继续打到 1 胜 3 负（−40 分）是掉的。
    assert points_of([player(1, 1, 0), player(2, 1, 3)]) == [50, -40]
    # 正好 37.5% 的人（3 胜 5 负）打完 8 场是 0 分，等于初始分。
    assert points_of([player(1, 3, 5)]) == [0]


def test_a_win_always_helps_and_a_loss_always_hurts() -> None:
    """任何战绩下，多赢一局名次只升不降（+50）、多输一局只降不升（−30），杠杆恒定。"""
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


def test_page_explanation_matches_the_code() -> None:
    """页面上写给群友看的口径必须和代码一致，防止说明与实现各改各的。"""
    page = INDEX_HTML.read_text("utf-8")
    assert "积分 = 胜场 × 50 − 负场 × 30" in page, "index.html 的排名说明没跟上代码里的规则"
    assert "并列同名次" in page, "页面没说明并列名次的规则"
    assert "个人胜率" not in page, "页面还留着已经去掉的那一列胜率"
    assert "组队胜率" in page, "页面找不到胜率那一列的说明"
    render = re.search(
        r"function renderStats\(players, teamById\) \{[\s\S]*?\n\}", APP_JS.read_text("utf-8")
    )
    assert render and render.group(0).count("makeRateCell(") == 1, "玩家表应该只渲染一列胜率"
    # 排序用的积分必须是表里看得见的一列，否则群友没法直接复算自己为什么排这里。
    assert '<th class="col-points">积分</th>' in page, "玩家表没有积分这一列"
    assert render and "pointsOf(player)" in render.group(0), "积分那一列没按 pointsOf() 渲染"
