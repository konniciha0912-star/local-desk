const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");
const web = path.resolve(__dirname, "../../web");
const clone = (value) => JSON.parse(JSON.stringify(value));
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function until(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await tick();
  }
  throw new Error("UI did not reach the expected state");
}
async function fixture(t, preferences) {
  const dom = new JSDOM(fs.readFileSync(path.join(web, "index.html"), "utf8"), {
    url: "http://localhost:8765/",
    runScripts: "outside-only",
  });
  t.after(async () => {
    for (let i = 0; i < 5; i++) await tick();
    dom.window.close();
  });
  const w = dom.window,
    document = w.document,
    calls = [];
  w.matchMedia = () => ({ matches: false, addEventListener() {} });
  w.TextDecoder = TextDecoder;
  w.AbortController = AbortController;
  w.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  w.HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new w.Event("close"));
  };
  const conversations = [
    {
      id: "original",
      title: "保存した会話",
      tags: "[]",
      pinned: false,
      message_count: 2,
    },
  ];
  const question = {
    id: "q1",
    role: "user",
    content: "元の質問",
    metadata: { mode: "chat" },
  };
  const answer = {
    id: "a1",
    role: "assistant",
    content: "元の回答",
    metadata: { mode: "chat" },
  };
  const messages = { original: [question, answer] };
  let stream,
    pending = [],
    queue = [];
  const settings = {
    model: "fixture-model",
    endpoint: "http://localhost:1234/v1",
    temperature: 0.2,
    max_tokens: 512,
    context_length: 8192,
    system_prompt: "日本語",
    thinking: false,
  };
  if (preferences)
    w.localStorage.setItem(
      "local-desk:preferences",
      JSON.stringify(preferences),
    );
  const result = (data) => ({ ok: true, json: async () => clone(data) });
  w.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ url, body });
    if (url === "/api/bootstrap")
      return result({ token: "fixture", settings, desktop: false });
    if (url === "/api/models")
      return result({
        connected: true,
        models: ["fixture-model"],
        endpoint: settings.endpoint,
      });
    if (url === "/api/profiles") return result([]);
    if (url.startsWith("/api/messages"))
      return result(
        messages[new URL(url, w.location).searchParams.get("id")] || [],
      );
    if (url.startsWith("/api/conversations") && !body)
      return result(
        conversations.filter((c) =>
          url.includes("trash=1") ? c.deleted : !c.deleted,
        ),
      );
    if (url === "/api/conversations" && body) {
      const id = "blank";
      conversations.push({
        id,
        title: "新しい会話",
        tags: "[]",
        message_count: 0,
      });
      messages[id] = [];
      return result({ id });
    }
    if (url === "/api/fork") {
      const id = "branch";
      const source = messages[body.id];
      const through = source.findIndex((m) => m.id === body.through);
      messages[id] =
        body.through === "__start__" ? [] : clone(source.slice(0, through + 1));
      conversations.push({
        id,
        title: "分岐",
        tags: "[]",
        message_count: messages[id].length,
      });
      return result({ id });
    }
    if (url === "/api/organize") {
      const c = conversations.find((c) => c.id === body.id);
      if ("pinned" in body) c.pinned = body.pinned;
      if (body.tags) c.tags = JSON.stringify(body.tags);
      if (body.restore) c.deleted = false;
      return result({ ok: true });
    }
    if (url === "/api/delete") {
      conversations.find((c) => c.id === body.id).deleted = true;
      return result({ ok: true });
    }
    if (url === "/api/project")
      return result({
        root: body.root,
        files: [{ path: "facts.md" }, { path: "other.md" }],
      });
    if (url === "/api/project-search")
      return result({
        results: [{ path: "facts.md", start: 2, end: 3, text: "根拠の原文" }],
        truncated: false,
      });
    if (url === "/api/preview")
      return result({ digest: "fixture-digest", sources: [], clipped: false });
    if (url === "/api/chat") {
      stream = {
        getReader: () => ({
          read: () =>
            queue.length
              ? Promise.resolve(queue.shift())
              : new Promise((resolve) => pending.push(resolve)),
        }),
      };
      return { ok: true, body: stream };
    }
    if (url === "/api/diff")
      return result([
        {
          index: 0,
          path: "calculator.py",
          hunks: [
            {
              id: 0,
              old_start: 2,
              new_start: 2,
              lines: ["- return a-b", "+ return a+b"],
            },
            {
              id: 1,
              old_start: 5,
              new_start: 5,
              lines: ["- return a+b", "+ return a*b"],
            },
          ],
        },
      ]);
    if (url === "/api/apply-batch")
      return result({
        change_id: "group-1",
        message: "反映済み",
        changes: [{ check: "Python構文OK" }],
      });
    if (url === "/api/undo") return result({ message: "復元済み" });
    throw new Error("Unexpected fixture request: " + url);
  };
  const context = dom.getInternalVMContext();
  for (const script of [
    "vendor/marked.js",
    "vendor/prism-core.js",
    "vendor/prism-clike.js",
    "vendor/prism-python.js",
    "markdown.js",
    "app.js",
    "tools.js",
    "extensions.js",
  ])
    vm.runInContext(fs.readFileSync(path.join(web, script), "utf8"), context, {
      filename: script,
    });
  await until(
    () => document.querySelector("#chat-title").textContent === "保存した会話",
  );
  const evaluate = (source) => vm.runInContext(source, context);
  const button = (text, root = document) =>
    [...root.querySelectorAll("button")].find((b) => b.textContent === text);
  const emit = (event, data) => {
    const value = {
      value: new TextEncoder().encode(
        `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
      ),
      done: false,
    };
    if (pending.length) pending.shift()(value);
    else queue.push(value);
  };
  const finish = () => {
    const value = { done: true };
    if (pending.length) pending.shift()(value);
    else queue.push(value);
  };
  return {
    w,
    document,
    calls,
    conversations,
    messages,
    question,
    answer,
    evaluate,
    button,
    emit,
    finish,
  };
}

test("short stream phases update immediately, then controls recover", async (t) => {
  const f = await fixture(t);
  f.document.querySelector("#prompt").value = "質問";
  const sending = f.evaluate("send({preventDefault(){}})");
  await until(() => f.calls.some((c) => c.url === "/api/chat"));
  f.emit("start", { files: [] });
  await until(
    () =>
      f.document.querySelector("#generation-info").textContent ===
      "モデルの応答待ち",
  );
  f.emit("delta", { reasoning: "考える" });
  await until(
    () => f.document.querySelector("#generation-info").textContent === "思考中",
  );
  f.emit("delta", { content: "答え" });
  await until(
    () => f.document.querySelector("#generation-info").textContent === "回答中",
  );
  f.emit("done", {
    id: "answer2",
    content: "答え",
    metadata: { mode: "chat" },
  });
  f.finish();
  await sending;
  assert.equal(f.evaluate("state.busy"), false);
  assert.equal(f.document.querySelector("#stop").hidden, true);
  assert.equal(
    f.document.querySelector(".message:last-child .message-content")
      .textContent,
    "答え",
  );
});

test("appearance persists and shortcuts focus search/open settings", async (t) => {
  const f = await fixture(t);
  const theme = f.document.querySelector("#theme"),
    font = f.document.querySelector("#font-size");
  theme.value = "dark";
  theme.dispatchEvent(new f.w.Event("change"));
  font.value = "16";
  font.dispatchEvent(new f.w.Event("change"));
  const saved = JSON.parse(f.w.localStorage.getItem("local-desk:preferences"));
  const reopened = await fixture(t, saved);
  assert.equal(reopened.document.documentElement.dataset.theme, "dark");
  assert.equal(reopened.document.documentElement.style.fontSize, "16px");
  f.document.dispatchEvent(
    new f.w.KeyboardEvent("keydown", {
      key: "k",
      ctrlKey: true,
      bubbles: true,
    }),
  );
  assert.equal(f.document.activeElement.id, "conversation-search");
  f.document.dispatchEvent(
    new f.w.KeyboardEvent("keydown", {
      key: "O",
      ctrlKey: true,
      shiftKey: true,
      bubbles: true,
    }),
  );
  await until(() => f.document.querySelector("#settings-dialog").open);
});

test("regenerate keeps originals, restores approved new paths, and cancellation keeps draft", async (t) => {
  const f = await fixture(t);
  f.question.metadata = {
    mode: "edit",
    root: "/project",
    files: ["facts.md"],
    new_paths: ["new.py"],
  };
  f.evaluate(`state.messages=${JSON.stringify([f.question, f.answer])};`);
  let submitted = false;
  f.document.querySelector("#composer").requestSubmit = () => {
    submitted = true;
  };
  await f.evaluate('conversationAction("regenerate", state.messages[1])');
  assert.equal(f.messages.original[1].content, "元の回答");
  assert.deepEqual(f.calls.find((c) => c.url === "/api/fork").body, {
    id: "original",
    through: "__start__",
  });
  assert.deepEqual(
    JSON.parse(f.evaluate("JSON.stringify([...state.newPaths])")),
    ["new.py"],
  );
  assert.equal(submitted, true);
  assert.equal(f.document.querySelector("#prompt").value, "元の質問");
  f.document.querySelector("#prompt").value = "残したい下書き";
  const reusing = f.evaluate('reuseQuestion({content:"別の質問",metadata:{}})');
  await until(() => f.document.querySelector(".confirm-dialog"));
  f.button("キャンセル", f.document.querySelector(".confirm-dialog")).click();
  assert.equal(await reusing, undefined);
  assert.equal(f.document.querySelector("#prompt").value, "残したい下書き");
});

test("fork and continue preserve the answer and select correct continuation mode", async (t) => {
  const f = await fixture(t);
  await f.evaluate('conversationAction("fork", state.messages[1])');
  assert.equal(f.messages.original[1].content, "元の回答");
  assert.equal(f.messages.branch[1].content, "元の回答");
  let submitted = false;
  f.document.querySelector("#composer").requestSubmit = () => {
    submitted = true;
  };
  await f.evaluate('conversationAction("continue", {metadata:{mode:"edit"}})');
  assert.equal(submitted, true);
  assert.equal(f.evaluate("state.mode"), "chat");
  assert.match(f.document.querySelector("#prompt").value, /重複を避けて/);
});

test("pin, tags, cancelled delete, trash and restore keep original messages", async (t) => {
  const f = await fixture(t);
  f.document.querySelector("#pin-chat").click();
  await until(() => f.conversations[0].pinned);
  f.document.querySelector("#tag-chat").click();
  f.document.querySelector("#organize-content input").value = "仕事, Python";
  f.button("保存する").click();
  await until(() => f.conversations[0].tags === '["仕事","Python"]');
  f.document.querySelector("#delete-chat").click();
  await until(() => f.document.querySelector(".confirm-dialog"));
  f.button("キャンセル", f.document.querySelector(".confirm-dialog")).click();
  await tick();
  assert.equal(f.conversations[0].deleted, undefined);
  f.document.querySelector("#delete-chat").click();
  await until(() => f.document.querySelector(".confirm-dialog"));
  f.button("続ける", f.document.querySelector(".confirm-dialog")).click();
  await until(() => f.conversations[0].deleted);
  await until(
    () => f.document.querySelector("#chat-title").textContent === "新しい会話",
  );
  f.document.querySelector("#trash-open").click();
  await until(() => f.button("復元"));
  f.button("復元").click();
  await until(() => f.conversations[0].deleted === false);
  assert.equal(f.messages.original[1].content, "元の回答");
});

test("search results become an explicit file/line selection and citations flag missing evidence", async (t) => {
  const f = await fixture(t);
  await f.evaluate('loadProject("/project")');
  f.document.querySelector("#project-search-query").value = "会議";
  f.document.querySelector("#project-search").click();
  await until(() => f.button("この行を参照に追加"));
  f.button("この行を参照に追加").click();
  const body = JSON.parse(f.evaluate('JSON.stringify(chatBody("質問"))'));
  assert.deepEqual(body.files, ["facts.md"]);
  assert.deepEqual(body.ranges, { "facts.md": { start: 2, end: 3 } });
  f.evaluate(
    '$("messages").replaceChildren(messageElement({id:"cite",role:"assistant",content:"答え",metadata:{citations:[{path:"facts.md",start:2,end:3,verified:true,excerpt:"原文"},{path:"other.md",start:99,end:99,verified:false}]}}))',
  );
  assert.match(
    f.document.querySelector("#messages").textContent,
    /facts.md:L2-L3 · 送信した範囲内/,
  );
  assert.match(
    f.document.querySelector(".warning").textContent,
    /参照範囲を確認できません/,
  );
  assert.equal(f.document.querySelector("details pre").textContent, "原文");
});

test("hunk deselection sends only selected changes and grouped undo", async (t) => {
  const f = await fixture(t);
  await f.evaluate(
    'showLineDiff({id:"proposal",metadata:{edits:[{path:"calculator.py"}]}})',
  );
  const checks = f.document.querySelectorAll(".hunk-label input");
  assert.equal(checks.length, 2);
  checks[1].checked = false;
  checks[1].dispatchEvent(new f.w.Event("change"));
  f.button("選んだ変更をまとめて反映").click();
  await until(() => f.button("この組の変更を取り消す"));
  assert.deepEqual(f.calls.find((c) => c.url === "/api/apply-batch").body, {
    message: "proposal",
    indices: [0],
    hunks: { 0: [0] },
  });
  f.button("この組の変更を取り消す").click();
  await until(() => f.calls.some((c) => c.url === "/api/undo"));
  assert.deepEqual(f.calls.find((c) => c.url === "/api/undo").body, {
    id: "group-1",
  });
});
