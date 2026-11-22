#!/usr/bin/env python3
"""重建标题字体的子集：site/fonts/title-display.woff2。

标题「懂王杯光荣榜」用的是自托管的毛笔楷体（Ma Shan Zheng，SIL OFL 1.1）。字体只截取页面
真正用到的汉字，所以整个文件只有一百多 KB；将来在 config.json 里改标题、或者页面上新增了
文案，只要新字形不在这份子集里，那几个字就会掉回系统楷体。改完文案重跑一次即可：

    .venv/bin/pip install fonttools brotli
    .venv/bin/python scripts/build_title_font.py

想换成别的字体，改下面 FONT_URL / FONT_FAMILY 两处，再同步 styles.css 里 @font-face 的
font-family。候选字体都在 https://fonts.google.com/ 且是 OFL 授权，可以自托管。
"""

from __future__ import annotations

import argparse
import string
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "site" / "fonts" / "title-display.woff2"

# 站酷/马善政等中文字体在 google/fonts 仓库里的位置（OFL 授权，允许自托管再分发）。
FONT_URL = "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/mashanzheng/MaShanZheng-Regular.ttf"

# 页面文本里抓不到的常用字：万一以后标题换成「第一届春季挑战赛」这种，别掉字。
EXTRA_CHARS = (
    "第届春夏秋冬赛季年度巅峰挑战赛荣誉榜冠亚季军队伍组排行积分胜负平加冕"
    "王座奖杯奖牌勋章庆典狂欢开黑兄弟战友泉水泡茶俱乐部微信群公告须知"
    "数据更新最新查看切换深色浅色模式加载请稍候正在读取战报暂时未能展开"
)

# 页面里所有会出现汉字的源文件：静态结构、前端文案、站点配置。
TEXT_SOURCES = ("site/index.html", "site/app.js", "config.json")


def collect_chars() -> str:
    found: set[str] = set()
    for rel in TEXT_SOURCES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        found.update(ch for ch in text if ord(ch) >= 0x2000)
    found.update(EXTRA_CHARS)
    # ASCII 可打印字符一并留下：标题里万一出现数字或连字符也不会掉回系统字体。
    found.update(string.printable)
    found.discard("\r")
    return "".join(sorted(found))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="本地 TTF/OTF 路径；省略时从上面的 FONT_URL 下载")
    parser.add_argument("--output", default=str(OUT), help="输出 woff2 路径")
    args = parser.parse_args()

    try:
        from fontTools import subset
    except ModuleNotFoundError as exc:  # pragma: no cover - 只在缺依赖时触发
        raise SystemExit("缺少 fonttools，请先执行：.venv/bin/pip install fonttools brotli") from exc

    if args.source:
        source = Path(args.source).expanduser().resolve()
    else:
        source = Path("/tmp/title-font-source.ttf")
        if not source.exists():
            print(f"下载字体源文件 {FONT_URL}")
            with urllib.request.urlopen(FONT_URL, timeout=120) as response:
                source.write_bytes(response.read())

    chars = collect_chars()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    subset.main(
        [
            str(source),
            f"--text={chars}",
            f"--output-file={out}",
            "--flavor=woff2",
            "--layout-features=",
            "--no-hinting",
            "--desubroutinize",
        ]
    )
    print(f"已写入 {out}：{len(chars)} 个字形，{out.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
