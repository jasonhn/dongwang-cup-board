# 内战光荣榜（Dota 2 内战 / 联赛公开看板）

一个纯静态的 Dota 2 内战战绩看板：按时间范围统计每个玩家的胜、负、总场数、个人胜率与组队胜率，点 Account ID 还能展开这名玩家和每个队友的同队场次与胜率。数据每天从 OpenDota 自动刷新，部署在 GitHub Pages 上，群里直接发链接就能看。

派生自 <https://github.com/BCSZSZ/ustc-dota2-hall-of-fame>，在其基础上做了三件事：

1. 联赛 ID、看板标题、时区全部收进 `config.json`，换群不用改代码；
2. 新增 `manual` 模式：**没有官方联赛也能做看板**，把每场的 `match_id` 记进 `matches.txt` 即可；
3. 新增 `names.json`：用群昵称覆盖 Steam 昵称（Steam 名太长、还会被改名）；
4. 排名表显示玩家 **Steam 头像**，头像缓存在 `site/data/avatars.json`，抓不到就退化成首字母徽章；
5. 前三名显示 **冠军 / 亚军 / 季军奖杯**（金、银、铜），不再是数字 1、2、3；
6. 组队胜率并进同一张玩家表，点 Account ID 弹出队友详情；原来那个几百行几百列的矩阵删掉了，手机上不用左右拖。

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
| `collect_mode` | `league`：只按联赛 ID 归集；`manual`：只按 `matches.txt` 手动登记的 `match_id`；`both`：两者合并统计（推荐） |
| `league_id` | `league` 或 `both` 模式下必填。申请 Amateur League 后拿到，也可以用 `--league-name` 反查 |
| `manual_match_file` | `manual` 模式的比赛清单，每行一个 `match_id`，`#` 开头是注释 |
| `refresh_recent` | 每次刷新重新抓最近 N 场（补全还没解析完的比赛），其余用仓库里已有的缓存，省 API 额度 |
| `time_zone` | 看板显示与输入框用的时区，例如 `Asia/Shanghai` |
| `names_file` | 群昵称映射文件 |
| `avatars_file` | Steam 头像缓存，键是 `account_id`，值是头像 URL；空字符串代表“这个人没有头像，别再抓” |
| `max_new_avatars` | 每次运行最多新查多少个玩家的头像（老玩家复用缓存，省 API 额度） |
| `site` | 看板标题、副标题、页脚等文案，前端运行时读取，不用改 HTML |

### 3. 本地跑一次，确认能出数据

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
cp .env.example .env          # 可选：填 OPENDOTA_API_KEY
.venv/bin/python collector.py
```

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
```

只想知道 ID、不写数据的话，看输出里的 `Resolved '懂王杯' to league id 19905`，再把它填进 `config.json`；名字不完全一致时会列出候选，换成完全一致的名字即可。

### 4. Push，开 GitHub Pages

本仓库用 GitHub Actions 部署（`.github/workflows/pages.yml`），所以 Pages 的来源必须切成 **GitHub Actions**：

仓库 **Settings → Pages → Build and deployment → Source = GitHub Actions**。新建仓库默认是 “Deploy from a branch”，不改这里的话 Actions 会在 deploy 步骤报错 “Pages not enabled”。

然后：

- push 到 `main` 即触发一次刷新 + 部署；
- 每天 UTC 01:00（北京时间 09:00）自动刷新。GitHub cron 用 UTC，改时间记得换算；
- 也可以随时在 Actions 页面手动点 “Run workflow”。

部署完成后，那次运行的 deploy 步骤会打印地址，格式是 `https://<用户名>.github.io/<仓库名>/`。

可选：在仓库 Settings → Secrets and variables → Actions 里新建 `OPENDOTA_API_KEY`。脚本默认走匿名免费接口（约 60 次/分钟、3000 次/天），额度用尽才会改用带 Key 的请求，并且有 `OPENDOTA_MAX_PAID_CALLS` 上限保护。Key 只存在 Secret 或本地 `.env`，不会进仓库。

## 三种收数据的方式

### `league` 模式（最省心）

用联赛模式开的房间，比赛会自带 `leagueid`，OpenDota 按联赛自动归集，你什么都不用记。要拿到自己的 `league_id`，去 Valve 的联赛申请入口 <https://www.dota2.com/league/0/list> 申请 Amateur League，审批与费用以页面为准。小群不一定要折腾，先用现成联赛把看板跑顺也行。

联赛刚建好、还没打比赛时 `matchIds` 是空的，看板会显示“还没有战报入册”并给出下一步提示，不会报错。

### `manual` 模式（零门槛，最适合没有联赛的群）

1. `config.json` 里把 `collect_mode` 改成 `"manual"`；
2. 每打完一场，把 `match_id` 追加到 `matches.txt`（OpenDota 页面地址里就有，或在客户端比赛历史里复制）；
3. push，看板自动更新。

`manual` 模式不校验比赛属于哪个联赛，普通房间、开黑房间都能收，比赛按开始时间排序。

### `both` 模式（两边都要）

联赛自动归集加上 `matches.txt` 里手动登记的场次，一起统计；同一个 `match_id` 不会重复抓。适合“大部分比赛走联赛开房，偶尔有普通房间要补录”的群。

## 为什么联赛里打过的比赛没被收录

先查 OpenDota 有没有这场：`GET /api/leagues/<league_id>/matchIds`。如果这个列表是空的，说明这场比赛**没有带 `leagueid`**，十有八九是从自定义房间或普通房开的，不是从客户端「联赛」入口开的房——联赛名存在，不代表比赛会挂到它下面。

开黑排天梯匹配（`game_mode 22`）永远不会带联赛标记，这类比赛只能靠 `matches.txt` 手动登记。

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
- GitHub Actions 会把 `site/data/league.json` 和 `site/data/avatars.json` 一起提交，所以缓存会一直累积；本地跑一次再 push 也能把新头像带上去。

## 组队胜率与队友详情

只有一张玩家表，列是：排名、玩家（头像 + 昵称）、Account ID、胜、负、总场数、个人胜率、组队胜率。前三名是金 / 银 / 铜奖杯，写着冠军 / 亚军 / 季军，屏幕阅读器仍然读得出名次。

**组队胜率的定义**：把这名玩家每一段队友关系的胜率各算一个样本再取平均，不按共同场次加权。

**为什么不加权**：一场五人队正好给这个人贡献 4 段关系，加权平均的结果精确等于个人胜率，那就等于没有新信息。只有同一段队友关系有输有赢时，组队胜率才会和个人胜率拉开差距。场次很少的时候（比如群里才打了几场）这个值大多就是 0% 或 100%，属于正常现象。

**点 Account ID** 会弹出一个对话框，列出这名玩家在当前时间范围内和每个队友的：共同场次、胜、负、胜率、最近一次同队日期；没有同队记录的人会给出提示。`Esc` 或“关闭”退出。

弹窗里的**共同场次、胜、负、胜率**四列表头可以点击排序，像 Excel 一样：第一次点是从大到小（▼），再点一次翻成从小到大（▲），没选中的列显示 ⇅；换列排序时默认从大到小。默认按共同场次从大到小。

手机端（宽度 ≤ 640px）会自动隐藏 Account ID 列和总场数列：Account ID 变成玩家名字下面那个可点的按钮，弹窗里也会隐藏 Account ID 和最近同队两列，表格不需要横向滚动。

## 前提与坑

- **不需要任何账号登录。** 看板是纯静态网页，浏览器只读仓库里的 `site/data/league.json`，不会直连 OpenDota，也不需要 Steam 账号、Steam 密码或 Steam Web API Key。数据由 `collector.py` 在本地或 GitHub Actions 里抓取（匿名即可），抓取结果作为文件提交进仓库。可选的 `OPENDOTA_API_KEY` 只是一个 API 令牌，同样不是登录。
- **必须开“公开比赛数据”**：客户端 Settings → Game → Expose Public Match Data。没开的话比赛会被匿名化，看板对不上人（数据里的 `anonymous_player_slots` 会告诉你有多少个位置是匿名）。
- **回放约 10 天过期**：过期后只剩基础数据，眼位、经济曲线等细节拿不到。本看板只用基础字段，所以不受影响，但想留细节要尽早解析。
- **普通房间不会自动进联赛**：所以要么申请联赛，要么用 `manual` 模式手动登记。
- **`manual` 模式不会自动收联赛比赛**：以后改走联赛开房的话，记得把 `collect_mode` 改成 `both` 或 `league`，否则联赛里的比赛会被忽略。
- **匿名接口有限流**：脚本遇到限流会等到下一分钟再试，遇到日额度耗尽才切到带 Key 的请求。别在本地写高频轮询。
- **数据是公开的**：看板会把 Steam 昵称和 `account_id` 公开挂在网页上，群内部用没问题，公开传播前想清楚。

## 目录结构

```
config.json              唯一需要改的配置
names.json               群昵称映射（account_id -> 昵称）
matches.txt              manual 模式的比赛清单
collector.py             抓 OpenDota 数据，写 site/data/league.json 与 site/data/avatars.json
site/                    纯静态看板（index.html / app.js / styles.css / data/league.json / data/avatars.json）
tests/test_collector.py  采集逻辑测试
.github/workflows/pages.yml  每天刷新 + 部署 GitHub Pages
```

跑测试：

```bash
.venv/bin/python -m pytest -q
```

## 后续可做的事

- 在 `collector.py` 里多存几个字段（如 GPM、击杀/死亡/助攻、英雄），再在 `site/app.js` 加一张“单场详情”表；
- 把 `matches.txt` 的维护交给群机器人：战报 bot 播报战绩时顺手发 `match_id`，你复制粘贴进文件即可；
- 想做赛季排行榜/MMR，可以按赛季把 `league_id` 分段，或用多个仓库各做一个赛季的看板。

