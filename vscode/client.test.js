"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const { Client } = require("./client");
test("local client authenticates, preserves split UTF-8, handles failures and cancellation", async () => {
  const server = http.createServer((req, res) => {
    if (req.url === "/api/bootstrap")
      return res.end(
        JSON.stringify({ application: "local-desk", token: "fixture" }),
      );
    assert.equal(req.headers["x-app-token"], "fixture");
    if (req.url === "/error") {
      res.writeHead(409);
      return res.end(JSON.stringify({ error: "fixture changed" }));
    }
    if (req.url === "/stall") return;
    res.setHeader("Content-Type", "text/event-stream");
    const data = Buffer.from(
      'event: delta\ndata: {"content":"日本語"}\n\nevent: done\ndata: {"content":"日本語"}\n\n',
    );
    if (req.url === "/incomplete")
      return res.end('event: delta\ndata: {"content":"partial"}\n\n');
    res.write(data.subarray(0, 36));
    setTimeout(() => res.end(data.subarray(36)), 10);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const client = new Client(server.address().port);
    await client.connect();
    const events = [];
    const done = await client.request("/stream", {}, undefined, (name, value) =>
      events.push([name, value]),
    );
    assert.equal(done.content, "日本語");
    assert.equal(events.length, 2);
    await assert.rejects(client.request("/error", {}), /fixture changed/);
    await assert.rejects(
      client.request("/incomplete", {}, undefined, () => {}),
      /完了/,
    );
    const control = new AbortController();
    const pending = client.request("/stall", {}, control.signal);
    control.abort();
    await assert.rejects(pending, { name: "AbortError" });
    assert.throws(() => new Client(80), /invalid/);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
