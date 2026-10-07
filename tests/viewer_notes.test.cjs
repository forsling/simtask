// The read-only project/workstream note panel is gone until the viewer task for titled
// notes: a board load neither requests notes nor renders a notes panel.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const roots = new Map();
function element(tag = "div") {
  return {tag, children: [], textContent: "", value: "", hidden: false, open: false, dataset: {}, style: {},
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
    append(...children) { this.children.push(...children); },
    prepend() {}, replaceChildren(...children) { this.children = children; },
    setAttribute() {}, addEventListener(type, fn) { this["on" + type] = fn; },
    querySelector() { return null; }, scrollIntoView() {}};
}
const get = id => roots.get(id) || roots.set(id, element()).get(id);
const context = vm.createContext({
  document: {getElementById: get, createElement: element, createElementNS: () => element(),
    addEventListener() {}, querySelectorAll() { return []; }},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => "", setItem() {}}, history: {replaceState() {}, pushState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
const text = node => [node.textContent, ...(node.children || []).map(text)].join("");
async function test() {
  run(`state.project = "p"; state.stream = "w";
    state.projects = [{id: "p", name: "Project"}];
    state.streams = [{id: "w", project_id: "p", branch: "main"}];
    renderDetail = () => {}; selectTask = async () => {}; toast = (m) => toasts.push(m);
    api = async (action, data) => {
      calls.push([action, data]);
      if (action === "tasks") return {items: [{id: "t", title: "T", view: "ready"}], next_offset: null, workstream_order_revision: 1};
      if (action === "workstreams") return {items: state.streams, next_offset: null};
      throw new Error("unexpected " + action);
    };`);
  context.calls = [];
  context.toasts = [];
  await run("reload()");
  assert.deepEqual(context.calls.map(([a]) => a).sort(), ["tasks", "workstreams"]);
  assert.equal(run("state.rows.length"), 1);
  assert.equal(run("typeof renderNotes"), "undefined");
  assert.equal(roots.has("notes"), false, "no notes panel is looked up");
  assert.equal(context.toasts.length, 0, context.toasts.join("; "));
  const assets = path.join(__dirname, "../src/task_mcp/viewer_assets");
  assert.doesNotMatch(fs.readFileSync(path.join(assets, "index.html"), "utf8"), /id="notes"/);
  assert.doesNotMatch(fs.readFileSync(path.join(assets, "style.css"), "utf8"), /^\.notes? /m);
  console.log("viewer notes removed ok");
}
test().catch(error => { console.error(error); process.exit(1); });
