// Exercise the shipped creation and decision handlers with compact server ACKs.
// This verifies form/payload behavior; it makes no visual layout claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", disabled: false, hidden: false,
    value: "", checked: false, required: false, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { for (const child of children) {
      this.children.push(child); if (child && typeof child === "object") child.parentElement = this;
    } },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute() {}, querySelector(selector) { return descendants(this).find(n => selector === "[type=checkbox]" && n.type === "checkbox") || null; }, showModal() {}, close() {}, remove() {},
  };
  all.push(node);
  return node;
}
function get(id) {
  const found = [...all].reverse().find(node => node.id === id);
  if (found) return found;
  if (!roots.has(id)) roots.set(id, element());
  return roots.get(id);
}
const context = vm.createContext({
  document: {getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag), createDocumentFragment: element,
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
function descendants(node) {return [node, ...(node.children || []).flatMap(descendants)];}
async function test() {
  context.task = {id: "task", title: "Signed off task", project_id: "project", status: "done",
    object_type: "task", revision: 9, body: "Complete scope", acceptance_criteria: "Proof",
    workstream_ids: ["a", "b"], unresolved_items: [], blocked_by: [], attempts: [], gate_proposals: []};
  run(`state.project = "project"; state.task = task; state.stream = "a"; state.streams = []; state.projects = [];
    pages = async () => [{id: "a", branch: "A"}, {id: "b", branch: "B"}];
    calls = []; api = async (action, payload) => { captured = {action, payload}; calls.push(captured);
      return action === "tasks" ? {workstream_order_revision: 7, next_offset: payload.offset === 0 ? 100 : null,
        items: payload.offset === 0 ? [{id: "task", title: task.title, workstream_order_key: 1, view: "done"},
          {id: "anchor", title: "Open anchor", workstream_order_key: 2, view: "ready"}] :
          [{id: "unseen", title: "Task beyond first page", workstream_order_key: 3}]} :
        {workstream_id: payload.workstream_id, workstream_order_revision: 8, changed: true}; };`);
  run("renderDetail(task)");
  const labels = descendants(get("detail")).map(n => n.textContent);
  assert.ok(labels.includes("Reorder tasks"), "Completed tasks may change list position");
  assert.ok(!labels.includes("Edit (e)"));
  await run("moveTask(task)");
  assert.equal(context.captured.action, "tasks");
  assert.equal(context.captured.payload.workstream_id, "a");
  assert.equal(get("field-workstream_id").value, "a");
  assert.equal(get("field-anchor_id").required, true);
  assert.deepEqual(get("field-anchor_id").children.map(n => n.value), ["anchor", "unseen"]);
  assert.ok(!get("fields").children.some(n => n.name === "instruction"));
  context.values = new Map([["workstream_id", "a"], ["anchor_id", "anchor"], ["position", "after"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.action, "reorder");
  assert.deepEqual(JSON.parse(JSON.stringify(context.captured.payload)), {
    workstream_id: "a", task_ids: ["anchor", "task", "unseen"], expected_order_revision: 7,
  });
  assert.equal(context.calls.filter(c => c.action === "reorder").length, 1, "One call retains unseen members");
  // Conflict reconciliation keeps the named workstream and reloads its complete order.
  run(`api = async (action, payload) => {
    captured = {action, payload};
    if (action === "tasks") return {workstream_order_revision: 11, next_offset: null,
      items: [{id: "unseen", title: "Earlier now", workstream_order_key: 1}, {id: "task", title: task.title, workstream_order_key: 2}, {id: "anchor", title: "Open anchor", workstream_order_key: 3}]};
    const error = new Error("revision_conflict"); error.conflict = true; throw error;
  };`);
  await assert.rejects(run("submitAction(values)"), /revision_conflict/);
  await descendants(get("conflict")).find(n => n.textContent === "Show the current workstream order").onclick();
  assert.equal(context.captured.payload.workstream_id, "a");
  descendants(get("conflict")).find(n => n.textContent === "Use this order").onclick();
  run(`api = async (action, payload) => {captured = {action, payload}; return {changed: false, workstream_order_revision: 11};};`);
  assert.equal((await run("submitAction(values)")).changed, false);
  assert.equal(context.captured.payload.expected_order_revision, 11);
  assert.deepEqual(Array.from(context.captured.payload.task_ids), ["unseen", "anchor", "task"]);
  // Choosing B refreshes its own anchors/revision, never sends A's stale list to B.
  run(`api = async (action, payload) => {captured = {action, payload}; return action === "tasks" ? {
    workstream_order_revision: 20, next_offset: null, items: [{id: "anchor", title: "B first", workstream_order_key: 1}, {id: "task", title: task.title, workstream_order_key: 2}]} : {changed: true};};`);
  get("field-workstream_id").value = "b";
  await get("field-workstream_id").onchange();
  await new Promise(resolve => setImmediate(resolve));
  context.values.set("workstream_id", "b");
  await run("submitAction(values)");
  assert.equal(context.captured.payload.workstream_id, "b");
  assert.equal(context.captured.payload.expected_order_revision, 20);
  assert.deepEqual(Array.from(context.captured.payload.task_ids), ["anchor", "task"]);
  run(`api = async (action, payload) => ({items: [], next_offset: payload.offset === 0 ? 100 : null,
     workstream_order_revision: payload.offset === 0 ? 11 : 12});`);
  await assert.rejects(run('taskBoard({project: "project", workstream_id: "a"})'), /order changed while loading/);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
