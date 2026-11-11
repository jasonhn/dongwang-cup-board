const DEFAULT_TIME_ZONE = "Asia/Shanghai";
const DAY_SECONDS = 86_400;

const elements = {
  heroStatus: document.querySelector("#heroStatus"),
  updateStamp: document.querySelector("#updateStamp"),
  fromDate: document.querySelector("#fromDate"),
  toDate: document.querySelector("#toDate"),
  fromRange: document.querySelector("#fromRange"),
  toRange: document.querySelector("#toRange"),
  timelineSelection: document.querySelector("#timelineSelection"),
  minimumTime: document.querySelector("#minimumTime"),
  maximumTime: document.querySelector("#maximumTime"),
  selectedTime: document.querySelector("#selectedTime"),
  matchCount: document.querySelector("#matchCount"),
  playerCount: document.querySelector("#playerCount"),
  rangeDays: document.querySelector("#rangeDays"),
  availableCount: document.querySelector("#availableCount"),
  coverageNotice: document.querySelector("#coverageNotice"),
  statsBody: document.querySelector("#statsBody"),
  statsEmpty: document.querySelector("#statsEmpty"),
  matrixTable: document.querySelector("#matrixTable"),
  matrixEmpty: document.querySelector("#matrixEmpty"),
  matrixScroll: document.querySelector("#matrixScroll"),
  heroKicker: document.querySelector("#heroKicker"),
  heroTitle: document.querySelector("#heroTitle"),
  heroSubtitle: document.querySelector("#heroSubtitle"),
  footerLine: document.querySelector("#footerLine"),
  sourceText: document.querySelector("#sourceText"),
  fromTzLabel: document.querySelector("#fromTzLabel"),
  toTzLabel: document.querySelector("#toTzLabel"),
  quickButtons: [...document.querySelectorAll(".quick-ranges button")],
};

const state = {
  data: null,
  avatars: {},
  minimum: 0,
  maximum: 0,
  from: 0,
  to: 0,
};

let timeZone = DEFAULT_TIME_ZONE;
let wallClockFormatter = null;

function setTimeZone(next) {
  timeZone = next || DEFAULT_TIME_ZONE;
  wallClockFormatter = null;
}

function partsOf(seconds) {
  if (!wallClockFormatter) {
    wallClockFormatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
  }
  const parts = wallClockFormatter.formatToParts(new Date(seconds * 1000));
  return Object.fromEntries(parts.map((part) => [part.type, part.value]));
}

// Offset between the wall clock in `timeZone` and UTC, in minutes (fractional for
// zones such as Asia/Kolkata). Seconds are included so the value is exact.
function offsetMinutesOf(seconds) {
  const p = partsOf(seconds);
  const wall = Date.UTC(
    Number(p.year),
    Number(p.month) - 1,
    Number(p.day),
    Number(p.hour) % 24,
    Number(p.minute),
    Number(p.second),
  );
  return Math.round((wall - seconds * 1000) / 60_000);
}

function formatLocal(seconds) {
  const p = partsOf(seconds);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}

function formatShortLocal(seconds) {
  const p = partsOf(seconds);
  return `${p.year}-${p.month}-${p.day}`;
}

function toLocalInput(seconds) {
  const p = partsOf(seconds);
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

function parseLocalInput(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {
    return Number.NaN;
  }
  const wallSeconds = Date.parse(`${value}:00Z`) / 1000;
  if (!Number.isFinite(wallSeconds)) return Number.NaN;
  // The wall clock is not an instant: real = wall - offset(real). Two passes keep
  // the answer right even when the corrected instant crosses a DST boundary.
  let seconds = wallSeconds;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    seconds = wallSeconds - offsetMinutesOf(seconds) * 60;
  }
  return seconds;
}

function timeZoneLabel(seconds = Date.now() / 1000) {
  const offset = Math.round(offsetMinutesOf(seconds));
  const sign = offset < 0 ? "-" : "+";
  const absolute = Math.abs(offset);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  const minutes = absolute % 60;
  return minutes ? `UTC${sign}${hours}:${String(minutes).padStart(2, "0")}` : `UTC${sign}${hours}`;
}

function applyBranding(data) {
  const site = data.site || {};
  const title = site.title || "内战光荣榜";
  const subtitle = site.subtitle || "Dota 2 内战战绩";
  document.title = `${title} · ${subtitle}`;

  const description = document.querySelector('meta[name="description"]');
  if (description && site.description) description.setAttribute("content", site.description);
  if (elements.heroKicker) elements.heroKicker.textContent = site.kicker || "内战有喜 · 荣耀登榜";
  if (elements.heroTitle) {
    elements.heroTitle.textContent = title;
    elements.heroTitle.dataset.text = title;
    elements.heroTitle.setAttribute("aria-label", title);
  }
  if (elements.heroSubtitle) elements.heroSubtitle.textContent = subtitle;
  if (elements.footerLine) {
    elements.footerLine.textContent = site.footer || `${title} · 胜负皆尽兴，开黑常有喜`;
  }

  const label = `（${timeZoneLabel()}）`;
  if (elements.fromTzLabel) elements.fromTzLabel.textContent = label;
  if (elements.toTzLabel) elements.toTzLabel.textContent = label;
}

function formatPercent(value) {
  return `${(value * 100).toFixed(1)}%`;
}

function comparePlayerRanking(left, right) {
  // Compare (wins + 1) / (total + 2) exactly, without floating-point rounding.
  const adjustedRateOrder =
    (right.wins + 1) * (left.total + 2) - (left.wins + 1) * (right.total + 2);
  return (
    adjustedRateOrder ||
    right.wins - left.wins ||
    right.total - left.total ||
    left.name.localeCompare(right.name, "zh-CN") ||
    left.accountId - right.accountId
  );
}

function makeCell(tag, text, className = "") {
  const cell = document.createElement(tag);
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function makePlayerLabel(player) {
  const wrapper = document.createDocumentFragment();
  const name = document.createTextNode(player.name);
  const account = document.createElement("small");
  account.textContent = String(player.accountId);
  wrapper.append(name, account);
  return wrapper;
}

// Avatars are collected by collector.py into site/data/avatars.json.
// Only http(s) URLs are trusted; anything else falls back to a letter badge.
function isAvatarUrl(value) {
  return typeof value === "string" && /^https?:\/\//i.test(value);
}

async function loadAvatars() {
  try {
    const response = await fetch("./data/avatars.json", { cache: "no-store" });
    if (!response.ok) return {};
    const raw = await response.json();
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(
      Object.entries(raw).filter(([, url]) => isAvatarUrl(url)),
    );
  } catch {
    // Avatars are decorative: a missing file must never break the board.
    return {};
  }
}

function makeAvatarFallback(player) {
  const mark = document.createElement("span");
  mark.className = "player-avatar avatar-missing";
  mark.setAttribute("aria-hidden", "true");
  const first = Array.from(String(player.name || ""))[0];
  mark.textContent = first ? first.toUpperCase() : "?";
  return mark;
}

function makeAvatar(player) {
  const url = state.avatars[String(player.accountId)];
  if (!url) return makeAvatarFallback(player);
  const image = document.createElement("img");
  image.className = "player-avatar";
  image.src = url;
  image.alt = "";
  image.loading = "lazy";
  image.decoding = "async";
  image.addEventListener(
    "error",
    () => image.replaceWith(makeAvatarFallback(player)),
    { once: true },
  );
  return image;
}

function setQuickButtonActive(target) {
  elements.quickButtons.forEach((button) => button.classList.toggle("active", button === target));
}

function updateRangeControls() {
  elements.fromRange.value = String(state.from);
  elements.toRange.value = String(state.to);
  elements.fromDate.value = toLocalInput(state.from);
  elements.toDate.value = toLocalInput(state.to);
  elements.selectedTime.textContent = `${formatLocal(state.from)} — ${formatLocal(state.to)}`;

  const span = Math.max(1, state.maximum - state.minimum);
  const left = ((state.from - state.minimum) / span) * 100;
  const right = ((state.to - state.minimum) / span) * 100;
  elements.timelineSelection.style.left = `${Math.max(0, left)}%`;
  elements.timelineSelection.style.width = `${Math.max(0, right - left)}%`;
}

function setRange(from, to, quickButton = null) {
  state.from = Math.max(state.minimum, Math.min(from, state.maximum));
  state.to = Math.max(state.minimum, Math.min(to, state.maximum));
  if (state.from > state.to) [state.from, state.to] = [state.to, state.from];
  updateRangeControls();
  setQuickButtonActive(quickButton);
  render();
}

function getVisiblePlayers(match) {
  return (match.players || []).filter(
    (player) => Number.isFinite(player.account_id) && Number.isFinite(player.player_slot),
  );
}

function calculate(matches) {
  const stats = new Map();
  const pairs = new Map();

  for (const match of matches) {
    const players = getVisiblePlayers(match).map((player) => ({
      accountId: Number(player.account_id),
      name: player.display_name || player.personaname || `Account ${player.account_id}`,
      display_name: player.display_name || null,
      radiant: Number(player.player_slot) < 128,
    }));

    for (const player of players) {
      const won = player.radiant === match.radiant_win;
      const current = stats.get(player.accountId) || {
        accountId: player.accountId,
        name: player.name,
        wins: 0,
        losses: 0,
        total: 0,
        latestStart: 0,
      };
      current.wins += won ? 1 : 0;
      current.losses += won ? 0 : 1;
      current.total += 1;
      if (match.start_time >= current.latestStart) {
        // Group nicknames are stable; Steam nicknames can change, so only follow
        // them when the player has no configured display name.
        if (!player.display_name) current.name = player.name;
        current.latestStart = match.start_time;
      }
      stats.set(player.accountId, current);
    }

    for (const radiant of [true, false]) {
      const team = players.filter((player) => player.radiant === radiant);
      const teamWon = radiant === match.radiant_win;
      for (let left = 0; left < team.length; left += 1) {
        for (let right = left + 1; right < team.length; right += 1) {
          const ids = [team[left].accountId, team[right].accountId].sort((a, b) => a - b);
          const key = `${ids[0]}:${ids[1]}`;
          const current = pairs.get(key) || { games: 0, wins: 0 };
          current.games += 1;
          current.wins += teamWon ? 1 : 0;
          pairs.set(key, current);
        }
      }
    }
  }

  const players = [...stats.values()].sort(comparePlayerRanking);
  return { players, pairs };
}

function renderStats(players) {
  elements.statsBody.replaceChildren();
  elements.statsEmpty.hidden = players.length > 0;

  players.forEach((player, index) => {
    const row = document.createElement("tr");
    const rankCell = document.createElement("td");
    const rank = document.createElement("span");
    rank.className = "rank-badge";
    rank.textContent = String(index + 1);
    rankCell.append(rank);
    row.append(rankCell);
    const nameCell = document.createElement("td");
    nameCell.className = "player-name";
    nameCell.append(makeAvatar(player), makeCell("span", player.name, "player-label"));
    row.append(nameCell);
    row.append(makeCell("td", String(player.accountId), "account-id"));
    row.append(makeCell("td", String(player.wins)));
    row.append(makeCell("td", String(player.losses)));
    row.append(makeCell("td", String(player.total)));
    const rateCell = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "rate-pill";
    pill.textContent = formatPercent(player.wins / player.total);
    rateCell.append(pill);
    row.append(rateCell);
    elements.statsBody.append(row);
  });
}

function pairClass(rate) {
  if (rate < 0.4) return "pair-loss";
  if (rate < 0.6) return "pair-even";
  return "pair-win";
}

function renderMatrix(players, pairs) {
  elements.matrixTable.replaceChildren();
  const hasPlayers = players.length > 0;
  elements.matrixScroll.hidden = !hasPlayers;
  elements.matrixEmpty.hidden = hasPlayers;
  if (!hasPlayers) return;

  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.append(makeCell("th", "玩家"));
  for (const player of players) {
    const th = document.createElement("th");
    th.append(makePlayerLabel(player));
    headRow.append(th);
  }
  head.append(headRow);

  const body = document.createElement("tbody");
  for (const rowPlayer of players) {
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.append(makePlayerLabel(rowPlayer));
    row.append(label);
    for (const columnPlayer of players) {
      if (rowPlayer.accountId === columnPlayer.accountId) {
        row.append(makeCell("td", "—", "pair-self"));
        continue;
      }
      const ids = [rowPlayer.accountId, columnPlayer.accountId].sort((a, b) => a - b);
      const pair = pairs.get(`${ids[0]}:${ids[1]}`);
      if (!pair) {
        row.append(makeCell("td", "", "pair-none"));
        continue;
      }
      const rate = pair.wins / pair.games;
      row.append(makeCell("td", `${pair.games}/${formatPercent(rate)}`, pairClass(rate)));
    }
    body.append(row);
  }

  elements.matrixTable.append(head, body);
}

function render() {
  if (!state.data) return;
  const matches = state.data.matches.filter(
    (match) => match.start_time >= state.from && match.start_time <= state.to,
  );
  const { players, pairs } = calculate(matches);
  elements.matchCount.textContent = String(matches.length);
  elements.playerCount.textContent = String(players.length);
  elements.rangeDays.textContent = String(Math.max(1, Math.ceil((state.to - state.from) / DAY_SECONDS)));
  renderStats(players);
  renderMatrix(players, pairs);
}

function showCoverageNotice(data) {
  const metadata = data.metadata || {};
  const unavailable = metadata.unavailable_matches || [];
  const anonymous = Number(metadata.anonymous_player_slots || 0);
  const incomplete = metadata.incomplete_roster_match_ids || [];
  const messages = [];
  if (unavailable.length) messages.push(`${unavailable.length} 场尚未取得公开详情`);
  if (anonymous) messages.push(`${anonymous} 个选手位为匿名`);
  if (incomplete.length) messages.push(`${incomplete.length} 场公开阵容不完整`);
  if (!messages.length) {
    elements.coverageNotice.hidden = true;
    return;
  }
  elements.coverageNotice.textContent = `覆盖提示：${messages.join("；")}。这些位置不会伪装成已知玩家。`;
  elements.coverageNotice.hidden = false;
}

function bindControls() {
  elements.fromRange.addEventListener("input", () => {
    const next = Math.min(Number(elements.fromRange.value), state.to);
    setRange(next, state.to);
  });
  elements.toRange.addEventListener("input", () => {
    const next = Math.max(Number(elements.toRange.value), state.from);
    setRange(state.from, next);
  });
  elements.fromDate.addEventListener("change", () => {
    const next = parseLocalInput(elements.fromDate.value);
    if (Number.isFinite(next)) setRange(next, state.to);
  });
  elements.toDate.addEventListener("change", () => {
    const next = parseLocalInput(elements.toDate.value);
    if (Number.isFinite(next)) setRange(state.from, next);
  });
  elements.quickButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.range === "all") {
        setRange(state.minimum, state.maximum, button);
        return;
      }
      const days = Number(button.dataset.days || 0);
      setRange(Math.max(state.minimum, state.maximum - days * DAY_SECONDS), state.maximum, button);
    });
  });
}

function collectionLabel(data) {
  if (data.collection_mode === "manual") return "手动收录";
  if (data.collection_mode === "both") return `联赛 ${data.league_id} + 手动登记`;
  return `联赛 ${data.league_id}`;
}

function showEmptySnapshot(data) {
  const scope =
    data.collection_mode === "manual"
      ? "手动登记的清单"
      : `联赛 ${data.league_id}${data.league?.name ? `（${data.league.name}）` : ""}` +
        (data.collection_mode === "both" ? " 加上手动登记的清单" : "");
  elements.heroStatus.textContent = "还没有战报入册";
  const parsed = Date.parse(data.generated_at_utc || "");
  const generated = Number.isFinite(parsed) ? parsed / 1000 : Date.now() / 1000;
  elements.updateStamp.textContent = `数据更新时间：${formatLocal(generated)} ${timeZoneLabel(generated)}`;
  elements.coverageNotice.textContent =
    `${scope} 目前还没有可用比赛。用联赛模式开一个房间打一场，几分钟后 OpenDota 就会收录这场比赛；` +
    "已经打过的普通房间可以把 match_id 写进 matches.txt，并把 config.json 的 collect_mode 改成 manual。";
  elements.coverageNotice.hidden = false;
  elements.statsEmpty.hidden = false;
  elements.matrixEmpty.hidden = false;
  elements.matrixScroll.hidden = true;
}

async function load() {
  try {
    const [response, avatars] = await Promise.all([
      fetch("./data/league.json", { cache: "no-store" }),
      loadAvatars(),
    ]);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    setTimeZone(data.time_zone);
    state.data = data;
    state.avatars = avatars;
    applyBranding(data);
    if (!Array.isArray(data.matches) || data.matches.length === 0) {
      showEmptySnapshot(data);
      return;
    }
    const lastMatchEnd = Math.max(
      ...data.matches.map((match) => Number(match.start_time) + Number(match.duration || 0)),
    );
    // Follow the real clock, not just the newest match, so freshly added matches
    // land inside the range without anyone dragging the slider.
    state.minimum = Math.min(
      Math.min(...data.matches.map((match) => Number(match.start_time))),
      Math.floor(Date.now() / 1000),
    );
    state.maximum = Math.max(lastMatchEnd, Math.floor(Date.now() / 1000));
    state.from = state.minimum;
    state.to = state.maximum;

    for (const range of [elements.fromRange, elements.toRange]) {
      range.min = String(state.minimum);
      range.max = String(state.maximum);
      range.step = "60";
    }
    for (const input of [elements.fromDate, elements.toDate]) {
      input.min = toLocalInput(state.minimum);
      input.max = toLocalInput(state.maximum);
    }

    const scope = collectionLabel(data);
    elements.heroStatus.textContent = `${data.matches.length} 场战报已入册 · ${scope}`;
    const generated = Date.parse(data.generated_at_utc) / 1000;
    elements.updateStamp.textContent = `数据更新时间：${formatLocal(generated)} ${timeZoneLabel(generated)}`;
    elements.minimumTime.textContent = formatShortLocal(state.minimum);
    elements.maximumTime.textContent = formatShortLocal(state.maximum);
    if (elements.sourceText) {
      elements.sourceText.textContent =
        data.collection_mode === "manual"
          ? `每天自动刷新手动登记的 ${data.metadata?.match_ids_returned ?? data.matches.length} 场比赛。只统计能公开识别 Account ID 且有胜负结果的选手位；比赛隐私设置可能造成匿名或缺失。`
          : `每天自动刷新${data.collection_mode === "both" ? `联赛 ${data.league_id} 和手动登记的场次` : `联赛 ${data.league_id}`}。只统计能公开识别 Account ID 且有胜负结果的选手位；比赛隐私设置可能造成匿名或缺失。`;
    }
    elements.availableCount.textContent = String(data.metadata?.matches_available ?? data.matches.length);
    showCoverageNotice(data);
    bindControls();
    const allButton = elements.quickButtons.find((button) => button.dataset.range === "all");
    setRange(state.minimum, state.maximum, allButton);
  } catch (error) {
    elements.heroStatus.textContent = "战报暂时未能展开";
    elements.updateStamp.textContent = "数据读取失败";
    elements.coverageNotice.textContent =
      globalThis.location?.protocol === "file:"
        ? "浏览器不允许网页从本地文件读取数据。请在项目根目录运行 python -m http.server -d site 8080，" +
          "然后打开 http://localhost:8080；或者直接部署到 GitHub Pages 用线上地址访问。"
        : `暂时无法读取联赛数据：${error.message}`;
    elements.coverageNotice.hidden = false;
    elements.statsEmpty.hidden = false;
    elements.matrixEmpty.hidden = false;
    elements.matrixScroll.hidden = true;
  }
}

load();
