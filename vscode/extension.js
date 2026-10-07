"use strict";
const vscode = require("vscode"),
  path = require("node:path"),
  crypto = require("node:crypto");
const { Client } = require("./client");
const previews = new Map();
let lastChange = "",
  inflight = false;
function config() {
  return vscode.workspace.getConfiguration("localDesk");
}
async function client() {
  const value = new Client(config().get("port", 8765));
  await value.connect();
  return value;
}
function editor() {
  const value = vscode.window.activeTextEditor;
  if (!value || value.document.uri.scheme !== "file")
    throw Error("ローカルファイルを開いてください。");
  if (!vscode.workspace.isTrusted)
    throw Error("信頼したプロジェクトで使用してください。");
  return value;
}
async function chat(api, body, title) {
  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title,
      cancellable: true,
    },
    async (progress, cancellation) => {
      const id = crypto.randomUUID(),
        controller = new AbortController();
      const listener = cancellation.onCancellationRequested(() => {
        api.request("/api/cancel", { request_id: id }).catch(() => {});
        controller.abort();
      });
      try {
        return await api.request(
          "/api/chat",
          { ...body, request_id: id },
          controller.signal,
          (event, value) => {
            if (event === "error") throw Error(value.error);
            if (event === "delta")
              progress.report({
                message: value.reasoning ? "思考中…" : "回答中…",
              });
          },
        );
      } finally {
        listener.dispose();
      }
    },
  );
}
async function explain() {
  const active = editor(),
    text = active.document.getText(active.selection);
  if (!text.trim()) throw Error("説明するコードを選択してください。");
  if (text.length > 20000)
    throw Error("選択範囲を20,000文字以下にしてください。");
  const api = await client(),
    conversation = (await api.request("/api/conversations", {})).id;
  const answer = await chat(
    api,
    {
      conversation,
      mode: "code",
      prompt: `このコードの動作・入力・注意点を日本語で説明してください。\n${active.document.fileName}:${active.selection.start.line + 1}\n\n${text}`,
    },
    "Local Desk: コードを説明",
  );
  const document = await vscode.workspace.openTextDocument({
    language: "markdown",
    content: answer.content,
  });
  await vscode.window.showTextDocument(document, vscode.ViewColumn.Beside);
}
async function edit() {
  const active = editor();
  if (active.document.isDirty)
    throw Error(
      "差分を正しく検証するため、ファイルを保存してから相談してください。",
    );
  const version = active.document.version;
  const folder = vscode.workspace.getWorkspaceFolder(active.document.uri);
  if (!folder) throw Error("ファイルが入ったフォルダーを開いてください。");
  const prompt = await vscode.window.showInputBox({
    prompt: "どのように修正しますか？ 内容と差分を確認してから反映します。",
  });
  if (!prompt) return;
  const api = await client(),
    conversation = (await api.request("/api/conversations", {})).id;
  const file = path
    .relative(folder.uri.fsPath, active.document.uri.fsPath)
    .replaceAll("\\", "/");
  const body = {
    conversation,
    mode: "edit",
    root: folder.uri.fsPath,
    files: [file],
    prompt,
  };
  const scope = await api.request("/api/preview", body);
  const confirmed = await vscode.window.showWarningMessage(
    `${file}の${scope.sources[0].start}〜${scope.sources[0].included_end}行、入力${scope.input_tokens}トークンをこのPCのAIへ渡します。${scope.clipped ? "一部が省略されています。" : ""}`,
    { modal: true },
    "送信する",
  );
  if (confirmed !== "送信する") return;
  const answer = await chat(
    api,
    { ...body, preview_digest: scope.digest },
    "Local Desk: 修正案を作成",
  );
  if (active.document.version !== version)
    throw Error(
      "生成中にファイルが変更されました。保存してから相談し直してください。",
    );
  const edits = answer.metadata?.edits || [];
  if (!edits.length)
    throw Error(
      answer.metadata?.proposal_warning ||
        "反映できる修正案がありません。Local Deskで回答を確認してください。",
    );
  const original = active.document.getText();
  let proposed = original;
  const operations = edits.map((edit, index) => ({
    edit,
    index,
    start: original.indexOf(edit.old),
  }));
  if (
    operations.some((item) => item.start < 0) ||
    operations.some((a, i) =>
      operations.some(
        (b, j) =>
          i < j &&
          a.start < b.start + b.edit.old.length &&
          b.start < a.start + a.edit.old.length,
      ),
    )
  )
    throw Error(
      "修正箇所が重複しています。Local Deskで修正案を確認してください。",
    );
  for (const item of operations.sort((a, b) => b.start - a.start))
    proposed =
      proposed.slice(0, item.start) +
      item.edit.new +
      proposed.slice(item.start + item.edit.old.length);
  const uri = vscode.Uri.parse(
    "localdesk-preview:/" + crypto.randomUUID() + "/" + path.basename(file),
  );
  previews.set(uri.toString(), proposed);
  while (previews.size > 20) previews.delete(previews.keys().next().value);
  await vscode.commands.executeCommand(
    "vscode.diff",
    active.document.uri,
    uri,
    "Local Desk: " + file + " の修正案",
  );
  const apply = await vscode.window.showWarningMessage(
    "差分を確認してください。反映するとバックアップを保存し、まとめて取り消せます。",
    { modal: true },
    "すべて反映",
  );
  if (apply !== "すべて反映") return;
  if (active.document.isDirty)
    throw Error(
      "エディター側で変更されています。先に保存し、修正を相談し直してください。",
    );
  lastChange = (
    await api.request("/api/apply-batch", {
      message: answer.id,
      indices: edits.map((_, index) => index),
    })
  ).change_id;
  vscode.window.showInformationMessage(
    "修正を反映しました。Local Deskの変更履歴からも取り消せます。",
  );
}
async function completion(document, position, token) {
  if (!vscode.workspace.isTrusted || document.uri.scheme !== "file" || inflight)
    return [];
  inflight = true;
  const controller = new AbortController();
  let api, timer;
  const id = crypto.randomUUID();
  const cancel = () => {
    controller.abort();
    if (api) api.request("/api/cancel", { request_id: id }).catch(() => {});
  };
  const listener = token.onCancellationRequested(cancel);
  try {
    api = await client();
    if (token.isCancellationRequested) return [];
    timer = setTimeout(cancel, config().get("completionTimeoutMs", 4000));
    const text = document.getText(),
      offset = document.offsetAt(position);
    const result = await api.request(
      "/api/complete",
      {
        prefix: text.slice(Math.max(0, offset - 8000), offset),
        suffix: text.slice(offset, offset + 2000),
        language: document.languageId,
        request_id: id,
      },
      controller.signal,
    );
    return result.text
      ? [
          new vscode.InlineCompletionItem(
            result.text,
            new vscode.Range(position, position),
          ),
        ]
      : [];
  } catch {
    return [];
  } finally {
    clearTimeout(timer);
    listener.dispose();
    inflight = false;
  }
}
function activate(context) {
  const register = (name, handler) =>
    context.subscriptions.push(
      vscode.commands.registerCommand(name, async () => {
        try {
          await handler();
        } catch (error) {
          vscode.window.showErrorMessage(error.message);
        }
      }),
    );
  register("localDesk.explain", explain);
  register("localDesk.edit", edit);
  register("localDesk.undo", async () => {
    if (!lastChange)
      throw Error(
        "このエディターから反映した変更がありません。Local Deskの変更履歴を確認してください。",
      );
    const api = await client();
    await api.request("/api/undo", { id: lastChange });
    lastChange = "";
  });
  register("localDesk.complete", async () => {
    const active = editor(),
      source = new vscode.CancellationTokenSource(),
      position = active.selection.active,
      version = active.document.version;
    try {
      const items = await completion(active.document, position, source.token);
      if (active.document.version !== version)
        throw Error(
          "補完待ちの間にファイルが変わりました。もう一度実行してください。",
        );
      if (items.length)
        await active.edit((builder) =>
          builder.insert(position, items[0].insertText),
        );
      else
        vscode.window.showInformationMessage(
          "補完できませんでした。AIの接続・使用中・待ち時間を確認してください。",
        );
    } finally {
      source.dispose();
    }
  });
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider("localdesk-preview", {
      provideTextDocumentContent: (uri) => previews.get(uri.toString()) || "",
    }),
  );
  context.subscriptions.push(
    vscode.languages.registerInlineCompletionItemProvider(
      { scheme: "file" },
      {
        provideInlineCompletionItems: (document, position, ctx, token) =>
          config().get("inlineCompletion", false)
            ? completion(document, position, token)
            : [],
      },
    ),
  );
}
function deactivate() {
  previews.clear();
}
module.exports = { activate, deactivate };
