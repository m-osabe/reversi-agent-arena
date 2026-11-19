// Thin client for Reversi Agent Arena.
// All game rules (legal moves, flips, auto-pass, winner) are decided exclusively by the server.

const COLS = "ABCDEFGH";
const SEAT_STORAGE_KEY = "reversi_arena_seats_v1";

const els = {
  gameSelect: document.getElementById("game-select"),
  refreshGamesBtn: document.getElementById("refresh-games-btn"),
  newGameTitle: document.getElementById("new-game-title"),
  createGameBtn: document.getElementById("create-game-btn"),
  gameTitle: document.getElementById("game-title"),
  gameIdPill: document.getElementById("game-id-pill"),
  statusBadge: document.getElementById("status-badge"),
  sseIndicator: document.getElementById("sse-indicator"),
  playerNameInput: document.getElementById("player-name-input"),
  seatStatusHint: document.getElementById("seat-status-hint"),
  cardB: document.getElementById("card-B"),
  cardW: document.getElementById("card-W"),
  nameB: document.getElementById("name-B"),
  nameW: document.getElementById("name-W"),
  scoreB: document.getElementById("score-B"),
  scoreW: document.getElementById("score-W"),
  actionsB: document.getElementById("actions-B"),
  actionsW: document.getElementById("actions-W"),
  turnBanner: document.getElementById("turn-banner"),
  board: document.getElementById("board"),
  moveCommentBar: document.getElementById("move-comment-bar"),
  moveCommentInput: document.getElementById("move-comment-input"),
  agentPromptBox: document.getElementById("agent-prompt-box"),
  agentCurlBox: document.getElementById("agent-curl-box"),
  copyPromptBtn: document.getElementById("copy-prompt-btn"),
  copyCurlBtn: document.getElementById("copy-curl-btn"),
  moveCountBadge: document.getElementById("move-count-badge"),
  downloadKifuBtn: document.getElementById("download-kifu-btn"),
  historyList: document.getElementById("history-list"),
  toast: document.getElementById("toast"),
};

let currentGameId = null;
let currentState = null;
let eventSource = null;
let toastTimer = null;
let isSubmittingMove = false;

// --- Local seat storage (sessionStorage) ---
function loadSeats() {
  try {
    return JSON.parse(sessionStorage.getItem(SEAT_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveSeat(gameId, seatInfo) {
  const seats = loadSeats();
  seats[gameId] = seatInfo;
  sessionStorage.setItem(SEAT_STORAGE_KEY, JSON.stringify(seats));
}

function clearSeat(gameId) {
  const seats = loadSeats();
  delete seats[gameId];
  sessionStorage.setItem(SEAT_STORAGE_KEY, JSON.stringify(seats));
}

function getMySeat(gameId) {
  if (!gameId) return null;
  return loadSeats()[gameId] || null;
}

// --- Toast ---
function showToast(message, isError = false) {
  els.toast.textContent = message;
  els.toast.classList.remove("hidden", "error");
  if (isError) els.toast.classList.add("error");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.classList.add("hidden");
  }, 3000);
}

// --- API helpers ---
async function apiFetch(path, options = {}) {
  const { headers: extraHeaders, ...restOptions } = options;
  const res = await fetch(path, {
    ...restOptions,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...(extraHeaders || {}),
    },
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const err = await res.json();
      if (err?.detail?.message) msg = err.detail.message;
      else if (typeof err?.detail === "string") msg = err.detail;
      else if (Array.isArray(err?.detail) && err.detail[0]?.msg) {
        msg = err.detail[0].msg;
      }
    } catch {
      // ignore
    }
    throw new Error(msg);
  }
  return res.json();
}

async function fetchGamesList(selectGameId = null) {
  const games = await apiFetch("/api/games");
  els.gameSelect.innerHTML = "";

  if (games.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "対局がありません";
    els.gameSelect.appendChild(opt);
    return games;
  }

  for (const g of games) {
    const opt = document.createElement("option");
    opt.value = g.game_id;
    const statusText = g.status === "finished" ? "終了" : `手番:${g.current_turn}`;
    opt.textContent = `${g.title} [${g.game_id}] (${g.counts.B}-${g.counts.W} / ${statusText})`;
    els.gameSelect.appendChild(opt);
  }

  const targetId = selectGameId || currentGameId || games[0].game_id;
  if (games.some((g) => g.game_id === targetId)) {
    els.gameSelect.value = targetId;
  }
  return games;
}

async function createGame() {
  const title = els.newGameTitle.value.trim() || "Reversi Match";
  try {
    const state = await apiFetch("/api/games", {
      method: "POST",
      body: JSON.stringify({ title }),
    });
    els.newGameTitle.value = "";
    await fetchGamesList(state.game_id);
    openGame(state.game_id);
    showToast(`新規対局を作成しました (${state.game_id})`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function joinSeat(color) {
  if (!currentGameId) return;
  const name = els.playerNameInput.value.trim() || "Human";
  try {
    const res = await apiFetch(`/api/games/${currentGameId}/join`, {
      method: "POST",
      body: JSON.stringify({ color, name }),
    });
    saveSeat(currentGameId, { color: res.color, token: res.token, name: res.name });
    renderState(res.state);
    showToast(`${res.color === "B" ? "黒" : "白"} (${res.name}) として参加しました`);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function submitMove(position) {
  if (!currentGameId || isSubmittingMove) return;
  const seat = getMySeat(currentGameId);
  if (!seat) return;

  isSubmittingMove = true;
  const comment = els.moveCommentInput.value.trim() || null;
  try {
    const state = await apiFetch(`/api/games/${currentGameId}/moves`, {
      method: "POST",
      headers: { "X-Player-Token": seat.token },
      body: JSON.stringify({ position, comment }),
    });
    els.moveCommentInput.value = "";
    renderState(state);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    isSubmittingMove = false;
  }
}

async function resignGame() {
  if (!currentGameId) return;
  const seat = getMySeat(currentGameId);
  if (!seat) return;
  if (!confirm("本当に投了しますか？")) return;

  try {
    const state = await apiFetch(`/api/games/${currentGameId}/resign`, {
      method: "POST",
      headers: { "X-Player-Token": seat.token },
      body: JSON.stringify({ comment: "Resigned" }),
    });
    renderState(state);
    showToast("投了しました");
  } catch (err) {
    showToast(err.message, true);
  }
}

function downloadKifuJson() {
  if (!currentState) return;
  const kifu = {
    game_id: currentState.game_id,
    title: currentState.title,
    created_at: currentState.created_at,
    status: currentState.status,
    winner: currentState.winner,
    end_reason: currentState.end_reason,
    players: {
      B: currentState.players.B.name || "Black",
      W: currentState.players.W.name || "White",
    },
    counts: currentState.counts,
    history: currentState.history,
  };
  const safeTitle = (currentState.title || "kifu")
    .replace(/[^a-zA-Z0-9_\-\u3040-\u30ff\u4e00-\u9faf]+/g, "_")
    .replace(/^_+|_+$/g, "");
  const filename = `${safeTitle || "reversi"}_${currentState.game_id}.json`;

  const blob = new Blob([JSON.stringify(kifu, null, 2)], {
    type: "application/json; charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
  showToast(`棋譜を保存しました (${filename})`);
}

// --- SSE connection ---
function openGame(gameId) {
  if (!gameId) return;
  currentGameId = gameId;

  const url = new URL(window.location.href);
  url.searchParams.set("game", gameId);
  window.history.replaceState({}, "", url);

  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }

  els.sseIndicator.className = "sse-dot";
  els.sseIndicator.title = "SSE接続中...";

  const es = new EventSource(`/api/games/${gameId}/events`);
  eventSource = es;

  es.addEventListener("open", () => {
    els.sseIndicator.className = "sse-dot connected";
    els.sseIndicator.title = "リアルタイム同期中 (SSE)";
  });

  es.addEventListener("state", (ev) => {
    try {
      const state = JSON.parse(ev.data);
      renderState(state);
      fetchGamesList(currentGameId).catch(() => {});
    } catch (err) {
      console.error("Failed to parse SSE state", err);
    }
  });

  es.addEventListener("error", () => {
    els.sseIndicator.className = "sse-dot error";
    els.sseIndicator.title = "接続が切れました（自動再接続中...）";
  });
}

// --- Rendering ---
function colorLabel(color) {
  return color === "B" ? "黒 (B)" : "白 (W)";
}

function renderState(state) {
  currentState = state;
  const mySeat = getMySeat(state.game_id);
  const isMyTurn =
    Boolean(mySeat) &&
    state.status === "playing" &&
    state.current_turn === mySeat.color;

  els.gameTitle.textContent = state.title;
  els.gameIdPill.textContent = `ID: ${state.game_id}`;
  if (state.status === "finished") {
    els.statusBadge.textContent = "終局";
    els.statusBadge.className = "pill pill-finished";
  } else {
    els.statusBadge.textContent = "対局中";
    els.statusBadge.className = "pill pill-live";
  }

  renderPlayerCard("B", state, mySeat);
  renderPlayerCard("W", state, mySeat);

  if (mySeat) {
    els.seatStatusHint.textContent = `あなたは ${colorLabel(mySeat.color)} (${mySeat.name}) として対局中です`;
  } else {
    els.seatStatusHint.textContent =
      "空席のボタンを押すと対局に参加できます（押さなければ観戦モード）";
  }
  els.moveCommentBar.classList.toggle("hidden", !mySeat || state.status !== "playing");

  els.turnBanner.className = "turn-banner";
  if (state.status === "finished") {
    els.turnBanner.classList.add("finished");
    if (state.winner === "draw") {
      els.turnBanner.textContent = `終局：引き分け（黒 ${state.counts.B} - 白 ${state.counts.W}）`;
    } else {
      const winPlayer = state.players[state.winner]?.name || colorLabel(state.winner);
      const reason = state.end_reason === "resign" ? "（投了）" : "";
      els.turnBanner.textContent = `終局：${colorLabel(state.winner)}「${winPlayer}」の勝利！${reason}（黒 ${state.counts.B} - 白 ${state.counts.W}）`;
    }
  } else if (isMyTurn) {
    els.turnBanner.classList.add("your-turn");
    els.turnBanner.textContent = `あなたの手番です（${colorLabel(mySeat.color)}）— 光っているマスをクリックして着手してください`;
  } else {
    const turnName = state.players[state.current_turn]?.name || "未参加";
    els.turnBanner.textContent = `現在の手番：${colorLabel(state.current_turn)}（${turnName}）`;
  }

  renderBoard(state, isMyTurn);
  renderHistory(state);
  renderAgentPanel(state);
}

function renderPlayerCard(color, state, mySeat) {
  const card = color === "B" ? els.cardB : els.cardW;
  const nameEl = color === "B" ? els.nameB : els.nameW;
  const scoreEl = color === "B" ? els.scoreB : els.scoreW;
  const actionsEl = color === "B" ? els.actionsB : els.actionsW;

  const info = state.players[color];
  nameEl.textContent = info.joined ? info.name : "空席（参加待ち）";
  scoreEl.textContent = String(state.counts[color]);

  card.classList.toggle(
    "active-turn",
    state.status === "playing" && state.current_turn === color
  );
  card.classList.toggle(
    "winner-card",
    state.status === "finished" && state.winner === color
  );

  actionsEl.innerHTML = "";
  const isMe = mySeat && mySeat.color === color;

  if (isMe) {
    const badge = document.createElement("span");
    badge.className = "pill pill-live";
    badge.textContent = "あなた";
    actionsEl.appendChild(badge);

    if (state.status === "playing") {
      const resignBtn = document.createElement("button");
      resignBtn.className = "btn btn-sm btn-danger";
      resignBtn.textContent = "投了";
      resignBtn.addEventListener("click", resignGame);
      actionsEl.appendChild(resignBtn);
    }

    const spectateBtn = document.createElement("button");
    spectateBtn.className = "btn btn-sm btn-ghost";
    spectateBtn.textContent = "観戦表示へ";
    spectateBtn.title = "このブラウザの操作権限を外します";
    spectateBtn.addEventListener("click", () => {
      clearSeat(state.game_id);
      renderState(state);
    });
    actionsEl.appendChild(spectateBtn);
  } else if (!info.joined && !mySeat && state.status === "playing") {
    const joinBtn = document.createElement("button");
    joinBtn.className = "btn btn-sm btn-primary";
    joinBtn.textContent = color === "B" ? "黒で参加" : "白で参加";
    joinBtn.addEventListener("click", () => joinSeat(color));
    actionsEl.appendChild(joinBtn);
  }
}

function renderBoard(state, isMyTurn) {
  const legalSet = new Set(isMyTurn ? state.legal_moves : []);
  const lastPos =
    state.last_move && !state.last_move.passed && !state.last_move.resigned
      ? state.last_move.position
      : null;
  const flippedSet = new Set(state.last_move?.flipped || []);

  els.board.innerHTML = "";

  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const pos = `${COLS[c]}${r + 1}`;
      const cellVal = state.board[r][c];
      const cellBtn = document.createElement("button");
      cellBtn.type = "button";
      cellBtn.className = "cell";
      cellBtn.dataset.pos = pos;
      cellBtn.setAttribute("aria-label", `${pos}`);

      if (flippedSet.has(pos)) {
        cellBtn.classList.add("flipped-cell");
      }

      if (cellVal === "B" || cellVal === "W") {
        const stone = document.createElement("span");
        stone.className = `stone ${cellVal}`;
        if (pos === lastPos) {
          stone.classList.add("last-move");
        }
        cellBtn.appendChild(stone);
        cellBtn.disabled = true;
      } else if (legalSet.has(pos)) {
        cellBtn.classList.add("legal");
        cellBtn.title = `${pos} に置く`;
        cellBtn.addEventListener("click", () => submitMove(pos));
      } else {
        cellBtn.disabled = true;
      }

      els.board.appendChild(cellBtn);
    }
  }
}

function renderHistory(state) {
  els.moveCountBadge.textContent = `${state.history.length} 手`;
  els.historyList.innerHTML = "";

  if (state.history.length === 0) {
    const li = document.createElement("li");
    li.className = "history-empty";
    li.textContent = "まだ着手はありません";
    els.historyList.appendChild(li);
    return;
  }

  for (const item of state.history) {
    const li = document.createElement("li");
    li.className = "history-item";

    const head = document.createElement("div");
    head.className = "history-item-head";

    const num = document.createElement("span");
    num.className = "history-num";
    num.textContent = `#${item.move_number}`;

    const stone = document.createElement("span");
    stone.className = `mini-stone ${item.color}`;

    const action = document.createElement("span");
    if (item.resigned) {
      action.textContent = `${colorLabel(item.color)} 投了`;
    } else if (item.passed) {
      action.textContent = `${colorLabel(item.color)} パス`;
    } else {
      action.textContent = `${item.position}`;
    }

    const meta = document.createElement("span");
    meta.className = "history-meta";
    if (!item.passed && !item.resigned) {
      meta.textContent = `反転 ${item.flipped.length}枚`;
    }

    head.append(num, stone, action, meta);
    li.appendChild(head);

    if (item.comment) {
      const commentEl = document.createElement("div");
      commentEl.className = "history-comment";
      commentEl.textContent = item.comment;
      li.appendChild(commentEl);
    }

    els.historyList.appendChild(li);
  }

  els.historyList.scrollTop = els.historyList.scrollHeight;
}

function renderAgentPanel(state) {
  const baseUrl = window.location.origin;
  const openColor = !state.players.W.joined ? "W" : !state.players.B.joined ? "B" : "auto";

  const promptText = [
    `リバーシAPIサーバー (${baseUrl}) で開催中の対局 (game_id: ${state.game_id}) に参加して対戦してください。`,
    `1. まず ${baseUrl}/api/guide を読んでAPIの使い方・UTF-8注意点・待機ループの停止ルールを確認してください。`,
    `2. POST ${baseUrl}/api/games/${state.game_id}/join に color="${openColor}" で参加してください。`,
    `3. 終局まで GET /wait で自分の手番を待ち、board_ascii と legal_moves を見て最善手を考え、短い思考コメント(comment)を添えて POST /moves で着手してください。`,
    `4. HTTPエラー・JSON解析失敗・status=="finished" を受け取った場合は再試行せず即座にループを終了して結果を報告してください。`,
  ].join("\n");

  const curlText = [
    `# 1. 参加してトークンを取得`,
    `curl -s -X POST "${baseUrl}/api/games/${state.game_id}/join" \\`,
    `  -H "Content-Type: application/json; charset=utf-8" \\`,
    `  -d '{"color":"${openColor}","name":"AI-Agent"}'`,
    ``,
    `# 2. 自分の手番が来るまで待機 (例: color=${openColor === "auto" ? "W" : openColor})`,
    `curl -s "${baseUrl}/api/games/${state.game_id}/wait?color=${openColor === "auto" ? "W" : openColor}&timeout=60"`,
    ``,
    `# 3. 着手する`,
    `curl -s -X POST "${baseUrl}/api/games/${state.game_id}/moves" \\`,
    `  -H "Content-Type: application/json; charset=utf-8" \\`,
    `  -H "X-Player-Token: <YOUR_TOKEN>" \\`,
    `  -d '{"position":"D3","comment":"序盤の主導権を狙います"}'`,
  ].join("\n");

  els.agentPromptBox.textContent = promptText;
  els.agentCurlBox.textContent = curlText;
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(`${label}をコピーしました`);
  } catch {
    showToast("コピーに失敗しました", true);
  }
}

// --- Event wiring ---
els.createGameBtn.addEventListener("click", createGame);
els.newGameTitle.addEventListener("keydown", (e) => {
  if (e.key === "Enter") createGame();
});
els.refreshGamesBtn.addEventListener("click", () => fetchGamesList(currentGameId));
els.gameSelect.addEventListener("change", () => {
  if (els.gameSelect.value) openGame(els.gameSelect.value);
});
els.downloadKifuBtn.addEventListener("click", downloadKifuJson);
els.copyPromptBtn.addEventListener("click", () =>
  copyText(els.agentPromptBox.textContent, "エージェント向けプロンプト")
);
els.copyCurlBtn.addEventListener("click", () =>
  copyText(els.agentCurlBox.textContent, "curlコマンド")
);

// --- Init ---
async function init() {
  const urlGameId = new URLSearchParams(window.location.search).get("game");
  let games = await fetchGamesList(urlGameId);

  if (games.length === 0) {
    const created = await apiFetch("/api/games", {
      method: "POST",
      body: JSON.stringify({ title: "First Match" }),
    });
    games = await fetchGamesList(created.game_id);
    openGame(created.game_id);
    return;
  }

  const initialId =
    urlGameId && games.some((g) => g.game_id === urlGameId)
      ? urlGameId
      : games[0].game_id;
  els.gameSelect.value = initialId;
  openGame(initialId);
}

init().catch((err) => {
  console.error(err);
  showToast(`初期化エラー: ${err.message}`, true);
});
