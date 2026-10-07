"use strict";
// Parse Markdown into a small DOM allowlist. Model HTML is always literal text.
function entityText(text) {
  return String(text || "").replace(
    /&(?:amp|lt|gt|quot|#39|#\d+|#x[\da-f]+);/gi,
    (entity) => {
      const known = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
      };
      if (known[entity]) return known[entity];
      const value = entity.toLowerCase().startsWith("&#x")
        ? parseInt(entity.slice(3, -1), 16)
        : Number(entity.slice(2, -1));
      return value > 0 && value <= 0x10ffff
        ? String.fromCodePoint(value)
        : entity;
    },
  );
}
function safeMarkdownInline(tokens = []) {
  const fragment = document.createDocumentFragment();
  for (const token of tokens) {
    let element;
    if (token.type === "br") element = node("br");
    else if (["strong", "em", "del"].includes(token.type)) {
      element = node(token.type);
      element.append(safeMarkdownInline(token.tokens));
    } else if (token.type === "codespan")
      element = node("code", "", entityText(token.text));
    else if (token.type === "link") {
      try {
        const url = new URL(entityText(token.href));
        if (!["https:", "http:"].includes(url.protocol))
          throw new Error("unsafe link");
        element = node("a");
        element.href = url.href;
        element.target = "_blank";
        element.rel = "noopener noreferrer";
        element.append(safeMarkdownInline(token.tokens));
      } catch {
        element = document.createTextNode(entityText(token.text));
      }
    } else if (token.type === "image")
      element = document.createTextNode(
        "[画像: " + entityText(token.text) + "]",
      );
    else if (token.type === "html")
      element = document.createTextNode(token.raw);
    else if (token.tokens) {
      fragment.append(safeMarkdownInline(token.tokens));
      continue;
    } else
      element = document.createTextNode(entityText(token.text || token.raw));
    fragment.append(element);
  }
  return fragment;
}
function safeMarkdownBlocks(tokens) {
  const fragment = document.createDocumentFragment();
  for (const token of tokens) {
    let element;
    switch (token.type) {
      case "space":
        continue;
      case "heading":
        element = node("h" + Math.min(6, Math.max(2, token.depth + 1)));
        element.append(safeMarkdownInline(token.tokens));
        break;
      case "paragraph":
      case "text":
        element = node("p");
        element.append(
          token.tokens
            ? safeMarkdownInline(token.tokens)
            : document.createTextNode(entityText(token.text)),
        );
        break;
      case "blockquote":
        element = node("blockquote");
        element.append(safeMarkdownBlocks(token.tokens));
        break;
      case "code":
        element = codeBlock(token.text, token.lang || "code");
        break;
      case "hr":
        element = node("hr");
        break;
      case "list":
        element = node(token.ordered ? "ol" : "ul");
        if (token.ordered) element.start = token.start || 1;
        for (const item of token.items) {
          const li = node("li");
          if (item.task)
            li.append(document.createTextNode(item.checked ? "☑ " : "☐ "));
          li.append(safeMarkdownBlocks(item.tokens));
          element.append(li);
        }
        break;
      case "table": {
        element = node("div", "markdown-table");
        const table = node("table"),
          head = node("thead"),
          header = node("tr"),
          body = node("tbody");
        for (const cell of token.header) {
          const th = node("th");
          th.append(safeMarkdownInline(cell.tokens));
          header.append(th);
        }
        head.append(header);
        table.append(head, body);
        for (const row of token.rows) {
          const tr = node("tr");
          for (const cell of row) {
            const td = node("td");
            td.append(safeMarkdownInline(cell.tokens));
            tr.append(td);
          }
          body.append(tr);
        }
        element.append(table);
        break;
      }
      default:
        element = node("p", "", token.raw || token.text || "");
    }
    fragment.append(element);
  }
  return fragment;
}
function renderSafeMarkdown(text) {
  return safeMarkdownBlocks(
    marked.lexer(String(text), { gfm: true, breaks: true }),
  );
}
function highlightCode(element, code, language) {
  const aliases = {
    js: "javascript",
    py: "python",
    javascript: "javascript",
    python: "python",
    json: "json",
    css: "css",
    c: "clike",
    cpp: "clike",
  };
  const grammar = Prism.languages[aliases[language.toLowerCase()]];
  if (!grammar || code.length > 60000) return;
  function render(tokens) {
    const fragment = document.createDocumentFragment();
    for (const token of Array.isArray(tokens) ? tokens : [tokens]) {
      if (typeof token === "string")
        fragment.append(document.createTextNode(token));
      else {
        const span = node("span", "token " + token.type);
        span.append(render(token.content));
        fragment.append(span);
      }
    }
    return fragment;
  }
  element.replaceChildren(render(Prism.tokenize(code, grammar)));
}
