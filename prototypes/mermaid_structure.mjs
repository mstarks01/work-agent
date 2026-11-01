// PROTOTYPE, throwaway: one Mermaid diagram on stdin, its structure as JSON on
// stdout, read by Mermaid's own parsers. A flowchart goes through Mermaid's
// flowchart grammar (flow.jison), an architecture-beta diagram through the
// Langium parser in @mermaid-js/parser. Shapes and icons are reported as
// written; what they mean is left to the reader of this output.
import { readFileSync } from "node:fs";

const text = readFileSync(0, "utf8");
const lines = text.split("\n");
const lineOf = (...tokens) => {
  const words = (l) => new Set(l.split(/[^A-Za-z0-9_-]+/));
  const i = lines.findIndex((l) => { const w = words(l); return tokens.every((t) => w.has(t)); });
  return i < 0 ? null : { line: i + 1, excerpt: lines[i].trim() };
};
const header = lines.map((l) => l.trim()).find((l) => l && !l.startsWith("%%")) || "";

async function flowchart() {
  const { JSDOM } = await import("jsdom");
  const dom = new JSDOM("<!doctype html><body></body>");
  globalThis.window = dom.window; globalThis.document = dom.window.document;
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
  const db = (await mermaid.mermaidAPI.getDiagramFromText(text)).db;
  const subgraphs = db.getSubGraphs();
  const parentOf = new Map();
  subgraphs.forEach((s) => s.nodes.forEach((n) => { if (!parentOf.has(n)) parentOf.set(n, s.id); }));
  const groupIds = new Set(subgraphs.map((s) => s.id));
  return {
    kind: "flowchart",
    groups: subgraphs.map((s) => ({ id: s.id, label: s.title, parent: parentOf.get(s.id) || null, cite: lineOf(s.id) })),
    nodes: [...db.getVertices().values()].filter((v) => !groupIds.has(v.id)).map((v) => ({
      id: v.id, label: v.text, shape: v.type || "default", group: parentOf.get(v.id) || null, cite: lineOf(v.id) })),
    edges: db.getEdges().map((e) => ({
      from: e.start, to: e.end, label: e.text || "", arrow: e.type, cite: lineOf(e.start, e.end) })),
  };
}

async function architecture() {
  const { parse } = await import("@mermaid-js/parser");
  const ast = await parse("architecture", text);
  return {
    kind: "architecture",
    groups: ast.groups.map((g) => ({ id: g.id, label: g.title || g.id, icon: g.icon || null, parent: g.in || null, cite: lineOf("group", g.id) })),
    nodes: ast.services.map((s) => ({ id: s.id, label: s.title || s.id, icon: s.icon || null, group: s.in || null, cite: lineOf("service", s.id) })),
    edges: ast.edges.map((e) => ({
      from: e.lhsId, to: e.rhsId, label: "",
      arrow: e.lhsInto && e.rhsInto ? "both" : e.rhsInto ? "forward" : e.lhsInto ? "backward" : "none",
      cite: lineOf(e.lhsId, e.rhsId) })),
  };
}

try {
  const read = /^(flowchart|graph)\b/.test(header) ? flowchart
    : /^architecture-beta\b/.test(header) ? architecture : null;
  if (!read) throw new Error(`not a flowchart or architecture-beta diagram: ${header.slice(0, 40)}`);
  console.log(JSON.stringify(await read()));
} catch (error) {
  console.log(JSON.stringify({ error: String(error.message || error).slice(0, 300) }));
}
