"use strict";
const http = require("node:http");
class Client {
  constructor(port = 8765) {
    if (!Number.isInteger(port) || port < 1024 || port > 65535)
      throw Error("Port is invalid");
    this.port = port;
    this.token = "";
  }
  async connect() {
    const result = await this.request("/api/bootstrap");
    if (result.application !== "local-desk" || !result.token)
      throw Error("Local Deskをデスクトップから開いてください。");
    this.token = result.token;
    return result;
  }
  request(path, body, signal, onEvent) {
    return new Promise((resolve, reject) => {
      const payload =
        body === undefined ? null : Buffer.from(JSON.stringify(body));
      const headers = { "X-App-Token": this.token };
      if (payload) {
        headers["Content-Type"] = "application/json";
        headers["Content-Length"] = payload.length;
      }
      const request = http.request(
        {
          hostname: "127.0.0.1",
          port: this.port,
          path,
          method: payload ? "POST" : "GET",
          headers,
          signal,
        },
        (response) => {
          response.setEncoding("utf8");
          let raw = "",
            total = 0,
            last;
          response.on("data", (chunk) => {
            total += Buffer.byteLength(chunk, "utf8");
            if (total > 2 * 1024 * 1024) {
              request.destroy(Error("Response too large"));
              return;
            }
            raw += chunk;
            if (onEvent) {
              let split;
              while ((split = raw.indexOf("\n\n")) >= 0) {
                const block = raw.slice(0, split);
                raw = raw.slice(split + 2);
                const name = /^event: (.+)$/m.exec(block)?.[1],
                  data = /^data: (.+)$/m.exec(block)?.[1];
                if (data) {
                  try {
                    const value = JSON.parse(data);
                    onEvent(name, value);
                    if (name === "done") last = value;
                  } catch (error) {
                    request.destroy(error);
                  }
                }
              }
            }
          });
          response.on("error", reject);
          response.on("end", () => {
            try {
              const result =
                onEvent && response.statusCode < 400 ? last : JSON.parse(raw);
              if (response.statusCode >= 400)
                throw Error(result?.error || "Request failed");
              if (onEvent && !last)
                throw Error("AIの応答が完了する前に接続が終了しました。");
              if (response.statusCode >= 400 || result?.error)
                throw Error(result?.error || "Request failed");
              resolve(result);
            } catch (error) {
              reject(error);
            }
          });
        },
      );
      request.on("error", reject);
      request.setTimeout(180000, () =>
        request.destroy(Error("AIの応答が時間内に届きませんでした。")),
      );
      if (payload) request.write(payload);
      request.end();
    });
  }
}
module.exports = { Client };
