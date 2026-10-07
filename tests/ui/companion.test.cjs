const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");
const web = path.resolve(__dirname, "../../web");
test("companion exposes actual status and never claims unrun tests", async (t) => {
  const dom = new JSDOM(
    fs.readFileSync(path.join(web, "companion.html"), "utf8"),
    { url: "http://127.0.0.1:8765/companion.html", runScripts: "outside-only" },
  );
  t.after(() => dom.window.close());
  const w = dom.window,
    calls = [];
  w.fetch = async (url, options = {}) => {
    calls.push({ url, body: options.body && JSON.parse(options.body) });
    return {
      ok: true,
      json: async () =>
        url === "/api/bootstrap"
          ? { token: "fixture" }
          : url === "/api/desktop"
            ? { expanded: JSON.parse(options.body).action === "expand" }
            : {
                stage: "考えています",
                model: "fixture",
                busy: true,
                cancellable: true,
                last: {
                  summary: "<script>evil</script>",
                  files: ["file.py"],
                  edits: 2,
                },
              },
    };
  };
  const context = dom.getInternalVMContext();
  vm.runInContext(
    fs.readFileSync(path.join(web, "companion.js"), "utf8"),
    context,
  );
  await new Promise((r) => setImmediate(r));
  assert.equal(w.document.querySelector("#stage").textContent, "考えています");
  assert.equal(w.document.querySelector("#cancel").hidden, false);
  assert.equal(w.document.querySelector("#summary script"), null);
  assert.match(
    w.document.querySelector("#checks").textContent,
    /未適用.*実行されていません/,
  );
  w.document.querySelector("#toggle").click();
  await new Promise((r) => setImmediate(r));
  assert.equal(w.document.querySelector("#details").hidden, false);
  w.document.querySelector("#cancel").click();
  await new Promise((r) => setImmediate(r));
  assert.equal(
    calls.find((c) => c.url === "/api/cancel").body &&
      Object.keys(calls.find((c) => c.url === "/api/cancel").body).length,
    0,
  );
  vm.runInContext(
    "renderActivity({stage:'待機中',model:'fixture',last:{verification:'テスト失敗',command:'python -m unittest'}})",
    context,
  );
  assert.match(w.document.querySelector("#checks").textContent, /テスト失敗/);
});
