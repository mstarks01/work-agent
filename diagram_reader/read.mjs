// Reads one Mermaid diagram on stdin and prints its structure as one JSON line
// (ADR 0121). A flowchart goes through Mermaid's flowchart grammar, an
// architecture-beta diagram through the Langium grammar in @mermaid-js/parser.
// A shape or an icon is printed as written: what it means is extraction's
// judgement.
//
// Output: {"structure": {...}} for a diagram it read, {"refused": "..."} for
// one it cannot read. Exit code 3 means a package is missing, so the caller can
// tell an install fault from a diagram fault.
import { readFileSync } from "node:fs";

const text = readFileSync(0, "utf8");
const lines = text.split("\n");

// The first diagram line, past blank lines, %% comments and front matter.
function header() {
  let inFrontMatter = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (line === "---") { inFrontMatter = !inFrontMatter; continue; }
    if (inFrontMatter || !line || line.startsWith("%%")) continue;
    return line;
  }
  return "";
}

// The first line that holds every token as a whole word, 1-based.
function lineOf(...tokens) {
  const index = lines.findIndex((line) => {
    const words = new Set(line.split(/[^A-Za-z0-9_-]+/));
    return tokens.every((token) => words.has(token));
  });
  return index < 0 ? null : index + 1;
}

// A label as plain text: Mermaid's placeholder for an entity code decoded, a
// line break a space, and any other markup dropped.
function plain(label) {
  return String(label ?? "")
    .replace(/ﬂ°°(\d+)¶ß/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/<br\s*\/?>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function arrowOf(type) {
  if (type.startsWith("double_arrow")) return "both";
  if (type === "arrow_open" || type === "invisible") return "none";
  return "forward";
}

async function flowchart() {
  const { JSDOM } = await import("jsdom");
  const dom = new JSDOM("<!doctype html><body></body>");
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
  const db = (await mermaid.mermaidAPI.getDiagramFromText(text)).db;
  const subgraphs = db.getSubGraphs();
  const parentOf = new Map();
  for (const subgraph of subgraphs) {
    for (const member of subgraph.nodes) if (!parentOf.has(member)) parentOf.set(member, subgraph.id);
  }
  const groupIds = new Set(subgraphs.map((subgraph) => subgraph.id));
  return {
    kind: "flowchart",
    groups: subgraphs.map((subgraph) => ({
      id: subgraph.id, label: plain(subgraph.title) || subgraph.id,
      parent: parentOf.get(subgraph.id) ?? null, line: lineOf(subgraph.id) })),
    nodes: [...db.getVertices().values()].filter((vertex) => !groupIds.has(vertex.id)).map((vertex) => ({
      id: vertex.id, label: plain(vertex.text) || vertex.id, shape: vertex.type || "square",
      group: parentOf.get(vertex.id) ?? null, line: lineOf(vertex.id) })),
    edges: db.getEdges().map((edge) => ({
      source: edge.start, target: edge.end, label: plain(edge.text),
      arrow: arrowOf(edge.type || "arrow_point"), line: lineOf(edge.start, edge.end) })),
    junctions: [],
  };
}

// The 1-based diagram line of an architecture AST node, from Langium's own
// position.
function astLine(node) {
  const line = node.$cstNode?.range.start.line;
  return line === undefined ? null : line + 1;
}

async function architecture() {
  const { parse } = await import("@mermaid-js/parser");
  const ast = await parse("architecture", text);
  const parentOf = new Map([...ast.services, ...ast.junctions].map((item) => [item.id, item.in ?? null]));
  // An end marked {group} is the group that holds the node, not the node.
  const end = (id, group) => (group && parentOf.get(id)) || id;
  return {
    kind: "architecture",
    groups: ast.groups.map((group) => ({
      id: group.id, label: plain(group.title) || group.id, parent: group.in ?? null,
      line: astLine(group) })),
    nodes: ast.services.map((service) => ({
      id: service.id, label: plain(service.title) || service.id, shape: service.icon ?? "",
      group: service.in ?? null, line: astLine(service) })),
    junctions: ast.junctions.map((junction) => ({
      id: junction.id, group: junction.in ?? null, line: astLine(junction) })),
    edges: ast.edges.map((edge) => ({
      source: end(edge.lhsId, edge.lhsGroup), target: end(edge.rhsId, edge.rhsGroup), label: "",
      arrow: edge.lhsInto && edge.rhsInto ? "both" : edge.rhsInto ? "forward" : edge.lhsInto ? "backward" : "none",
      line: astLine(edge) })),
  };
}

const first = header();
const read = /^(flowchart|graph)\b/.test(first) ? flowchart
  : /^architecture-beta\b/.test(first) ? architecture : null;
try {
  if (!read) {
    console.log(JSON.stringify({ refused: "the diagram is not a flowchart or an architecture-beta diagram" }));
  } else {
    console.log(JSON.stringify({ structure: await read() }));
  }
} catch (error) {
  if (error?.code === "ERR_MODULE_NOT_FOUND") {
    console.error(String(error.message));
    process.exit(3);
  }
  console.log(JSON.stringify({ refused: String(error?.message ?? error).slice(0, 500) }));
}
