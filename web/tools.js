"use strict";
let latestDiagnostics = null;
function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob),
    link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function applyAppearance() {
  const dark =
    preferences.theme === "dark" ||
    (preferences.theme !== "light" &&
      matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.style.fontSize = (preferences.fontSize || 14) + "px";
  $("theme").value = preferences.theme || "system";
  $("font-size").value = String(preferences.fontSize || 14);
}
applyAppearance();
matchMedia("(prefers-color-scheme: dark)").addEventListener(
  "change",
  applyAppearance,
);
for (const id of ["theme", "font-size"])
  on(id, "change", () => {
    preferences.theme = $("theme").value;
    preferences.fontSize = Number($("font-size").value);
    saveLocal("preferences", preferences);
    applyAppearance();
  });
const endpointLabel = document.querySelector('label[for="endpoint"]');
$("advanced-settings")
  .querySelector("summary")
  .after(endpointLabel, $("endpoint"));
$("advanced-settings").append($("models-refresh").parentElement, $("model"));
on("response-preset", "change", () => {
  const preset = $("response-preset").value;
  if (preset === "quick") {
    $("thinking").checked = false;
    $("max-tokens").value = 512;
  }
  if (preset === "careful") {
    $("thinking").checked = true;
    $("max-tokens").value = Math.min(
      3072,
      Number($("context-length").value) / 2 - 128,
    );
  }
  if (preset === "standard") {
    $("thinking").checked = state.settings.thinking;
    $("max-tokens").value = state.settings.max_tokens;
  }
});
on("project-pick", "click", async () => {
  const result = await api("/api/pick", { kind: "folder" });
  if (result.path) await loadProject(result.path);
});
$("project-pane").addEventListener("dragover", (event) =>
  event.preventDefault(),
);
$("project-pane").addEventListener("drop", (event) => {
  event.preventDefault();
  if (!state.desktop)
    toast("ブラウザ版ではフォルダーの絶対パスを入力してください。");
});
window.addEventListener("local-desk-drop", async (event) => {
  const paths = event.detail;
  if (!Array.isArray(paths) || !paths.length) return;
  const path = paths[0];
  try {
    await loadProject(path);
  } catch {
    const slash = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
    if (slash < 0) return;
    await loadProject(
      path.slice(0, slash),
      paths.map((value) => value.slice(slash + 1).replaceAll("\\", "/")),
    );
  }
});
async function renderDiagnostics() {
  const box = $("diagnostics-result");
  box.replaceChildren(node("p", "muted", "接続とGPUを確認しています…"));
  latestDiagnostics = await api("/api/diagnostics");
  const d = latestDiagnostics;
  $("app-version").textContent =
    "Local Desk " + d.version + " · Python " + d.python;
  box.replaceChildren();
  const list = node("dl", "diagnostic-list");
  const mib = (bytes) => (bytes / 1024 / 1024).toFixed(1) + " MB";
  const entries = [
    ["モデル", d.model],
    ["読込枠", d.context_length.toLocaleString() + " トークン"],
    [
      "接続",
      d.connection.connected ? "接続中" : "未接続：設定でモデルを選び直せます",
    ],
    [
      "GPU",
      d.gpu.available
        ? `${d.gpu.name} · ${d.gpu.used_mib}/${d.gpu.total_mib} MiB · driver ${d.gpu.driver}`
        : "取得できませんでした",
    ],
    ["保存データ", mib(d.storage.data_bytes)],
    ["取り消し用コピー", mib(d.storage.undo_bytes) + "（自動削除しません）"],
    ["導入アーカイブ", mib(d.storage.archives_bytes)],
    ["空き容量", mib(d.storage.free_bytes)],
  ];
  if (d.performance.length) {
    const p = d.performance[0];
    entries.push([
      "直近の生成",
      `${p.tokens_per_second} tokens/s（準備・思考を含む全時間） · 最初の出力 ${p.first_token_seconds ?? "未計測"}秒`,
    ]);
  }
  for (const [label, value] of entries)
    list.append(node("dt", "", label), node("dd", "", String(value)));
  box.append(list);
}
on("maintenance-open", "click", async () => {
  $("maintenance-dialog").showModal();
  await renderDiagnostics();
});
on("maintenance-close", "click", () => $("maintenance-dialog").close());
on("diagnostics-refresh", "click", renderDiagnostics);
on("diagnostics-export", "click", async () => {
  if (state.desktop) {
    const result = await api("/api/export-diagnostics", {});
    if (result.saved) toast("診断を保存しました：" + result.path);
    return;
  }
  if (!latestDiagnostics) await renderDiagnostics();
  downloadBlob(
    new Blob([JSON.stringify(latestDiagnostics, null, 2)], {
      type: "application/json",
    }),
    "local-desk-diagnostics.json",
  );
});
on("runtime-verify", "click", async () => {
  $("maintenance-status").textContent =
    "モデルと実行ファイルのSHA-256を検証しています。数十秒かかる場合があります。";
  const result = await api("/api/verify-runtime", {});
  $("maintenance-status").textContent = result.ok
    ? `${result.files.length}ファイルの一致を確認しました。${result.release} · ${result.revision}`
    : "検証に失敗しました：" +
      result.files
        .filter((file) => !file.ok)
        .map((file) => file.name)
        .join(", ");
});
on("backup-create", "click", async () => {
  $("maintenance-status").textContent = "バックアップを作成しています…";
  const result = await api("/api/backup", {});
  const response = await fetch(
    "/api/backup-download?name=" + encodeURIComponent(result.name),
    { headers: { "X-App-Token": state.token } },
  );
  if (!response.ok) throw new Error("バックアップの取得に失敗しました。");
  downloadBlob(await response.blob(), result.name);
  $("maintenance-status").textContent = result.warning;
});
on("restore-pick", "click", async () => {
  const result = await api("/api/pick", { kind: "file" });
  if (result.path) $("restore-path").value = result.path;
});
on("backup-restore", "click", async () => {
  const path = $("restore-path").value.trim();
  if (!path) return toast("復元するバックアップを選択してください。");
  if (
    !(await confirmAction(
      "現在のデータを退避して、このバックアップの会話・設定・変更履歴に復元します。続けますか？",
    ))
  )
    return;
  const result = await api("/api/restore", { path });
  $("maintenance-status").textContent =
    result.message + " 復元前の退避：" + result.retained;
  await refreshConversations();
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    $("conversation-search").focus();
  }
  if (
    (event.ctrlKey || event.metaKey) &&
    event.shiftKey &&
    event.key.toLowerCase() === "o"
  ) {
    event.preventDefault();
    $("settings-open").click();
  }
});

let scopeResolver = null,
  previewBody = null,
  previewScope = null;
const scopeOptions = { history_limit: 30, ranges: {} };
function chatBody(prompt) {
  return {
    conversation: state.conversation,
    prompt,
    mode: state.mode,
    root: state.project?.root || "",
    files: [...state.selected],
    new_paths: [...state.newPaths],
    history_limit: scopeOptions.history_limit,
    ranges: scopeOptions.ranges,
    compact: !!state.compactNext,
    task_preset: $("task-preset").value,
  };
}
function renderScope(scope, makeRanges = false) {
  previewScope = scope;
  const exact = scope.measurement === "loaded-model";
  $("scope-info").textContent =
    `${exact ? "読込中モデルで計測" : "UTF-8バイト数による保守的な上限"}：入力 ${scope.input_tokens.toLocaleString()} / ${scope.budget.toLocaleString()}。出力用に${scope.output_reserved.toLocaleString()}を確保。過去のメッセージ ${scope.history_used}/${scope.history_total}。質問 ${scope.prompt_included}/${scope.prompt_characters}文字。`;
  $("scope-sources").replaceChildren();
  for (const source of scope.sources) {
    $("scope-sources").append(
      node(
        "p",
        source.clipped ? "warning" : "muted",
        `${source.path}：${source.create ? "承認した新規作成先" : source.included_end ? "L" + source.start + "〜L" + source.included_end : "送信されません"}${source.clipped ? "（末尾を省略）" : ""}`,
      ),
    );
  }
  $("scope-content").textContent = scope.messages
    .map((message) => `【${message.role}】\n${message.content}`)
    .join("\n\n");
  if (makeRanges) {
    $("scope-ranges").replaceChildren();
    $("history-limit").value = scopeOptions.history_limit;
    for (const source of scope.sources) {
      const row = node("div", "scope-range");
      row.append(node("span", "", source.path));
      for (const [key, label] of [
        ["start", "開始行"],
        ["end", "終了行"],
      ]) {
        const input = node("input");
        input.type = "number";
        input.min = "1";
        input.max = String(source.total_lines || 1);
        input.value = source[key];
        input.dataset.path = source.path;
        input.dataset.bound = key;
        input.setAttribute("aria-label", source.path + " " + label);
        input.addEventListener("change", () => {
          scopeOptions.ranges[source.path] ||= {
            start: source.start,
            end: source.end,
          };
          scopeOptions.ranges[source.path][key] = Number(input.value);
          $("scope-send").disabled = true;
        });
        row.append(input);
      }
      $("scope-ranges").append(row);
    }
  }
  $("scope-send").disabled = false;
}
async function prepareUserRequest(prompt, force) {
  previewBody = chatBody(prompt);
  const scope = await api("/api/preview", previewBody);
  if (force || scope.clipped || scope.sources.length || state.compactNext) {
    renderScope(scope, true);
    $("scope-dialog").showModal();
    const approved = await new Promise((resolve) => {
      scopeResolver = resolve;
    });
    if (!approved) {
      state.compactNext = false;
      return null;
    }
  } else previewScope = scope;
  const body = { ...previewBody, preview_digest: previewScope.digest };
  state.compactNext = false;
  return body;
}
on("history-limit", "change", () => {
  scopeOptions.history_limit = Number($("history-limit").value);
  $("scope-send").disabled = true;
});
on("scope-refresh", "click", async () => {
  scopeOptions.history_limit = Number($("history-limit").value);
  for (const input of $("scope-ranges").querySelectorAll("input")) {
    scopeOptions.ranges[input.dataset.path] ||= {};
    scopeOptions.ranges[input.dataset.path][input.dataset.bound] = Number(
      input.value,
    );
  }
  $("scope-send").disabled = true;
  previewBody = chatBody(previewBody.prompt);
  renderScope(await api("/api/preview", previewBody));
});
on("scope-send", "click", () => {
  const resolve = scopeResolver;
  scopeResolver = null;
  $("scope-dialog").close();
  resolve?.(true);
});
$("scope-dialog").addEventListener("close", () => {
  scopeResolver?.(false);
  scopeResolver = null;
});
on("scope-close", "click", () => $("scope-dialog").close());
on("preview-open", "click", (event) => send(event, true));
on("compact-chat", "click", (event) => {
  if (state.busy || !state.messages.length)
    return toast("要約する会話がありません。");
  state.compactNext = true;
  setMode("summary");
  $("prompt").value =
    "これまでの会話を、決定事項・未解決の課題・重要な事実に分けて要約してください。新しい事実を加えないでください。元の履歴は保存したまま、この要約を使って会話を続けます。";
  saveDraft();
  resizePrompt();
  updateSendButton();
  send(event, true);
});
async function conversationAction(action, message) {
  if (state.busy) return toast("生成が終わってから操作してください。");
  if (action === "continue") {
    $("prompt").value = "直前の回答の続きを、重複を避けて書いてください。";
    setMode(
      message.metadata.mode === "edit"
        ? "chat"
        : message.metadata.mode || "chat",
    );
    saveDraft();
    resizePrompt();
    updateSendButton();
    return $("composer").requestSubmit();
  }
  const original = state.messages.slice();
  const index = original.findIndex((item) => item.id === message.id);
  const questionIndex = original
    .slice(0, index)
    .findLastIndex((item) => item.role === "user");
  if (action === "regenerate" && questionIndex < 0)
    return toast("元の質問が見つかりません。");
  const through =
    action === "regenerate"
      ? original[questionIndex - 1]?.id || "__start__"
      : message.id;
  const result = await api("/api/fork", { id: state.conversation, through });
  await openConversation(result.id);
  if (action === "regenerate") {
    const question = original[questionIndex];
    if (question.metadata.root)
      await loadProject(question.metadata.root, question.metadata.files || []);
    else {
      state.selected.clear();
      renderAttachments();
      renderFiles();
    }
    if (question.metadata.new_paths?.length)
      state.newPaths = new Set(question.metadata.new_paths);
    if (!(await reuseQuestion(question))) return;
    $("composer").requestSubmit();
  } else toast("元の会話を残して分岐しました。");
}
on("pin-chat", "click", async () => {
  const current = state.conversations.find(
    (item) => item.id === state.conversation,
  );
  if (!current) return;
  await api("/api/organize", { id: current.id, pinned: !current.pinned });
  await refreshConversations();
});
on("organize-close", "click", () => $("organize-dialog").close());
on("tag-chat", "click", () => {
  const current = state.conversations.find(
    (item) => item.id === state.conversation,
  );
  if (!current) return;
  $("organize-title").textContent = "会話のタグ";
  const input = node("input"),
    save = node("button", "secondary-button", "保存する");
  input.value = JSON.parse(current.tags || "[]").join(", ");
  input.setAttribute("aria-label", "タグ（カンマ区切り）");
  save.addEventListener("click", async () => {
    await api("/api/organize", {
      id: current.id,
      tags: input.value
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
    });
    $("organize-dialog").close();
    await refreshConversations();
  });
  $("organize-content").replaceChildren(
    node(
      "p",
      "muted",
      "タグはカンマで区切ります。履歴検索でも見つけられます。",
    ),
    input,
    save,
  );
  $("organize-dialog").showModal();
});
on("trash-open", "click", async () => {
  $("organize-title").textContent = "ゴミ箱";
  $("organize-content").replaceChildren();
  const items = await api("/api/conversations?trash=1");
  if (!items.length)
    $("organize-content").append(node("p", "muted", "ゴミ箱は空です。"));
  for (const item of items) {
    const row = node("div", "change-row"),
      button = node("button", "secondary-button", "復元");
    row.append(node("span", "", item.title), button);
    button.addEventListener("click", async () => {
      await api("/api/organize", { id: item.id, restore: true });
      row.remove();
      await refreshConversations();
      toast("会話を復元しました。");
    });
    $("organize-content").append(row);
  }
  $("organize-dialog").showModal();
});

let editingTemplate = "";
function editTemplate(item = { name: "", mode: "chat", prompt: "{input}" }) {
  editingTemplate = item.id || "";
  $("template-form").hidden = false;
  $("template-name").value = item.name;
  $("template-mode").value = item.mode;
  $("template-prompt").value = item.prompt;
  $("template-name").focus();
}
async function renderTemplates() {
  const items = await api("/api/templates");
  $("templates-list").replaceChildren();
  for (const item of items) {
    const card = node("div", "template-card"),
      use = node("button", "secondary-button", "使う"),
      edit = node("button", "text-button", "編集"),
      remove = node("button", "text-button", "削除");
    const title = node("strong", "", item.name),
      actions = node("div", "profile-row");
    actions.append(use, edit, remove);
    card.append(title, node("p", "muted", item.prompt.slice(0, 140)), actions);
    use.addEventListener("click", () => {
      if (state.busy) return toast("生成が終わってから使ってください。");
      const text = item.prompt.includes("{input}")
        ? item.prompt.replaceAll("{input}", $("prompt").value)
        : item.prompt + "\n\n" + $("prompt").value;
      if (text.length > 20000)
        return toast("テンプレートと文章の合計が20,000文字を超えています。");
      $("prompt").value = text;
      setMode(item.mode);
      saveDraft();
      resizePrompt();
      updateSendButton();
      $("templates-dialog").close();
      $("prompt").focus();
    });
    edit.addEventListener("click", () => editTemplate(item));
    remove.addEventListener("click", async () => {
      if (
        !(await confirmAction(
          item.name +
            "を削除しますか？直前の保存はバックアップファイルに残ります。",
        ))
      )
        return;
      await api("/api/template", { id: item.id, delete: true });
      await renderTemplates();
    });
    $("templates-list").append(card);
  }
}
on("templates-open", "click", async () => {
  $("template-form").hidden = true;
  $("templates-dialog").showModal();
  await renderTemplates();
});
on("templates-close", "click", () => $("templates-dialog").close());
on("template-new", "click", () => editTemplate());
on("template-form", "submit", async (event) => {
  event.preventDefault();
  await api("/api/template", {
    id: editingTemplate,
    name: $("template-name").value,
    mode: $("template-mode").value,
    prompt: $("template-prompt").value,
  });
  $("template-form").hidden = true;
  await renderTemplates();
  toast("テンプレートを保存しました。");
});
on("clipboard-paste", "click", async () => {
  if (state.busy) return;
  const text = state.desktop
    ? (await api("/api/clipboard", {})).text
    : await navigator.clipboard.readText();
  if (!text) return toast("クリップボードに文字がありません。");
  if (text.length > 20000)
    return toast("20,000文字以下の範囲を選んで貼り付けてください。");
  if (
    $("prompt").value &&
    !(await confirmAction(
      "入力中の下書きをクリップボードの文章に置き換えますか？",
    ))
  )
    return;
  $("prompt").value = text;
  saveDraft();
  resizePrompt();
  updateSendButton();
  $("prompt").focus();
});

on("new-file-add", "click", async () => {
  if (!state.project)
    return toast("先に作成先のプロジェクトを選んでください。");
  if (state.newPaths.size + state.selected.size >= 6)
    return toast("参照先と作成先は合わせて6ファイルまでです。");
  const result = await api("/api/new-path", {
    root: state.project.root,
    path: $("new-file-path").value.trim(),
  });
  state.newPaths.add(result.path);
  $("new-file-path").value = "";
  renderAttachments();
  setMode("edit");
  toast("作成先を追加しました。内容をプレビューしてから反映できます。");
});
async function showLineDiff(message) {
  const diffs = await api("/api/diff", { message: message.id });
  $("organize-title").textContent = "行差分と一括反映";
  const box = $("organize-content"),
    selections = new Map();
  box.replaceChildren();
  box.append(
    node(
      "p",
      "field-hint",
      "選択した変更をまとめて検証します。反映後は同じ組の全ファイルをまとめて取り消します。PythonとJSONは構文も確認します。",
    ),
  );
  for (const diff of diffs) {
    const card = node("section", "template-card");
    const edit = message.metadata.edits[diff.index],
      applied = edit.change_id && !edit.undone;
    card.append(
      node("h3", "", diff.path + (diff.create ? "（新規作成）" : "")),
    );
    const chosen = new Set();
    selections.set(diff.index, chosen);
    for (const hunk of diff.hunks) {
      const label = node("label", "hunk-label"),
        check = node("input");
      check.type = "checkbox";
      check.checked = !applied;
      check.disabled = !!applied;
      if (!applied) chosen.add(hunk.id);
      check.addEventListener("change", () =>
        check.checked ? chosen.add(hunk.id) : chosen.delete(hunk.id),
      );
      label.append(
        check,
        document.createTextNode(
          `変更箇所 ${hunk.id + 1}：前 L${hunk.old_start} / 後 L${hunk.new_start}${applied ? "（反映済み）" : ""}`,
        ),
      );
      const pre = node("pre", "line-diff");
      for (const line of hunk.lines)
        pre.append(
          node(
            "div",
            line.startsWith("+")
              ? "diff-add"
              : line.startsWith("-")
                ? "diff-remove"
                : "",
            line || " ",
          ),
        );
      card.append(label, pre);
    }
    if (edit.applied_hunks)
      card.append(
        node(
          "p",
          "muted",
          "反映した箇所: " +
            edit.applied_hunks.map((value) => value + 1).join(", "),
        ),
      );
    box.append(card);
  }
  const apply = node("button", "send-button", "選んだ変更をまとめて反映"),
    status = node("p", "field-hint");
  box.append(apply, status);
  apply.addEventListener("click", async () => {
    const hunks = {},
      indices = [];
    for (const [index, chosen] of selections)
      if (chosen.size) {
        indices.push(index);
        hunks[index] = [...chosen];
      }
    if (!indices.length) return toast("反映する変更箇所を選んでください。");
    apply.disabled = true;
    try {
      const result = await api("/api/apply-batch", {
        message: message.id,
        indices,
        hunks,
      });
      status.textContent =
        result.message +
        " " +
        result.changes.map((item) => item.check).join(" ");
      if (state.project)
        await loadProject(state.project.root, [
          ...state.selected,
          ...state.newPaths,
        ]);
      state.messages = await api("/api/messages?id=" + state.conversation);
      renderMessages();
      const undo = node("button", "secondary-button", "この組の変更を取り消す");
      box.append(undo);
      undo.addEventListener("click", async () => {
        const restored = await api("/api/undo", { id: result.change_id });
        status.textContent = restored.message;
        undo.disabled = true;
        state.messages = await api("/api/messages?id=" + state.conversation);
        renderMessages();
      });
    } catch (error) {
      toast(error.message);
      apply.disabled = false;
    }
  });
  $("organize-dialog").showModal();
}

function selectReference(path, start, end) {
  if (
    !state.selected.has(path) &&
    state.selected.size + state.newPaths.size >= 6
  )
    return toast("参照は6ファイルまでです。先に不要な選択を外してください。");
  state.selected.add(path);
  if (start && end) scopeOptions.ranges[path] = { start, end };
  renderAttachments();
  renderFiles();
  saveWorkspace();
  toast(path + "を参照に追加しました。");
}
on("project-search", "click", async () => {
  if (!state.project) return toast("先にプロジェクトを選んでください。");
  const query =
    $("project-search-query").value.trim() || $("prompt").value.slice(0, 500);
  const result = await api("/api/project-search", {
    root: state.project.root,
    query,
  });
  $("organize-title").textContent = "プロジェクト内の検索結果";
  const box = $("organize-content");
  box.replaceChildren(
    node(
      "p",
      "field-hint",
      result.truncated
        ? "検索の上限に達しました。結果から参照する範囲を選んでください。"
        : "選んだ箇所だけを質問の参照に追加します。送信前に原文を確認できます。",
    ),
  );
  if (!result.results.length)
    box.append(
      node(
        "p",
        "muted",
        "関連する箇所が見つかりませんでした。別の語句で試してください。",
      ),
    );
  for (const item of result.results) {
    const card = node("div", "template-card"),
      choose = node("button", "secondary-button", "この行を参照に追加");
    card.append(
      node("strong", "", `${item.path}:L${item.start}-L${item.end}`),
      node("pre", "search-excerpt", item.text),
      choose,
    );
    choose.addEventListener("click", () =>
      selectReference(item.path, item.start, item.end),
    );
    box.append(card);
  }
  $("organize-dialog").showModal();
});
on("project-overview", "click", async () => {
  if (!state.project) return toast("先にプロジェクトを選んでください。");
  const result = await api("/api/project-overview", {
    root: state.project.root,
  });
  $("organize-title").textContent = "プロジェクトの概要";
  const box = $("organize-content");
  box.replaceChildren(
    node(
      "p",
      "muted",
      `${result.files}個の参照可能なファイル${result.truncated ? "（一覧の上限まで）" : ""}。Pythonの関数・クラスは実行せずに構文から読み取っています。`,
    ),
  );
  box.append(node("h3", "", "最初に読む候補"));
  for (const path of result.suggested) {
    const button = node("button", "secondary-button", path);
    button.addEventListener("click", () => selectReference(path));
    box.append(button);
  }
  for (const item of result.python) {
    const card = node("div", "template-card");
    card.append(node("strong", "", item.path));
    for (const definition of item.definitions) {
      const button = node(
        "button",
        "text-button",
        `${definition.kind} ${definition.name} · L${definition.line}`,
      );
      button.addEventListener("click", () => selectReference(item.path));
      card.append(button);
    }
    if (item.imports.length)
      card.append(node("p", "muted", "import: " + item.imports.join(", ")));
    box.append(card);
  }
  $("organize-dialog").showModal();
});
on("project-tests-open", "click", () => {
  if (!state.project) return toast("先にプロジェクトを選んでください。");
  const root = state.project.root,
    box = $("organize-content");
  $("organize-title").textContent = "プロジェクトのテスト";
  box.replaceChildren(
    node("p", "", root),
    node(
      "p",
      "warning",
      "このプロジェクトのPythonコードを実行します。信頼するプロジェクトで使ってください。実行は45秒で停止し、結果の末尾64KBを表示します。",
    ),
  );
  const select = node("select");
  select.setAttribute("aria-label", "テストの種類");
  select.append(
    new Option("python -m unittest discover -s tests -v", "unittest"),
    new Option("python -m pytest -q（pytestの導入が必要）", "pytest"),
  );
  const button = node("button", "send-button", "表示したコマンドを実行"),
    result = node("pre", "search-excerpt");
  box.append(select, button, result);
  button.addEventListener("click", async () => {
    if (
      !(await confirmAction(
        root +
          "で " +
          select.selectedOptions[0].textContent +
          " を実行しますか？",
      ))
    )
      return;
    button.disabled = true;
    result.textContent = "実行中です…";
    try {
      const report = await api("/api/project-tests", {
        root,
        kind: select.value,
        confirmed: true,
      });
      result.textContent = `${report.command}\n終了コード ${report.exit_code} · ${report.seconds}秒${report.timed_out ? "（時間制限で停止）" : ""}\n\n${report.output}`;
    } catch (error) {
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  $("organize-dialog").showModal();
});
