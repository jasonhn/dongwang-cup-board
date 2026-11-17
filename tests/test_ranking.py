"""排名标准的回归测试。

标准是"净胜场 → 胜场数 → 昵称 → Account ID"，没有任何可调参数。测试不重写一份算法，
而是把 site/app.js 里线上那份 comparePlayerRanking 抠出来交给 node 跑，
保证测的就是浏览器里那份代码。
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

COMPARATOR = re.compile(r"function comparePlayerRanking\(left, right\) \{[\s\S]*?\n\}")
RANKS = re.compile(r"function rankNumbers\(players\) \{[\s\S]*?\n\}")

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


def ranked_ids(cases: list[dict[str, object]]) -> list[int]:
    """按 app.js 的排序跑一遍，返回 account id 的先后顺序。"""
    source = APP_JS.read_text("utf-8")
    comparator = COMPARATOR.search(source)
    assert comparator, "site/app.js 里找不到 comparePlayerRanking，改名了？"
    script = "\n".join(
        [
            comparator.group(0),
            f"const cases = {json.dumps(cases, ensure_ascii=False)};",
            "console.log(JSON.stringify(cases.slice().sort(comparePlayerRanking).map((p) => p.id)));",
        ]
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"node 执行失败：{result.stderr.strip()}"
    return json.loads(result.stdout)


def rank_labels(cases: list[dict[str, object]]) -> list[int]:
    """按 app.js 的名次函数跑一遍，返回每个人显示的名次（并列时共用一个数字）。"""
    source = APP_JS.read_text("utf-8")
    comparator, ranks = COMPARATOR.search(source), RANKS.search(source)
    assert comparator and ranks, "site/app.js 里找不到 comparePlayerRanking / rankNumbers"
    script = "\n".join(
        [
            comparator.group(0),
            ranks.group(0),
            f"const cases = {json.dumps(cases, ensure_ascii=False)};",
            "console.log(JSON.stringify(rankNumbers(cases.slice().sort(comparePlayerRanking))));",
        ]
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"node 执行失败：{result.stderr.strip()}"
    return json.loads(result.stdout)


def test_no_tunable_constant_is_left_in_the_code() -> None:
    """标准必须零参数：一旦留下可调系数，排名就又变成"管理员说了算"。"""
    source = APP_JS.read_text("utf-8")
    leftovers = re.findall(r"RANKING_PRIOR_\w+|RANKING_WEIGHT|const\s+\w*(?:WEIGHT|LAMBDA)\s*=\s*\d", source)
    assert not leftovers, f"site/app.js 里又出现可调参数：{leftovers}"
    body = COMPARATOR.search(source).group(0)
    code = "\n".join(line for line in body.splitlines() if not line.strip().startswith("//"))
    assert "/" not in code, "comparePlayerRanking 不该再做除法：净胜场只用整数相减，也就不会有浮点误差"


def test_more_games_beat_a_shorter_perfect_record() -> None:
    """最初那条抱怨：7 局 6 胜 1 负（净 5）要排在 3 局全胜（净 3）前面。"""
    assert ranked_ids([player(1, 6, 1), player(2, 3, 0)]) == [1, 2]


def test_a_single_lucky_win_cannot_reach_the_podium() -> None:
    """1 局全胜净胜场只有 1，不靠人为门槛也进不了前三。"""
    cases = [
        player(1, 6, 1),   # 净 5
        player(2, 5, 2),   # 净 3
        player(3, 3, 0),   # 净 3，胜场比上一位少，排其后
        player(4, 3, 1),   # 净 2
        player(5, 1, 0),   # 净 1，只有 1 场
        player(6, 5, 4),   # 净 1，但胜场多，排在 1 胜 0 负 之前
        player(7, 6, 6),   # 净 0：五成选手打满也还是 0
    ]
    assert ranked_ids(cases) == [1, 2, 3, 4, 6, 5, 7]


def test_equal_net_go_to_more_wins_then_more_games() -> None:
    """净胜场相同（都是 1）时，赢得多、打得多的人在前。"""
    assert ranked_ids([player(1, 1, 0), player(2, 2, 1), player(3, 5, 4)]) == [3, 2, 1]
    # 净胜场与胜场数都相等时负场也必然相等，只剩昵称兜底：顺序与传入顺序无关。
    pair = [player(1, 4, 2), player(2, 4, 2)]
    assert ranked_ids(pair) == ranked_ids(list(reversed(pair)))


def test_identical_records_share_one_rank() -> None:
    """战绩完全相同的人并列：昵称字典序只决定显示顺序，不决定谁的名次更低。"""
    # 2 胜 1 负 靠胜场数压住三个 1 胜 0 负，后三者并列第 2。
    cases = [player(1, 1, 0), player(2, 1, 0), player(3, 1, 0), player(4, 2, 1)]
    assert rank_labels(cases) == [1, 2, 2, 2]
    # 名次数字不因为并列而被跳过：并列之后紧接的名次就是它在队列里的位置。
    crowded = [player(1, 3, 0), player(2, 1, 0), player(3, 1, 0), player(4, 0, 3)]
    assert rank_labels(crowded) == [1, 2, 2, 4]
    # 并列人数不受上限约束：同战绩的 5 个人全都同一个名次。
    assert rank_labels([player(i, 2, 2) for i in range(1, 6)]) == [1, 1, 1, 1, 1]


def test_a_win_always_helps_and_a_loss_always_hurts() -> None:
    """任何战绩下，多赢一局名次只升不降、多输一局只降不升；每人每局的杠杆都是同一个 ±1。"""
    grid = [(w, l) for w in range(9) for l in range(9) if w + l > 0]
    order = ranked_ids([player(w * 100 + l, w, l) for w, l in grid])
    position = {player_id: index for index, player_id in enumerate(order)}

    for wins, losses in grid:
        here = position[wins * 100 + losses]
        if wins + 1 <= 8:
            assert position[(wins + 1) * 100 + losses] < here, f"{wins}胜{losses}负 多赢一局反而掉名次"
        if losses + 1 <= 8:
            assert position[wins * 100 + losses + 1] > here, f"{wins}胜{losses}负 多输一局反而涨名次"


def test_page_explanation_matches_the_rule() -> None:
    """页面上写给群友看的口径必须和代码一致，防止说明与实现各改各的。"""
    assert "净胜场 = 胜场 − 负场" in INDEX_HTML.read_text("utf-8"), (
        "index.html 的排名说明没跟上代码里的标准"
    )
