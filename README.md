# 内战光荣榜（Dota 2 内战 / 联赛公开看板）

一个纯静态的 Dota 2 内战战绩看板：按时间范围统计每个玩家的胜、负、总场数、个人胜率与组队胜率，点 Account ID 还能展开这名玩家和每个队友的同队场次与胜率。数据每 15 分钟从 OpenDota 自动刷新，部署在 GitHub Pages 上，群里直接发链接就能看。

派生自 <https://github.com/BCSZSZ/ustc-dota2-hall-of-fame>，在其基础上做了三件事：

1. 联赛 ID、看板标题、时区全部收进 `config.json`，换群不用改代码；
2. 新增 `manual` 模式：**没有官方联赛也能做看板**，把每场的 `match_id` 记进 `matches.txt` 即可；
3. 新增 `names.json`：用群昵称覆盖 Steam 昵称（Steam 名太长、还会被改名）；
4. 排名表显示玩家 **Steam 头像**，头像缓存在 `site/data/avatars.json`，抓不到就退化成首字母徽章；
5. 前三名显示 **冠军 / 亚军 / 季军奖杯**（金、银、铜），不再是数字 1、2、3；
6. 组队胜率并进同一张玩家表，点 Account ID 弹出队友详情；原来那个几百行几百列的矩阵删掉了，手机上不用左右拖。
7. 玩家表下面多了一个 **「全部比赛」战报存档**：一行一场（时间 + 比赛 ID），点三角形展开该局的天辉／夜魇阵容、中文英雄名、每人 KDA（杀/死/助）与胜负；英雄名缓存在 `site/data/heroes.json`。
8. 排名不只看胜率，用 **修正胜率 =（胜场 + 3）÷（总场数 + 6）**，所以 7 局 6 胜 1 负 排在 3 局全胜 前面；参数含义、取值依据和已知边界见「排名算法」。

## 五分钟落地

### 1. Fork 并改成你自己的仓库

在 GitHub 上 Fork 本仓库（或 `git clone` 后 push 到你的新仓库）。GitHub Pages 会发布到 `https://<你的用户名>.github.io/<仓库名>/`。

### 2. 改 `config.json`

```json
{
  "collect_mode": "league",
  "league_id": 19224,
  "manual_match_file": "matches.txt",
  "refresh_recent": 5,
  "output": "site/data/league.json",
  "time_zone": "Asia/Shanghai",
  "names_file": "names.json",
  "avatars_file": "site/data/avatars.json",
  "heroes_file": "site/data/heroes.json",
  "max_new_avatars": 80,
  "site": {
    "title": "XX群光荣榜",
    "subtitle": "Dota 2 内战战绩",
    "kicker": "内战有喜 · 荣耀登榜",
    "footer": "XX群光荣榜 · 胜负皆尽兴，开黑常有喜",
    "description": "看板描述（搜索引擎可见）"
  }
}

```

| 字段 | 作用 |
|---|---|
| `collect_mode` | `league`：只按联赛 ID 归集；`manual`：只按 `matches.txt` 手动登记的 `match_id`；`both`：两者合并统计；`tournament`：用 Valve 官方接口按联赛自动收锦标赛比赛（需要 `STEAM_WEB_API_KEY`） |
| `league_id` | `league`、`both` 或 `tournament` 模式下必填。申请 Amateur League 后拿到，也可以用 `--league-name` 反查 |
| `manual_match_file` | `manual` 模式的比赛清单，每行一个 `match_id`，`#` 开头是注释 |
| `refresh_recent` | 每次刷新重新抓最近 N 场（补全还没解析完的比赛），其余用仓库里已有的缓存，省 API 额度 |
| `time_zone` | 看板显示与输入框用的时区，例如 `Asia/Shanghai` |
| `names_file` | 群昵称映射文件 |
| `avatars_file` | Steam 头像缓存，键是 `account_id`，值是头像 URL；空字符串代表“这个人没有头像，别再抓” |
| `heroes_file` | 英雄名缓存，键是 `hero_id`，值形如 `{"zh": "裂魂人", "en": "Spirit Breaker", "slug": "spirit_breaker"}`，给战报存档显示英雄用 |
| `overrides_file` | 手工补录文件（默认 `overrides.json`），用来录入公开接口查不到的比赛，见下面「手工补录」 |
| `exclude_game_modes` | 不算内战的 `game_mode`，默认 `[21]`（Valve 的 1v1 solo 房）。就算被联赛归集进来也不计进看板，四种模式都生效 |
| `exclude_match_ids` | 按 `match_id` 排除的比赛（弃赛、误开的房间等），这些 ID 连请求都不会发，省额度 |
| `max_new_avatars` | 每次运行最多新查多少个玩家的头像（老玩家复用缓存，省 API 额度） |
| `max_tournament_matches` | `tournament` 模式一次最多导入多少场联赛比赛（默认 100，取最新的） |
| `site` | 看板标题、副标题、页脚等文案，前端运行时读取，不用改 HTML |

### 3. 本地跑一次，确认能出数据

需要 **Python 3.11 及以上**（脚本用了 `datetime.UTC`），macOS 自带的 3.9 跑不了。

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
cp .env.example .env          # 可选：填 OPENDOTA_API_KEY
.venv/bin/python collector.py
```

`.venv/` 不入库，所以换机器、或者本机 Python 被升级/卸载之后，`.venv/bin/python` 会变成断链（报 `no such file or directory`）。用 uv 重建一条命令就够：

```bash
uv venv --seed --python 3.13 .venv && uv pip install -r requirements.txt
```

> 如果 uv 下载 Python 时报 `Invalid gzip header`，是 `UV_PYTHON_INSTALL_MIRROR` 指向的镜像包坏了，换成默认源重试：`env -u UV_PYTHON_INSTALL_MIRROR uv venv --seed --python 3.13 .venv`。仓库里的 `.python-version` 已经把版本钉在 3.13，`uv venv` 会自动挑对解释器。

看到 `league 19224: wrote 7 matches ...` 就成功了。想直接看页面：

采集完比赛后，脚本会顺带抓这次出现的玩家的头像，并打印一行 `26 avatars cached (26 new) in site/data/avatars.json`。不想抓头像（比如只验证比赛数据）可以加 `--skip-avatars`。

```bash
.venv/bin/python -m http.server -d site 8080
```

然后打开 <http://localhost:8080>。

> **不要直接双击 `site/index.html`。** 用 `file://` 打开时浏览器禁止网页读取本地 JSON，页面会显示“战报暂时未能展开”。必须用上面的 HTTP 服务（或部署到 GitHub Pages）访问。

命令行参数会覆盖 `config.json`，方便临时试：

```bash
.venv/bin/python collector.py --mode manual --league-id 0
.venv/bin/python collector.py --league-id 42 --refresh-recent 10 --output /tmp/preview.json
.venv/bin/python collector.py --league-name "懂王杯"   # 按联赛名反查 league_id 并采集
.venv/bin/python collector.py --add-match 9085584594    # 登记一场新比赛并立刻采集
```

`--add-match` 会把 `match_id` 追加进 `matches.txt`（重复的会自动跳过、注释保留），然后接着采集，一条命令就能把刚打完的场次的加进看板。注意要填 **match_id**，客户端里显示的房间号（lobby id）不是它。

只想知道 ID、不写数据的话，看输出里的 `Resolved '懂王杯' to league id 19905`，再把它填进 `config.json`；名字不完全一致时会列出候选，换成完全一致的名字即可。

### 4. Push，开 GitHub Pages

本仓库用 GitHub Actions 部署（`.github/workflows/pages.yml`），所以 Pages 的来源必须切成 **GitHub Actions**：

仓库 **Settings → Pages → Build and deployment → Source = GitHub Actions**。新建仓库默认是 “Deploy from a branch”，不改这里的话 Actions 会在 deploy 步骤报错 “Pages not enabled”。

然后：

- push 到 `main` 即触发一次刷新 + 部署；
- 每 15 分钟自动刷新一次（cron 是 UTC 的 `*/15 * * * *`，也就是每刻钟的 00/15/30/45 分）。想改频率就改这一行，GitHub 的 schedule 最少只能 5 分钟一次；
- 也可以随时在 Actions 页面手动点 “Run workflow”。

部署完成后，那次运行的 deploy 步骤会打印地址，格式是 `https://<用户名>.github.io/<仓库名>/`。

可选：在仓库 Settings → Secrets and variables → Actions 里新建 `OPENDOTA_API_KEY`。脚本默认走匿名免费接口（约 60 次/分钟、3000 次/天），额度用尽才会改用带 Key 的请求，并且有 `OPENDOTA_MAX_PAID_CALLS` 上限保护。Key 只存在 Secret 或本地 `.env`，不会进仓库。

刷新改成每 15 分钟一次后，一天会跑约 96 次、每次大约 10～20 个 OpenDota 请求（只重抓最近 5 场和手动登记的场次，其余走快照缓存），大概吃掉匿名日额度的三到五成。额度被吃紧时配一个 Key 最省事；不想配就把 cron 调回 `0 * * * *`。

## 四种收数据的方式

### `league` 模式（最省心）

用联赛模式开的房间，比赛会自带 `leagueid`，OpenDota 按联赛自动归集，你什么都不用记。要拿到自己的 `league_id`，去 Valve 的联赛申请入口 <https://www.dota2.com/league/0/list> 申请 Amateur League，审批与费用以页面为准。小群不一定要折腾，先用现成联赛把看板跑顺也行。

联赛刚建好、还没打比赛时 `matchIds` 是空的，看板会显示“还没有战报入册”并给出下一步提示，不会报错。

联赛房间里也可能顺手开 solo（1v1 mid 同样会带上 `leagueid`），这类房间默认不算内战：`exclude_game_modes` 已经排除 `game_mode 21`，被跳过的场次会连原因一起记在 `league.json` 的 `metadata.excluded_matches` 里，不会静默消失。

### `manual` 模式（零门槛，最适合没有联赛的群）

1. `config.json` 里把 `collect_mode` 改成 `"manual"`；
2. 每打完一场，把 `match_id` 追加到 `matches.txt`（OpenDota 页面地址里就有，或在客户端比赛历史里复制）；
3. push，看板自动更新。

`manual` 模式不校验比赛属于哪个联赛，普通房间、开黑房间都能收，比赛按开始时间排序。

### `both` 模式（两边都要）

联赛自动归集加上 `matches.txt` 里手动登记的场次，一起统计；同一个 `match_id` 不会重复抓。适合“大部分比赛走联赛开房，偶尔有普通房间要补录”的群。

### `tournament` 模式（联赛被 OpenDota 标成 excluded 时用这个）

OpenDota 只归集它自己跟踪的联赛。Amateur League 经常被标成 `tier: "excluded"`，此时 `GET /api/leagues/<league_id>/matchIds` 会返回空数组，`league` 模式什么也抓不到——即使比赛确实是从联赛入口开的房。

`tournament` 模式改问 Valve：`IDOTA2Match_570/GetMatchHistory/v1/?league_id=<league_id>` 会列出这个联赛的全部比赛和真正的 `match_id`，然后逐场交给 OpenDota 解析（OpenDota 是按需解析的，第一次请求某场才会去 Valve 拉数据）。`matches.txt` 里手动登记的场次也会一起合并，已经收进快照的比赛不会被丢掉，也不会重复抓。

要用它需要两件事：

1. 一个免费的 **Steam Web API Key**：<https://steamcommunity.com/dev/apikey>（账号需非受限状态）。填进本地 `.env` 的 `STEAM_WEB_API_KEY`，GitHub 上则放到仓库 Settings → Secrets and variables → Actions 里的 `STEAM_WEB_API_KEY`。**不要**写进 `config.json`，那个文件会进仓库。
2. `config.json` 里把 `collect_mode` 改成 `"tournament"`，`league_id` 填你的联赛 ID。

命令行也可以直接试：

```bash
STEAM_WEB_API_KEY=你的Key .venv/bin/python collector.py --mode tournament --league-name "懂王杯"
```

注意：客户端 Settings → Game → Expose Public Match Data 必须开着，否则 Valve 不返回这场比赛的数据，采集会把它记成“尚未取得公开详情”。另外客户端比赛历史里显示的**房间号（lobby id）不是 match_id**，`tournament` 模式不需要你管这件事，手动登记时才要认准 match_id。

## 为什么联赛里打过的比赛没被收录

先查 OpenDota 有没有这场：`GET /api/leagues/<league_id>/matchIds`。如果这个列表是空的，有两种可能：

1. 这场比赛**没有带 `leagueid`**——十有八九是从自定义房间或普通房开的，不是从客户端「联赛」入口开的房。联赛名存在，不代表比赛会挂到它下面。
2. 这个联赛被 OpenDota 标成 `tier: "excluded"`（`GET /api/leagues/<league_id>` 能看出来），于是它的比赛一场都不会被归集。这种情况下即使你确实是从联赛开的房，`league` 模式也是空的，改用 `tournament` 模式（走 Valve 官方接口）就能收到。

开黑排天梯匹配（`game_mode 22`）永远不会带联赛标记，这类比赛只能靠 `matches.txt` 手动登记。

还有一个更根本的原因：**OpenDota 不会主动爬普通账号的比赛**。它是“按需解析”——有人第一次请求 `GET /api/matches/<match_id>` 时它才去 Valve 拉那一场。所以你们群里的新比赛在 OpenDota 里本来就是不存在的，不是它解析失败；`GET /api/players/<account_id>/matches` 只会列出已经被人请求过的场次。结论：

- 想自动拿到联赛的全部比赛，只能走 Valve 官方接口，也就是 `tournament` 模式（需要免费的 Steam Web API Key，见上面）。
- 不想配 Key 就手动登记 `match_id`，一登记 OpenDota 就会去解析这场，看板下次刷新就有了。

## 手工补录（国服 / 完美世界服务器的比赛）

在**完美世界服务器**打的比赛，Valve 的全球接口和 OpenDota 都查不到：实测 `GET /api/matches/<id>` 全是 404，玩家的比赛历史里也没有，`www.dota2.com.cn/api/*` 需要登录态和签名，`dotabuff`、`stratz` 挡在 Cloudflare 后面。只有完美世界电竞 APP 能看到自己参加的那一场。

这类比赛靠 `overrides.json` 手工录入（把 APP 里看到的数据照抄进来），采集时会合并进快照，`overrides.json` 里的手写数据优先于 OpenDota 返回的内容，并且会把这场从“尚未取得公开详情”里清掉：

```json
{
  "matches": [
    {
      "match_id": 9085584594,
      "start_time": 1794400000,
      "duration": 2400,
      "radiant_win": true,
      "players": [
        {"account_id": 129964895, "personaname": "Monkey see Monkey do", "player_slot": 0, "hero_id": 47, "kills": 9, "deaths": 10, "assists": 15},
        {"account_id": 335060654, "personaname": "专治不举郭大夫", "player_slot": 1, "hero_id": 96, "kills": 3, "deaths": 6, "assists": 21}
      ]
    }
  ]
}

```

字段说明：`start_time` 是 Unix 秒（北京时间换算成 UTC 秒），`duration` 是秒数，`player_slot` 天辉 0–4、夜魇 128–132，`hero_id` 用 OpenDota 的英雄 ID，`account_id` 用 Steam 64 位 ID 减去 `76561197960141072`。录入 10 名选手即可，缺 `account_id` 的位置会被当作匿名。`kills` / `deaths` / `assists` 可选，照 APP 里的战绩抄上就能在战报里显示 KDA，不填那一列就是 `—`。补录场次会记在 `metadata.manual_override_match_ids` 里，方便核对。

## 群昵称映射

`names.json` 里用 `account_id` 做键，值是群里叫的名字：

```json
{
  "137665114": "老王",
  "192820566": "小李"
}
```

`account_id` 就是看板表格里 Account ID 那一列（手机上一列放不下，它显示在玩家名字下面，同样可以点击），也可以从 OpenDota 玩家页面 URL 里拿到。设了昵称后，玩家改名不会影响看板显示；没设的玩家继续显示 Steam 昵称。

## 玩家头像

排名表在玩家名字左边显示 Steam 头像。头像由 `collector.py` 通过 OpenDota 的 `GET /api/players/<account_id>` 取得，结果写进 `site/data/avatars.json`：

```json
{
  "129964895": "https://avatars.steamstatic.com/....jpg",
  "137665114": ""
}
```

- 只有 `http(s)` 开头的 URL 会被前端使用；`""` 表示 OpenDota 没给出头像，脚本以后不会再为这个人花额度。
- 每次运行只查缓存里没有的玩家，上限 `max_new_avatars`（默认 80），老玩家直接复用。
- 头像只是装饰：`avatars.json` 缺失、玩家没头像、或者 Steam CDN 上那个地址失效（404）时，前端自动换成名字首字母的圆形徽章，看板照常显示，不会报错。
- GitHub Actions 会把 `site/data/league.json`、`site/data/avatars.json` 和 `site/data/heroes.json` 一起提交，所以缓存会一直累积；本地跑一次再 push 也能把新头像带上去。

## 战报存档与英雄名

玩家表下面的「全部比赛」面板列出已收录的每一场内战：一行只显示开始时间 + `match_id`（右边顺带胜负和时长），点行首的三角形展开该局详情——开始／结束时间、时长、赛制、可点开的 OpenDota 原页链接，以及天辉／夜魇两边的玩家、Account ID、英雄和 KDA。KDA 直接取 OpenDota 每场的 `kills` / `deaths` / `assists`，写成 `9/10/15`（9 杀 10 死 15 助），鼠标悬停能看到完整说明。列表跟随上方的时间筛选，默认「全部战绩」就是全部已收录比赛，倒序排列，右上角还能一键展开／收起。

KDA 是后加的字段，所以老快照里没有这三个数字：采集脚本发现某场的阵容缺 KDA 时会**重新抓一次**那场（每场只补一次，补过之后哪怕拿到的是空值也不会再花额度），下次刷新就把历史比赛的 KDA 全部补齐。确实取不到数字的位置显示 `—`，不会当成 0/0/0。

比赛数据里只有 `hero_id`，中文名由 `collector.py` 写进 `site/data/heroes.json`：

```json
{
  "1": { "zh": "敌法师", "en": "Anti-Mage", "slug": "antimage" },
  "71": { "zh": "裂魂人", "en": "Spirit Breaker", "slug": "spirit_breaker" }
}
```

- `zh` 取自 Valve 官网的 `datafeed/herolist?language=schinese`（免 key），`en` 和完整 ID 列表取自 OpenDota 的 `GET /api/heroes`，`slug` 是英雄的内部名去掉 `npc_dota_hero_` 前缀，前端用它拼 Steam CDN 的英雄图标。
- **拼图标只能用 `slug`，不能用英文名小写**：11 号英雄的 slug 是 `nevermore`，不是 `shadow_fiend`。
- 英雄表一年才改几次，但每次采集都会顺带校对一遍；两个来源都挂了就保留磁盘上的旧文件继续跑，Valve 挂了就临时用英文名顶上，都不会让采集失败。`--skip-heroes` 可以跳过这一步。
- 前端读不到 `heroes.json` 时退化成 `#hero_id`，存档照常能看。

## 排名算法

排名按**修正胜率**排序，公式在 `site/app.js` 的 `comparePlayerRanking()`：

```
修正胜率 = (胜场 + 3) ÷ (总场数 + 6)
```

相当于赛季开始前先给每人记 **3 胜 3 负**（内战每局五五开，联盟平均胜率恒等于 50%），之后每打一局，真实胜率才把名次往本人水平拉：打到 6 局时先验只占一半权重，20 局时基本就是他自己的胜率。表格里显示的还是**真实胜率**，修正值只用来排序。同分依次比 胜场 → 总场数 → 昵称，所以同样 5 成的选手里打得多的人靠前。

**为什么不直接用胜率**：7 局 6 胜（86%）会排在 3 局全胜（100%）后面，等于告诉群友"打得多排名低"。老算法用的是 1 胜 1 负的先验，在这个才十几场的规模上压不住连胜的运气。

**为什么先验取 6 而不是更大**：加法先验有个可解析的临界点，设两人 (w₁,n₁)、(w₂,n₂)（n₂ 场更多），场多的人反超所需的先验强度是

```
α* = (w₂·n₁ − w₁·n₂) / ( 2·(w₁ − w₂) + (n₂ − n₁) )        // 先验 = α 胜 α 负，k = 2α
```

| 想让谁排前面 | 需要 k = 2α* |
|---|---|
| 6 胜 1 负 > 3 局全胜 | 3.0 |
| 12 胜 2 负 > 5 局全胜 | 4.0 |
| 25 胜 5 负 > 10 局全胜 | 10.0 |
| 30 胜 6 负 > 15 局全胜 | 20.0 |

所需 k 随双方场数**线性增长**，所以想靠调大 k 实现"打得多一定排前面"是追不上的；而 k 越大，场多的人之间的区分度越平——同样拿 20 场，75% 与 60% 的分数差距在 k=2 时是 0.136、k=6 时 0.115、k=12 时只剩 0.094。k=6 是"修掉明显荒谬、又不磨平老手"的位置。

**试过并否掉的两个方案**：

- **Wilson 置信下界**（App Store／Reddit 评分排序那个）：1 局全胜只有 0.21，0 胜 1 局直接 0 分，新人一进榜就被埋到底部，跟"拉人参与"的目标正好相反；而且它要 z ≥ 1.455 才能翻转 3 连胜与 6-1，z 是个说不清的隐藏参数。
- **胜率 × 场次系数**：分数被一路往 0 压，刷场次就能盖过胜率——12 局 5 成（0.375）会排在 7 局 57%（0.364）前面；0 胜的新人一律 0 分垫底。

**已知的边界（改公式解决不了，别再折腾了）**：加法先验无法把 2 胜 1 负 排到 1 胜 0 负 前面，比较会退化成 `3 < 2`，与 k 取多少无关。真要让"只打一场的挂冠军奖杯"这种事不发生，靠的是**场次门槛**和**展示层**，不是胜率公式。下面三条都还没实现：

- 场数低于阈值的行打一个"样本不足"角标，明示这个人只有 1 场；
- 赛季末发冠军／奖杯前套一条**相对**出勤线 `n ≥ ⌈0.5 × 最活跃者场数⌉`（别写死绝对场数：按"打满 10 场"今天只有 4 人达标；提到 60% 又会把 7 场 6 胜的当前榜首排除掉，冠军落到 55%～56% 胜率的人身上，逐档实测见 `docs/排名算法.md` §7）；
- 另开一条**出勤榜**（场次／出勤率），让样本不足的人有正当的上榜路径。

**"多打一场会不会掉名次"**：在这份真实数据上按 50% 胜率逐人模拟过，34 人里期望涨 16 人、跌 15 人、基本不动 3 人——**新算法没有系统性惩罚出勤**。但小样本的人名次本来就飘：只打 1 场的人再打一场，期望要挪 4.4 个名次（1 局全胜的现榜第 5，赢→第 3、输→第 20），这属于样本量问题，只能靠"样本不足"角标讲清楚，不要指望公式抹平。改先验的直接收益是榜首变稳：现榜前三名再打一场的期望名次抖动从 1.5 名降到 0.5 名。

**出勤不能塞进主榜分数**：试过 `修正胜率 + λ × (场数 / 最活跃者场数)`，λ=0.02、0.04 时前六名不变，**λ=0.06 起前六名里的最低胜率从 62% 掉到 55%**——低于五成多的人被顶上来，1 胜 0 负 的反被挤出去。所以出勤要走**独立的副榜**（只比 场次／出勤率，这类指标只增不减，没有回归均值的坑），冠军归属则用"出勤资格线达标者里的主榜第一"，而不是把两个指标加成一个分。

**样本量的事实**：置换检验（把每场的 5 个胜场随机重分配给该场 10 人，重复 3000 次）显示：胜率离散度统计量 χ²/df 观测值 0.837，纯随机零分布的中位数 1.015、95% 区间 [0.691, 1.401]，置换 p = 0.858 —— **现在的排名波动基本就是运气**，还不能拒绝"选手之间没有技术差异"。要在随机混排的 5v5 里稳定识别出一个真实胜率 60% 的人，约需 149 场/人（55% 则要 613 场）。所以排名公式只负责"不荒谬"，"多打有正反馈"得靠出勤门槛和出勤榜这类机制。跑 `.venv/bin/python scripts/rank_stats.py` 可以随时看当前值。

**完整推导**（记号与前提、临界 k\* 的闭式解与证明、三条不可能性结论、被否方案的反例、功效分析）见 **[`docs/排名算法.md`](docs/排名算法.md)**。上面这些结论的证明过程、以及每个数字是在哪份快照上算的，都在那份文档里。

**要调强度**：改 `site/app.js` 里的 `RANKING_PRIOR_WINS` / `RANKING_PRIOR_LOSSES` 两个常量即可（两者相加就是 k）。`tests/test_ranking.py` 会直接跑线上那份比较函数，并检查 `site/index.html` 上写给群友看的公式说明有没有跟着改，改了常量忘了改文案会直接测试失败。

## 组队胜率与队友详情

只有一张玩家表，列是：排名、玩家（头像 + 昵称）、Account ID、胜、负、总场数、个人胜率、组队胜率。前三名是金 / 银 / 铜奖杯，写着冠军 / 亚军 / 季军，屏幕阅读器仍然读得出名次。

**组队胜率的定义**：把这名玩家每一段队友关系的胜率各算一个样本再取平均，不按共同场次加权。

**为什么不加权**：一场五人队正好给这个人贡献 4 段关系，加权平均的结果精确等于个人胜率，那就等于没有新信息。只有同一段队友关系有输有赢时，组队胜率才会和个人胜率拉开差距。场次很少的时候（比如群里才打了几场）这个值大多就是 0% 或 100%，属于正常现象。

**点 Account ID** 会弹出一个对话框，列出这名玩家在当前时间范围内和每个队友的：共同场次、胜、负、胜率、最近一次同队日期；没有同队记录的人会给出提示。`Esc` 或“关闭”退出。

弹窗里的**共同场次、胜、负、胜率**四列表头可以点击排序，像 Excel 一样：第一次点是从大到小（▼），再点一次翻成从小到大（▲），没选中的列显示 ⇅；换列排序时默认从大到小。默认按共同场次从大到小。

手机端（宽度 ≤ 640px）会自动隐藏 Account ID 列和总场数列：Account ID 变成玩家名字下面那个可点的按钮，弹窗里也会隐藏 Account ID 和最近同队两列，表格不需要横向滚动。

## 前提与坑

- **不需要任何账号登录。** 看板是纯静态网页，浏览器只读仓库里的 `site/data/league.json`，不会直连 OpenDota，也不需要 Steam 账号或密码。数据由 `collector.py` 在本地或 GitHub Actions 里抓取，抓取结果作为文件提交进仓库。`league`、`manual`、`both` 模式匿名即可；`OPENDOTA_API_KEY` 和 `tournament` 模式用的 `STEAM_WEB_API_KEY` 都只是 API 令牌，不是登录，而且只存在 `.env` 或 GitHub Secrets 里。
- **必须开“公开比赛数据”**：客户端 Settings → Game → Expose Public Match Data。没开的话比赛会被匿名化，看板对不上人（数据里的 `anonymous_player_slots` 会告诉你有多少个位置是匿名）。
- **回放约 10 天过期**：过期后只剩基础数据，眼位、经济曲线等细节拿不到。本看板只用基础字段，所以不受影响，但想留细节要尽早解析。
- **普通房间不会自动进联赛**：所以要么申请联赛，要么用 `manual` 模式手动登记。
- **`manual` 模式不会自动收联赛比赛**：以后改走联赛开房的话，记得把 `collect_mode` 改成 `both` 或 `league`，否则联赛里的比赛会被忽略。
- **solo 房不算内战**：1v1 mid（`game_mode 21`）即使用联赛房间开也会被跳过，看板只统计 5v5；哪天要统计别的娱乐模式，改 `exclude_game_modes` 就行，被跳过的场次和原因都能在 `metadata.excluded_matches` 里核对。
- **匿名接口有限流**：脚本遇到限流会等到下一分钟再试，遇到日额度耗尽才切到带 Key 的请求。别在本地写高频轮询。
- **数据是公开的**：看板会把 Steam 昵称和 `account_id` 公开挂在网页上，群内部用没问题，公开传播前想清楚。

## 目录结构

```
config.json              唯一需要改的配置
names.json               群昵称映射（account_id -> 昵称）
matches.txt              manual 模式的比赛清单
overrides.json           手工补录的比赛（公开接口查不到的场次，见「手工补录」）
collector.py             抓 OpenDota 数据，写 site/data/league.json、avatars.json、heroes.json
site/                    纯静态看板（index.html / app.js / styles.css / data/league.json / data/avatars.json / data/heroes.json）
tests/test_collector.py  采集逻辑测试
tests/test_ranking.py    排名算法测试（把 app.js 里的比较函数交给 node 跑）
scripts/rank_stats.py    排名算法的数据体检：置换检验、临界 k*、出勤资格线逐档等，一条命令复现
docs/排名算法.md         排名算法的完整推导（记号、命题与证明、被否方案、功效分析）
.github/workflows/pages.yml  每 15 分钟刷新 + 部署 GitHub Pages
```

跑测试：

```bash
.venv/bin/python -m pytest -q
```

## 后续可做的事

- 主榜旁边加一条**铁人榜**（只比出场次数／出勤率），再给赛季冠军加一条**相对出勤资格线**：现在只打 1 场的人能排到第 5，靠改公式解决不了，得靠这两样（依据见「排名算法」）；
- 战报存档再多存几个字段（GPM、正补/反补、经济差、伤害与承伤），展开详情里就能直接排一行数据；
- 把 `matches.txt` 的维护交给群机器人：战报 bot 播报战绩时顺手发 `match_id`，你复制粘贴进文件即可；
- 想做赛季排行榜/MMR，可以按赛季把 `league_id` 分段，或用多个仓库各做一个赛季的看板。

