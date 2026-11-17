"""排名算法回归测试。

看板是纯前端排序，没有构建步骤，所以这里不重写一份算法来"自己测自己"：直接把
site/app.js 里的先验常量和 comparePlayerRanking 原样抠出来交给 node 跑，
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

PRIOR_BLOCK = re.compile(
    r"const RANKING_PRIOR_WINS = \d+;\n"
    r"const RANKING_PRIOR_LOSSES = \d+;\n"
    r"const RANKING_PRIOR_GAMES = [^;]+;"
)
COMPARATOR = re.compile(r"function comparePlayerRanking\(left, right\) \{[\s\S]*?\n\}")

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
    priors = PRIOR_BLOCK.search(source)
    comparator = COMPARATOR.search(source)
    assert priors and comparator, "site/app.js 里找不到排名先验常量或 comparePlayerRanking，改名了？"
    script = "\n".join(
        [
            priors.group(0),
            comparator.group(0),
            f"const cases = {json.dumps(cases, ensure_ascii=False)};",
            "console.log(JSON.stringify(cases.slice().sort(comparePlayerRanking).map((p) => p.id)));",
        ]
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"node 执行失败：{result.stderr.strip()}"
    return json.loads(result.stdout)


def test_more_games_beat_a_shorter_perfect_record() -> None:
    """核心诉求：打满 7 局 6 胜 1 负要排在只打 3 局全胜的人前面。"""
    assert ranked_ids([player(1, 6, 1), player(2, 3, 0)]) == [1, 2]


def test_a_single_game_cannot_reach_the_podium() -> None:
    """打 1 场全胜的新人可以排在前面被看见，但不能挤进前三把冠军奖杯拿走。

    注：加法先验做不到把 2 胜 1 负 排到 1 胜 0 负 前面——比较会退化成 3 < 2，
    与 k 取多少无关。真要让"没打满的人不许争冠"，靠的是场次门槛，不是胜率公式。
    """
    cases = [  # 期望：6胜1负 > 3胜0负 > 5胜2负 > 3胜1负 > 1胜0负 > 5胜4负 > 6胜6负
        player(1, 6, 1),
        player(2, 5, 2),
        player(3, 3, 0),
        player(4, 3, 1),
        player(5, 1, 0),
        player(6, 5, 4),
        player(7, 6, 6),
    ]
    order = ranked_ids(cases)
    assert order == [1, 3, 2, 4, 5, 6, 7], f"实际顺序：{order}"
    assert order[:3] == [1, 3, 2], f"前三应该由打得多的人占据，实际顺序：{order}"
    assert order.index(5) > 3, "只打 1 场的人不该进前三"


def test_a_win_always_helps_and_a_loss_always_hurts() -> None:
    """任何战绩下，多赢一局名次只升不降，多输一局只降不升，不会出现"别去打了"的死角。"""
    grid = [(w, l) for w in range(9) for l in range(9) if w + l > 0]
    ids = {w * 100 + l: player(w * 100 + l, w, l) for w, l in grid}
    order = ranked_ids(list(ids.values()))
    position = {player_id: index for index, player_id in enumerate(order)}

    for wins, losses in grid:
        here = position[wins * 100 + losses]
        if wins + 1 <= 8:
            assert position[(wins + 1) * 100 + losses] < here, f"{wins}胜{losses}负 多赢一局反而掉名次"
        if losses + 1 <= 8:
            assert position[wins * 100 + losses + 1] > here, f"{wins}胜{losses}负 多输一局反而涨名次"


def test_ties_go_to_the_player_with_more_games() -> None:
    """修正胜率打平时（3 胜 0 负和 5 胜 1 负都算 0.667），胜场多的在前。"""
    assert ranked_ids([player(1, 3, 0), player(2, 5, 1)]) == [2, 1]


def test_page_explanation_matches_the_code() -> None:
    """页面上写给群友看的公式必须和代码里的先验一致，防止说明和实现各改各的。"""
    source = APP_JS.read_text("utf-8")
    prior_wins = int(re.search(r"RANKING_PRIOR_WINS = (\d+);", source).group(1))
    prior_losses = int(re.search(r"RANKING_PRIOR_LOSSES = (\d+);", source).group(1))
    expected = f"（胜场 + {prior_wins}）÷（总场数 + {prior_wins + prior_losses}）"
    assert expected in INDEX_HTML.read_text("utf-8"), f"index.html 的排名说明还停留在旧公式：{expected}"
