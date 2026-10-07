"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  token: "",
  settings: {},
  conversations: [],
  conversation: null,
  messages: [],
  mode: "chat",
  busy: false,
  models: [],
  project: null,
  selected: new Set(),
  newPaths: new Set(),
  controller: null,
  request: null,
  followBottom: true,
  connectionReady: false,
  switching: false,
};
let toastTimer,
  ticker,
  searchTimer,
  searchRevision = 0;
function readSaved(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem("local-desk:" + key)) ?? fallback;
  } catch {
    return fallback;
  }
}
function saveLocal(key, value) {
  try {
    localStorage.setItem("local-desk:" + key, JSON.stringify(value));
  } catch {
    /* Private browsing or full storage must not prevent using the app. */
  }
}
const preferences = readSaved("preferences", {});
function saveDraft() {
  if (!state.conversation) return;
  const drafts = readSaved("drafts", {});
  const value = $("prompt").value;
  delete drafts[state.conversation];
  if (value) drafts[state.conversation] = value;
  const entries = Object.entries(drafts).slice(-30);
  saveLocal("drafts", Object.fromEntries(entries));
}
function restoreDraft() {
  $("prompt").value = readSaved("drafts", {})[state.conversation] || "";
  resizePrompt();
  updateSendButton();
}
function resizePrompt() {
  $("prompt").style.height = "auto";
  $("prompt").style.height = Math.min($("prompt").scrollHeight, 180) + "px";
}
function updateSendButton() {
  $("send").disabled =
    state.busy ||
    state.switching ||
    !state.connectionReady ||
    !$("prompt").value.trim();
}
function saveWorkspace() {
  saveLocal("workspace", {
    root: state.project?.root || "",
    files: [...state.selected],
    panel: !$("project-pane").hidden,
  });
}
function renderRecentProjects() {
  const select = $("recent-projects");
  select.replaceChildren(new Option("最近使ったフォルダー", ""));
  for (const root of readSaved("recent-projects", []))
    select.append(new Option(root, root));
}
async function loadProject(root, selected = []) {
  if (!root) return toast("フォルダーのパスを入力してください。");
  $("project-load").disabled = true;
  try {
    const project = await api("/api/project", { root });
    if (state.project?.root !== project.root) {
      state.newPaths.clear();
      scopeOptions.ranges = {};
    }
    state.project = project;
    const allowed = new Set(project.files.map((file) => file.path));
    for (const path of state.newPaths)
      if (allowed.has(path)) state.newPaths.delete(path);
    state.selected = new Set(
      selected.filter((path) => allowed.has(path)).slice(0, 6),
    );
    $("project-root").value = project.root;
    const recent = readSaved("recent-projects", []).filter(
      (path) => path !== project.root,
    );
    saveLocal("recent-projects", [project.root, ...recent].slice(0, 5));
    renderRecentProjects();
    renderFiles();
    renderAttachments();
    saveWorkspace();
  } finally {
    $("project-load").disabled = false;
  }
}
async function restoreWorkspace() {
  const workspace = readSaved("workspace", {});
  renderRecentProjects();
  if (!workspace.root) return;
  $("project-root").value = workspace.root;
  $("project-pane").hidden = !workspace.panel;
  try {
    await loadProject(workspace.root, workspace.files || []);
  } catch (error) {
    toast("参照フォルダーを復元できませんでした。" + error.message);
  }
}
async function reuseQuestion(message) {
  if (state.busy) return toast("生成が終わってから質問を編集してください。");
  if (
    $("prompt").value &&
    $("prompt").value !== message.content &&
    !(await confirmAction("入力中の下書きを、この質問に置き換えますか？"))
  )
    return;
  setMode(message.metadata?.mode || "chat");
  $("prompt").value = message.content;
  saveDraft();
  resizePrompt();
  updateSendButton();
  $("prompt").focus();
  if (state.mode === "code" || state.mode === "edit") {
    $("project-pane").hidden = false;
    saveWorkspace();
  }
  toast(
    "質問を入力欄に戻しました。内容と参照ファイルを確認して送信してください。",
  );
  return true;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 7000);
}
async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-App-Token": state.token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "通信に失敗しました。");
  return data;
}
function on(id, event, fn) {
  $(id).addEventListener(event, (e) => {
    Promise.resolve(fn(e)).catch((error) => toast(error.message));
  });
}
function confirmAction(text) {
  return new Promise((resolve) => {
    const dialog = node("dialog", "confirm-dialog");
    dialog.setAttribute("aria-label", "操作の確認");
    const cancel = node("button", "secondary-button", "キャンセル");
    const accept = node("button", "send-button", "続ける");
    let accepted = false;
    dialog.append(
      node("h2", "", "操作の確認"),
      node("p", "", text),
      cancel,
      accept,
    );
    document.body.append(dialog);
    cancel.addEventListener("click", () => dialog.close());
    accept.addEventListener("click", () => {
      accepted = true;
      dialog.close();
    });
    dialog.addEventListener(
      "close",
      () => {
        dialog.remove();
        resolve(accepted);
      },
      { once: true },
    );
    dialog.showModal();
    cancel.focus();
  });
}
function codeBlock(code, language = "code") {
  const box = node("div", "code-block"),
    head = node("div", "code-head"),
    copy = node("button", "text-button", "コピー");
  head.append(node("span", "", language || "code"), copy);
  const pre = node("pre"),
    content = node("code", "", code);
  highlightCode(content, code, language);
  pre.append(content);
  box.append(head, pre);
  copy.addEventListener("click", () =>
    navigator.clipboard.writeText(code).then(
      () => toast("コピーしました。"),
      () => toast("コピーできませんでした。"),
    ),
  );
  return box;
}
function markdown(text) {
  return renderSafeMarkdown(text);
}
function welcome() {
  const wrap = node("div", "welcome");
  wrap.append(
    node("div", "welcome-label", "LOCAL INTELLIGENCE"),
    node("h2", "", "考える、書く、つくる。\n自分のPCで。"),
    node(
      "p",
      "",
      "文章の相談からコードの修正まで。選んだファイルを参照しながら、手元のAIと作業を進められます。",
    ),
  );
  const grid = node("div", "suggestions");
  const suggestions = [
    [
      "アイデアを整理する",
      "作りたいものを、一緒に具体化。",
      "chat",
      "Pythonで日常の作業を便利にするアプリのアイデアを3つ提案してください。",
    ],
    [
      "文章をすっきりまとめる",
      "長い文章から、必要な要点だけ。",
      "summary",
      "次の文章を要約してください。\n\n",
    ],
    [
      "コードを読み解く",
      "ファイルを選んで、仕組みを質問。",
      "code",
      "選択したコードの役割と処理の流れを説明してください。",
    ],
    [
      "修正案を確認する",
      "変更前後を見て、反映と取り消し。",
      "edit",
      "選択したコードを読み、修正が必要な問題を一つ見つけて改善案を作ってください。",
    ],
  ];
  for (const [title, desc, mode, prompt] of suggestions) {
    const button = node("button", "suggestion");
    button.append(node("strong", "", title), node("span", "", desc));
    button.addEventListener("click", () => {
      setMode(mode);
      $("prompt").value = prompt;
      saveDraft();
      resizePrompt();
      updateSendButton();
      $("prompt").focus();
      if (mode === "code" || mode === "edit") $("project-pane").hidden = false;
      saveWorkspace();
    });
    grid.append(button);
  }
  wrap.append(grid);
  return wrap;
}
function showProposal(message, body) {
  const metadata = message.metadata;
  if (metadata.edits?.length) {
    const button = node(
      "button",
      "secondary-button",
      "行差分を確認して、選んだ箇所をまとめて反映",
    );
    button.addEventListener("click", () =>
      showLineDiff(message).catch((error) => toast(error.message)),
    );
    body.append(button);
  }
  if (metadata.explanation) body.append(markdown(metadata.explanation));
  if (metadata.proposal_warning)
    body.append(node("p", "warning", metadata.proposal_warning));
  if (metadata.proposal_warning && message.content) {
    const raw = node("details", "thoughts");
    raw.append(
      node("summary", "", "モデルの出力を見る"),
      node("pre", "", message.content),
    );
    body.append(raw);
  }
  (metadata.edits || []).forEach((edit, index) => {
    const box = node("div", "edit-card");
    box.append(node("div", "edit-title", edit.path));
    const diff = node("div", "diff-grid");
    for (const [label, content, className] of [
      ["変更前", edit.old, ""],
      ["変更後", edit.new, "after"],
    ]) {
      const col = node("div");
      col.append(
        node("div", "diff-label", label),
        node("pre", className, content),
      );
      diff.append(col);
    }
    const actions = node("div", "edit-actions"),
      apply = node("button", "secondary-button", "この変更を反映"),
      status = node("span", "", "変更前の一致確認とバックアップを行います。");
    actions.append(apply, status);
    function addUndo(changeId) {
      apply.hidden = true;
      const undo = node("button", "text-button", "取り消す");
      actions.append(undo);
      undo.addEventListener("click", async () => {
        undo.disabled = true;
        try {
          const data = await api("/api/undo", { id: changeId });
          status.textContent = data.message;
          undo.hidden = true;
          edit.undone = true;
          apply.hidden = false;
          apply.disabled = false;
        } catch (error) {
          toast(error.message);
          undo.disabled = false;
        }
      });
    }
    if (edit.change_id && !edit.undone) {
      status.textContent = "反映済み。バックアップから取り消せます。";
      addUndo(edit.change_id);
    }
    apply.addEventListener("click", async () => {
      apply.disabled = true;
      try {
        const result = await api("/api/apply", { message: message.id, index });
        status.textContent = result.message;
        edit.change_id = result.change_id;
        edit.undone = false;
        if (state.project)
          await loadProject(state.project.root, [
            ...state.selected,
            ...state.newPaths,
          ]);
        addUndo(result.change_id);
      } catch (error) {
        toast(error.message);
        apply.disabled = false;
      }
    });
    box.append(diff, actions);
    body.append(box);
  });
}
function messageElement(message, streaming = false) {
  const element = node(
    "article",
    "message " + message.role + (streaming ? " streaming" : ""),
  );
  const heading = node("div", "message-heading");
  heading.append(
    node("span", "avatar", message.role === "user" ? "U" : "L"),
    node("span", "", message.role === "user" ? "あなた" : "Local Desk"),
  );
  const body = node("div", "message-content");
  element.append(heading, body);
  updateMessage(element, message, streaming);
  if (message.role === "user") {
    const actions = node("div", "message-meta");
    const reuse = node("button", "text-button", "質問を編集");
    reuse.addEventListener("click", () => reuseQuestion(message));
    actions.append(reuse);
    element.append(actions);
  }
  return element;
}
function updateMessage(element, message, streaming = false) {
  const body = element.querySelector(".message-content");
  if (streaming) {
    // Keep one text node during generation. Markdown parsing/highlighting runs
    // once on completion instead of replacing the entire tree on each delta.
    let live = body.querySelector(".live-text");
    if (!live) {
      body.replaceChildren();
      live = node("div", "live-text");
      live.append(document.createTextNode(""));
      body.append(live);
    }
    const metadata = message.metadata || {};
    live.firstChild.data =
      metadata.mode === "edit"
        ? "修正案を作成しています…"
        : message.content ||
          (metadata.reasoning
            ? "モデルが考えています…"
            : "入力を読み込んでいます…");
    return;
  }
  body.replaceChildren();
  const metadata = message.metadata || {};
  if (metadata.reasoning) {
    const details = node("details", "thoughts"),
      summary = node("summary", "", "考えた過程");
    details.append(summary, node("pre", "", metadata.reasoning));
    body.append(details);
  }
  if (message.role === "user")
    body.append(document.createTextNode(message.content));
  else if (!streaming && metadata.mode === "edit" && metadata.edits)
    showProposal(message, body);
  else if (streaming && metadata.mode === "edit")
    body.append(node("p", "stream-placeholder", "修正案を作成しています…"));
  else if (message.content) body.append(markdown(message.content));
  else
    body.append(
      node(
        "p",
        "stream-placeholder",
        streaming
          ? metadata.reasoning
            ? "モデルが考えています…"
            : "入力を読み込んでいます…"
          : metadata.cancelled
            ? "生成を停止しました。"
            : "回答がありません。",
      ),
    );
  if (metadata.finish_reason === "length")
    body.append(
      node(
        "p",
        "warning",
        "最大出力に達しました。続きが必要な場合は再質問するか、最大出力を増やしてください。",
      ),
    );
  if (metadata.error) body.append(node("div", "message-error", metadata.error));
  if (metadata.files?.length) {
    const badges = node("div", "file-badges");
    metadata.files.forEach((path) => badges.append(node("span", "", path)));
    body.append(badges);
  }
  if (metadata.citations?.length && !streaming) {
    const references = node("details", "thoughts");
    references.append(node("summary", "", "根拠として示した参照箇所"));
    for (const citation of metadata.citations) {
      references.append(
        node(
          "p",
          citation.verified ? "" : "warning",
          `${citation.path}:L${citation.start}-L${citation.end} · ${citation.verified ? "送信した範囲内" : "参照範囲を確認できません"}`,
        ),
      );
      if (citation.excerpt)
        references.append(node("pre", "", citation.excerpt));
    }
    body.append(references);
  }
  if (metadata.structured_warning)
    body.append(node("p", "warning", metadata.structured_warning));
  element.querySelector(".message-meta")?.remove();
  if (message.role === "assistant" && !streaming) {
    const meta = node("div", "message-meta");
    if (metadata.seconds !== undefined)
      meta.append(node("span", "", `${metadata.seconds}秒`));
    const tokens = metadata.usage?.completion_tokens;
    if (tokens) {
      meta.append(node("span", "", `${tokens.toLocaleString()} tokens`));
      if (metadata.seconds > 0)
        meta.append(
          node(
            "span",
            "",
            `${(tokens / metadata.seconds).toFixed(1)} tokens/秒（全体平均）`,
          ),
        );
    }
    if (metadata.cancelled) meta.append(node("span", "", "停止済み"));
    if (metadata.clipped)
      meta.append(node("span", "warning", "長い参照内容を一部省略"));
    const copy = node("button", "text-button", "回答をコピー");
    copy.addEventListener("click", () =>
      navigator.clipboard
        .writeText(message.content)
        .then(() => toast("コピーしました。")),
    );
    meta.append(copy);
    if (message.id) {
      for (const [label, action] of [
        ["再生成", "regenerate"],
        ["続き", "continue"],
        ["ここから分岐", "fork"],
      ]) {
        const button = node("button", "text-button", label);
        button.addEventListener("click", () =>
          conversationAction(action, message).catch((error) =>
            toast(error.message),
          ),
        );
        meta.append(button);
      }
    }
    element.append(meta);
  }
}
function renderMessages() {
  const container = $("messages");
  state.followBottom = true;
  $("jump-latest").hidden = true;
  container.replaceChildren();
  if (!state.messages.length) container.append(welcome());
  else
    state.messages.forEach((message) =>
      container.append(messageElement(message)),
    );
  if (state.messages.length) scrollBottom();
  else container.scrollTop = 0;
}
function scrollBottom() {
  const area = $("messages");
  state.followBottom = true;
  area.scrollTop = area.scrollHeight;
  $("jump-latest").hidden = true;
}
function followResponse() {
  if (state.followBottom) scrollBottom();
  else $("jump-latest").hidden = false;
}
function renderConversations(items) {
  const container = $("conversations");
  container.replaceChildren();
  if (!items.length)
    container.append(
      node("p", "muted history-empty", "該当する会話がありません。"),
    );
  for (const item of items) {
    const button = node(
      "button",
      "conversation" + (item.id === state.conversation ? " active" : ""),
      (item.pinned ? "★ " : "") + item.title,
    );
    button.title = item.title + " " + JSON.parse(item.tags || "[]").join(" · ");
    button.addEventListener("click", () =>
      openConversation(item.id).catch((error) => toast(error.message)),
    );
    container.append(button);
  }
}
async function searchConversations() {
  const revision = ++searchRevision;
  const query = $("conversation-search").value.trim();
  const items = query
    ? await api("/api/conversations?q=" + encodeURIComponent(query))
    : state.conversations;
  if (revision === searchRevision) renderConversations(items);
}
async function refreshConversations() {
  state.conversations = await api("/api/conversations");
  await searchConversations();
  const current = state.conversations.find(
    (item) => item.id === state.conversation,
  );
  $("chat-title").textContent = current?.title || "新しい会話";
  $("pin-chat").textContent = current?.pinned ? "★" : "☆";
  $("pin-chat").setAttribute("aria-pressed", String(!!current?.pinned));
}
async function openConversation(id) {
  if (state.busy) return toast("生成を停止してから会話を切り替えてください。");
  const messages = await api("/api/messages?id=" + encodeURIComponent(id));
  saveDraft();
  state.conversation = id;
  state.messages = messages;
  restoreDraft();
  saveLocal("conversation", id);
  renderMessages();
  await refreshConversations();
  $("sidebar").classList.remove("open");
}
async function newConversation() {
  if (state.busy || state.switching)
    return toast("生成を停止してから新しい会話を開いてください。");
  await refreshConversations();
  const blank = state.conversations.find(
    (item) => item.message_count === 0 && !readSaved("drafts", {})[item.id],
  );
  const data = blank || (await api("/api/conversations", {}));
  $("conversation-search").value = "";
  await openConversation(data.id);
  $("prompt").focus();
}
function setMode(mode) {
  if (state.busy) return;
  if (
    !["chat", "summary", "translate", "code", "edit", "extract"].includes(mode)
  )
    mode = "chat";
  state.mode = mode;
  preferences.mode = mode;
  saveLocal("preferences", preferences);
  document.querySelectorAll(".mode").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  });
  $("prompt").placeholder = {
    chat: "何を手伝いましょうか？",
    summary: "要約したい文章を入力してください",
    translate: "翻訳したい文章と、翻訳先の言語を入力してください",
    code: "選択したコードについて質問してください",
    edit: "選択したコードを、どう修正したいですか？",
    extract: "JSONへ抽出したい文章と項目を入力してください",
  }[mode];
}
function setBusy(value) {
  state.busy = value;
  $("send").hidden = value;
  $("stop").hidden = !value;
  $("prompt").disabled = value;
  $("stop").disabled = false;
  document.querySelectorAll(".mode").forEach((b) => (b.disabled = value));
  $("delete-chat").disabled = value;
  $("new-chat").disabled = value;
  $("settings-open").disabled = value;
  $("rename-open").disabled = value;
  $("connection").disabled = value;
  $("connection-settings").disabled = value;
  $("project-load").disabled = value;
  $("recent-projects").disabled = value;
  updateSendButton();
}
async function refreshModels() {
  const result = await api("/api/models");
  state.models = result.models;
  state.connectionReady =
    result.connected && result.models.includes(state.settings.model);
  $("connection-help").hidden = state.connectionReady;
  $("connection-help-title").textContent = result.connected
    ? "使用するモデルを選択してください"
    : "推論サーバーに接続できません";
  $("connection-help-text").textContent = result.connected
    ? "接続設定でモデルを選ぶか、設定のモデル切り替えで準備してください。"
    : state.desktop
      ? "設定で同じモデルを再試行するか、別のモデルを選んでください。左下から診断を確認できます。"
      : "設定のモデル切り替え、またはStart.cmdで起動してください。起動後は「再確認」を押します。";
  updateSendButton();
  $("connection").textContent = result.connected
    ? result.models.length
      ? "ローカル接続中"
      : "モデルを準備してください"
    : "サーバー未接続";
  $("connection").className =
    "connection " +
    (result.connected && result.models.length ? "online" : "offline");
  $("connection").title = result.error || result.endpoint;
  const select = $("model"),
    previous = select.value || state.settings.model;
  select.replaceChildren();
  for (const id of state.models) select.append(new Option(id, id));
  if (previous && !state.models.includes(previous))
    select.append(new Option(previous + "（設定中）", previous));
  select.value = previous;
  return result;
}
function renderAttachments() {
  const box = $("attachments");
  box.replaceChildren();
  for (const path of state.newPaths) {
    const button = node("button", "attachment", "作成: " + path + " ×");
    button.type = "button";
    button.addEventListener("click", () => {
      state.newPaths.delete(path);
      renderAttachments();
    });
    box.append(button);
  }
  for (const path of state.selected) {
    const button = node("button", "attachment", path + " ×");
    button.type = "button";
    button.title = "参照から外す";
    button.addEventListener("click", () => {
      state.selected.delete(path);
      renderAttachments();
      renderFiles();
      saveWorkspace();
    });
    box.append(button);
  }
}
function renderFiles() {
  const box = $("files");
  box.replaceChildren();
  const filter = $("file-filter").value.toLowerCase();
  const files =
    state.project?.files.filter((f) => f.path.toLowerCase().includes(filter)) ||
    [];
  for (const file of files) {
    const row = node("div", "file-row"),
      check = node("input"),
      label = node("label", "", file.path),
      preview = node("button", "text-button", "見る");
    check.type = "checkbox";
    check.checked = state.selected.has(file.path);
    const id = "file-" + file.path;
    check.id = id;
    label.htmlFor = id;
    label.title = file.path;
    check.addEventListener("change", () => {
      if (check.checked) {
        if (state.selected.size + state.newPaths.size >= 6) {
          check.checked = false;
          return toast("選択できるのは6ファイルまでです。");
        }
        state.selected.add(file.path);
      } else state.selected.delete(file.path);
      renderAttachments();
      saveWorkspace();
    });
    preview.addEventListener("click", async () => {
      try {
        const result = await api("/api/file", {
          root: state.project.root,
          path: file.path,
        });
        const dialog = node("dialog", "preview-dialog"),
          head = node("div", "panel-head"),
          close = node("button", "icon-button", "×");
        head.append(node("h2", "", file.path), close);
        dialog.append(head, node("pre", "", result.content));
        document.body.append(dialog);
        close.addEventListener("click", () => dialog.close());
        dialog.addEventListener("close", () => dialog.remove());
        dialog.showModal();
      } catch (error) {
        toast(error.message);
      }
    });
    row.append(check, label, preview);
    box.append(row);
  }
  if (!files.length)
    box.append(
      node(
        "div",
        "empty-files",
        state.project
          ? "該当するファイルがありません。"
          : "フォルダーを指定してください。",
      ),
    );
  $("project-info").textContent = state.project
    ? `${state.project.files.length}ファイル${state.project.truncated ? "（先頭1,000件）" : ""} · .envや依存フォルダーは対象外`
    : "";
}
async function send(event, forcePreview = false) {
  event.preventDefault();
  if (state.busy || state.switching || state.previewing) return;
  const prompt = $("prompt").value.trim();
  if (!prompt) return;
  if (!state.connectionReady)
    return toast("先に推論サーバーとの接続とモデルを確認してください。");
  if (!state.conversation) await newConversation();
  if (state.mode === "edit" && !state.selected.size && !state.newPaths.size)
    return toast("コード修正には参照ファイルを選択してください。");
  let requestBody;
  state.previewing = true;
  try {
    requestBody = await prepareUserRequest(prompt, forcePreview);
  } finally {
    state.previewing = false;
  }
  if (!requestBody) return;
  const requestId = crypto.randomUUID();
  state.request = requestId;
  state.controller = new AbortController();
  const user = {
    role: "user",
    content: prompt,
    metadata: {
      files: [...state.selected],
      new_paths: [...state.newPaths],
      root: state.project?.root || "",
      mode: state.mode,
    },
  };
  const answer = {
    role: "assistant",
    content: "",
    metadata: { mode: state.mode },
  };
  state.messages.push(user, answer);
  if ($("messages").querySelector(".welcome")) $("messages").replaceChildren();
  $("messages").append(messageElement(user));
  const output = messageElement(answer, true);
  $("messages").append(output);
  $("prompt").value = "";
  saveDraft();
  resizePrompt();
  setBusy(true);
  scrollBottom();
  const start = performance.now();
  let stage = "入力を読み込み中";
  $("generation-info").textContent = stage;
  ticker = setInterval(
    () =>
      ($("generation-info").textContent =
        `${stage} ${((performance.now() - start) / 1000).toFixed(0)}秒`),
    1000,
  );
  let completed = false;
  let renderFrame = null;
  const scheduleRender = () => {
    if (renderFrame !== null) return;
    renderFrame = requestAnimationFrame(() => {
      renderFrame = null;
      updateMessage(output, answer, true);
      followResponse();
    });
  };
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-App-Token": state.token,
      },
      body: JSON.stringify({
        ...requestBody,
        request_id: requestId,
      }),
      signal: state.controller.signal,
    });
    if (!response.ok) {
      const data = await response.json();
      throw new Error(data.error);
    }
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const eventName = block.match(/^event: (.+)$/m)?.[1];
        const dataLine = block.match(/^data: (.+)$/m)?.[1];
        if (!dataLine) continue;
        const data = JSON.parse(dataLine);
        if (eventName === "start") {
          stage = "モデルの応答待ち";
          $("generation-info").textContent = stage;
          answer.metadata.clipped = data.clipped;
          answer.metadata.files = data.files;
          if (data.clipped)
            toast("入力が長いため、参照内容の末尾を省略しました。");
        } else if (eventName === "reset") {
          answer.content = "";
          stage = data.stage;
          $("generation-info").textContent = stage;
          answer.metadata.repair_attempts = 1;
        } else if (eventName === "delta") {
          stage = data.content ? "回答中" : data.reasoning ? "思考中" : stage;
          $("generation-info").textContent = stage;
          answer.content += data.content || "";
          answer.metadata.reasoning =
            (answer.metadata.reasoning || "") + (data.reasoning || "");
          scheduleRender();
        } else if (eventName === "error") {
          answer.metadata.error = data.error;
          toast(data.error);
        } else if (eventName === "done") {
          answer.id = data.id;
          answer.content = data.content;
          answer.metadata = data.metadata;
          completed = true;
        }
      }
      if (done) break;
    }
    if (!completed)
      throw new Error("接続が途中で終了しました。履歴を確認してください。");
  } catch (error) {
    if (error.name === "AbortError") {
      answer.metadata.cancelled = true;
      toast("生成を停止しました。");
    } else {
      answer.metadata.error = error.message;
      toast(error.message);
    }
  } finally {
    if (renderFrame !== null) cancelAnimationFrame(renderFrame);
    clearInterval(ticker);
    $("generation-info").textContent = "";
    setBusy(false);
    output.classList.remove("streaming");
    updateMessage(output, answer);
    followResponse();
    if (answer.metadata.error && !$("prompt").value) {
      $("prompt").value = prompt;
      saveDraft();
      resizePrompt();
      updateSendButton();
    }
    await refreshConversations();
    if (!completed) {
      try {
        state.messages = await api("/api/messages?id=" + state.conversation);
        renderMessages();
      } catch {}
    }
    $("prompt").focus();
  }
}
function download(name, text, type = "text/markdown") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = node("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function init() {
  updateSendButton();
  const boot = await fetch("/api/bootstrap").then((r) => r.json());
  state.token = boot.token;
  state.settings = boot.settings;
  state.desktop = boot.desktop;
  document.dispatchEvent(new CustomEvent("local-desk-ready"));
  if (boot.notices?.length) toast(boot.notices.join(" "));
  $("model-label").textContent = state.settings.model;
  const modelRefresh = refreshModels();
  await refreshConversations();
  const previous =
    readSaved("conversation", null) || sessionStorage.getItem("conversation");
  if (previous && state.conversations.some((c) => c.id === previous))
    await openConversation(previous);
  else if (state.conversations.length)
    await openConversation(state.conversations[0].id);
  else await newConversation();
  setMode(preferences.mode || "chat");
  $("send-hint").textContent =
    preferences.enterSend === false
      ? "Ctrl+Enter / ⌘+Enterで送信"
      : "Enterで送信 · Shift+Enterで改行";
  renderFiles();
  await restoreWorkspace();
  await modelRefresh;
  if (boot.startup_error) {
    toast("モデルを準備できませんでした。" + boot.startup_error);
    await openSettings();
    $("profile-status").textContent =
      boot.startup_error +
      " 同じモデルを再試行するか、別のモデルを選べます。診断は左下から開けます。";
  }
}
on("new-chat", "click", newConversation);
window.addEventListener("local-desk-conversation", async (event) => {
  if (typeof event.detail !== "string" || state.busy || state.switching) return;
  try {
    await refreshConversations();
    if (state.conversations.some((item) => item.id === event.detail))
      await openConversation(event.detail);
  } catch (error) {
    toast(error.message);
  }
});
on("composer", "submit", send);
on("prompt", "keydown", (event) => {
  if (
    event.key === "Enter" &&
    !event.isComposing &&
    (event.ctrlKey ||
      event.metaKey ||
      (preferences.enterSend !== false && !event.shiftKey))
  ) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
document
  .querySelectorAll(".mode")
  .forEach((button) =>
    button.addEventListener("click", () => setMode(button.dataset.mode)),
  );
on("stop", "click", async () => {
  const request = state.request;
  $("stop").disabled = true;
  await api("/api/cancel", { request_id: request });
  setTimeout(() => {
    if (state.busy && state.request === request) state.controller?.abort();
  }, 5000);
});
on("project-toggle", "click", () => {
  $("project-pane").hidden = !$("project-pane").hidden;
  saveWorkspace();
});
on("project-close", "click", () => {
  $("project-pane").hidden = true;
  saveWorkspace();
});
on("project-load", "click", () => loadProject($("project-root").value.trim()));
on("project-root", "keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    return loadProject($("project-root").value.trim());
  }
});
on("recent-projects", "change", () => {
  if ($("recent-projects").value)
    return loadProject($("recent-projects").value);
});
on("project-forget", "click", () => {
  state.project = null;
  state.selected.clear();
  $("project-root").value = "";
  saveLocal("recent-projects", []);
  saveWorkspace();
  renderRecentProjects();
  renderAttachments();
  renderFiles();
  toast("参照の選択とフォルダーの記憶を解除しました。");
});
on("file-filter", "input", renderFiles);
on("menu-toggle", "click", () => $("sidebar").classList.toggle("open"));
function fillSettings() {
  const s = state.settings;
  $("endpoint").value = s.endpoint;
  $("temperature").value = s.temperature;
  $("max-tokens").value = s.max_tokens;
  $("context-length").value = s.context_length;
  $("context-length").disabled =
    /^local-desk-(bonsai|qwen|ornith|custom-[a-f0-9]{12})$/.test(s.model);
  $("system-prompt").value = s.system_prompt;
  $("thinking").checked = !!s.thinking;
  $("enter-send").checked = preferences.enterSend !== false;
  $("model").value = s.model;
}
async function refreshProfiles() {
  const profiles = await api("/api/profiles");
  $("profiles").replaceChildren();
  for (const profile of profiles) {
    const button = node(
      "button",
      "profile-button" + (profile.active ? " active" : ""),
      profile.label,
    );
    button.type = "button";
    button.disabled = !profile.installed || state.switching || state.busy;
    button.setAttribute("aria-pressed", String(profile.active));
    button.title = profile.installed
      ? "モデルを読み込んで切り替える"
      : "未導入：READMEの準備手順を確認してください";
    button.addEventListener("click", () =>
      switchProfile(profile).catch((error) => toast(error.message)),
    );
    $("profiles").append(button);
  }
}
async function switchProfile(profile, reset = false) {
  if (state.busy || state.switching) return;
  state.switching = true;
  $("profile-status").textContent = profile.label + "を読み込み中です…";
  $("prompt").disabled = true;
  $("new-chat").disabled = true;
  $("settings-form")
    .querySelectorAll("input, select, textarea, button:not(#settings-close)")
    .forEach((element) => (element.disabled = true));
  updateSendButton();
  try {
    state.settings = await api("/api/profile", { profile: profile.id, reset });
    fillSettings();
    $("model-label").textContent = state.settings.model;
    await refreshModels();
    $("profile-status").textContent =
      profile.label + "に切り替えました。会話と下書きはそのまま使えます。";
    toast(profile.label + "に切り替えました。");
  } catch (error) {
    $("profile-status").textContent =
      "切り替えに失敗しました。" + error.message;
    await refreshModels();
    throw error;
  } finally {
    state.switching = false;
    $("prompt").disabled = state.busy;
    $("new-chat").disabled = state.busy;
    $("settings-form")
      .querySelectorAll("input, select, textarea, button")
      .forEach((element) => (element.disabled = false));
    fillSettings();
    await refreshProfiles();
    updateSendButton();
  }
}
async function openSettings() {
  fillSettings();
  $("settings-dialog").showModal();
  await Promise.all([refreshModels(), refreshProfiles()]);
  $("model").value = state.settings.model;
}
on("settings-open", "click", openSettings);
on("connection", "click", openSettings);
on("connection-settings", "click", openSettings);
on("connection-retry", "click", refreshModels);
on("settings-close", "click", () => $("settings-dialog").close());
on("models-refresh", "click", refreshModels);
on("profile-reset", "click", async () => {
  const profiles = await api("/api/profiles");
  const active = profiles.find((profile) => profile.active);
  if (!active) return toast("登録済みモデルを選択してください。");
  await switchProfile(active, true);
});
on("settings-form", "submit", async (event) => {
  event.preventDefault();
  if (state.switching)
    return toast("モデルの読み込みが終わるまでお待ちください。");
  state.settings = await api("/api/settings", {
    endpoint: $("endpoint").value,
    model: $("model").value,
    temperature: Number($("temperature").value),
    max_tokens: Number($("max-tokens").value),
    context_length: Number($("context-length").value),
    system_prompt: $("system-prompt").value,
    thinking: $("thinking").checked,
  });
  preferences.enterSend = $("enter-send").checked;
  saveLocal("preferences", preferences);
  $("send-hint").textContent =
    preferences.enterSend === false
      ? "Ctrl+Enter / ⌘+Enterで送信"
      : "Enterで送信 · Shift+Enterで改行";
  $("model-label").textContent = state.settings.model;
  $("settings-dialog").close();
  await refreshModels();
  toast("設定を保存しました。");
});
on("delete-chat", "click", async () => {
  if (!state.conversation) return;
  if (
    !(await confirmAction(
      "この会話をゴミ箱に移しますか？左下のゴミ箱から復元できます。",
    ))
  )
    return;
  const deleted = state.conversation;
  await api("/api/delete", { id: deleted });
  const drafts = readSaved("drafts", {});
  delete drafts[deleted];
  saveLocal("drafts", drafts);
  state.conversation = null;
  $("prompt").value = "";
  await newConversation();
});
on("export", "click", () => {
  const title = $("chat-title").textContent;
  const body = state.messages
    .map(
      (m) =>
        `## ${m.role === "user" ? "あなた" : "Local Desk"}\n\n${m.content}`,
    )
    .join("\n\n---\n\n");
  download("local-desk-conversation.md", `# ${title}\n\n${body}\n`);
  toast("会話を保存しました。");
});
init().catch((error) => toast("起動確認に失敗しました: " + error.message));
setInterval(() => {
  if (!state.busy && state.token) refreshModels().catch(() => {});
}, 30000);

async function showChanges() {
  const changes = await api("/api/changes");
  const list = $("changes-list");
  list.replaceChildren();
  if (!changes.length)
    list.append(node("p", "muted", "反映した変更はまだありません。"));
  for (const change of changes) {
    const row = node("div", "change-row"),
      info = node("div");
    info.append(
      node("strong", "", change.path.split(/[\\/]/).pop()),
      node("small", "muted", new Date(change.created * 1000).toLocaleString()),
    );
    const undo = node(
      "button",
      "secondary-button",
      change.undone ? "取り消し済み" : "取り消す",
    );
    undo.disabled = !!change.undone;
    row.title = change.path;
    row.append(info, undo);
    list.append(row);
    undo.addEventListener("click", async () => {
      undo.disabled = true;
      try {
        const result = await api("/api/undo", { id: change.id });
        toast(result.message);
        await showChanges();
      } catch (error) {
        toast(error.message);
        undo.disabled = false;
      }
    });
  }
}
on("changes-open", "click", async () => {
  await showChanges();
  $("changes-dialog").showModal();
});
on("changes-close", "click", () => $("changes-dialog").close());

on("prompt", "input", () => {
  saveDraft();
  resizePrompt();
  updateSendButton();
});
on("conversation-search", "input", () => {
  ++searchRevision;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(
    () => searchConversations().catch((error) => toast(error.message)),
    180,
  );
});
on("messages", "scroll", () => {
  const area = $("messages");
  state.followBottom =
    area.scrollHeight - area.scrollTop - area.clientHeight < 80;
  $("jump-latest").hidden = state.followBottom;
});
on("jump-latest", "click", scrollBottom);
on("rename-open", "click", () => {
  $("conversation-name").value = $("chat-title").textContent;
  $("rename-dialog").showModal();
  $("conversation-name").select();
});
on("rename-close", "click", () => $("rename-dialog").close());
on("rename-form", "submit", async (event) => {
  event.preventDefault();
  $("rename-save").disabled = true;
  try {
    await api("/api/rename", {
      id: state.conversation,
      title: $("conversation-name").value,
    });
    $("rename-dialog").close();
    await refreshConversations();
    toast("会話の名前を変更しました。");
  } finally {
    $("rename-save").disabled = false;
  }
});
