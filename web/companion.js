"use strict";
let token = "",
  expanded = false,
  stopped = false;
const el = (id) => document.getElementById(id);
async function request(path, body) {
  const r = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: {
      "X-App-Token": token,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const value = await r.json();
  if (!r.ok) throw new Error(value.error || "接続できません");
  return value;
}
async function control(action) {
  try {
    if (action === "quit") stopped = true;
    const result = await request("/api/desktop", { action });
    expanded = result.expanded;
    el("details").hidden = !expanded;
    el("toggle").setAttribute("aria-expanded", String(expanded));
  } catch (error) {
    stopped = false;
    el("error").textContent = error.message;
  }
}
function renderActivity(data) {
  el("stage").textContent = data.stage;
  document.body.classList.toggle("busy", data.busy);
  el("model").textContent = data.model;
  el("elapsed").textContent =
    data.seconds == null ? "" : `${data.seconds.toFixed(1)} 秒`;
  el("cancel").hidden = !data.cancellable;
  const last = data.last || {};
  if (last.summary) el("summary").textContent = last.summary;
  el("files").replaceChildren(
    ...(last.files || []).map((path) => {
      const li = document.createElement("li");
      li.textContent = path;
      return li;
    }),
  );
  el("checks").textContent = last.verification
    ? `${last.verification} · ${last.command || ""}`
    : last.edits
      ? `修正案 ${last.edits} 件・未適用。テストは実行されていません。`
      : "この回答に対するテストは実行されていません。";
  el("error").textContent =
    last.error || (last.cancelled ? "生成を停止しました。" : "");
}
async function poll() {
  if (stopped) return;
  try {
    renderActivity(await request("/api/activity"));
  } catch (error) {
    el("stage").textContent = "接続が切れました";
    el("error").textContent = error.message;
  }
  if (!stopped) setTimeout(poll, 1000);
}
el("toggle").onclick = () => control(expanded ? "collapse" : "expand");
el("collapse").onclick = () => control("collapse");
el("normal").onclick = () => control("normal");
el("chat").onclick = () => control("conversation");
el("quit").onclick = () => control("quit");
el("cancel").onclick = async () => {
  try {
    await request("/api/cancel", {});
  } catch (error) {
    el("error").textContent = error.message;
  }
};
request("/api/bootstrap")
  .then((boot) => {
    token = boot.token;
    return poll();
  })
  .catch((error) => {
    el("stage").textContent = error.message;
  });
