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
    workstream_ids: ["main"], unresolved_items: [], blocked_by: [], attempts: [], gate_proposals: []};
  run(`state.project = "project"; state.task = task; state.streams = []; state.projects = [];
    api = async (action, payload) => { captured = {action, payload};
      return action === "tasks" ? {project_order_revision: 7, next_offset: null,
        items: [{id: "task", title: task.title, order_key: 1, view: "done"},
                {id: "anchor", title: "Open anchor", order_key: 2, view: "ready"}]} :
        {task_id: "task", anchor_id: "anchor", project_order_revision: 8, changed: true}; };`);
  run("renderDetail(task)");
  const labels = descendants(get("detail")).map(n => n.textContent);
  assert.ok(labels.includes("Move in project order"), "Scheduling controls remain available for completed tasks");
  assert.ok(!labels.includes("Edit (e)"));
  await run("moveTask(task)");
  assert.equal(context.captured.action, "tasks");
  assert.equal("workstream_id" in context.captured.payload, false, "Anchor selection uses shared project order");
  assert.equal(get("field-anchor_id").required, true);
  assert.deepEqual(get("field-anchor_id").children.map(n => n.value), ["anchor"], "Self is excluded");
  context.values = new Map([["anchor_id", "anchor"], ["position", "after"], ["instruction", "User asked to move it after the anchor"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.action, "reorder");
  assert.deepEqual(JSON.parse(JSON.stringify(context.captured.payload)), {
    project: "project", task_id: "task", anchor_id: "anchor", position: "after",
    expected_order_revision: 7, instruction: "User asked to move it after the anchor",
  });
  run(`api = async (action, payload) => {
    captured = {action, payload};
    if (action === "tasks") return {project_order_revision: 11, next_offset: null,
      items: [{id: "task", title: task.title, order_key: 1}, {id: "anchor", title: "Open anchor", order_key: 2}]};
    const error = new Error("revision_conflict"); error.conflict = true; throw error;
  };`);
  await assert.rejects(run("submitAction(values)"), /revision_conflict/);
  assert.equal(get("field-instruction").value, "", "Conflict keeps existing form fields intact");
  await descendants(get("conflict")).find(n => n.textContent === "Show the current project order").onclick();
  const checkbox = descendants(get("fields")).find(n => n.type === "checkbox");
  checkbox.checked = true;
  descendants(get("conflict")).find(n => n.textContent === "I've reviewed it — use this order").onclick();
  assert.equal(checkbox.checked, false);
  run(`api = async (action, payload) => {captured = {action, payload}; return {changed: false, project_order_revision: 11};};`);
  const noop = await run("submitAction(values)");
  assert.equal(noop.changed, false);
  assert.equal(context.captured.payload.expected_order_revision, 11);
  assert.equal(context.captured.payload.instruction, "User asked to move it after the anchor");
  run(`api = async (action, payload) => ({items: [], next_offset: payload.offset === 0 ? 100 : null,
     project_order_revision: payload.offset === 0 ? 11 : 12});`);
  await assert.rejects(run('taskBoard({project: "project"})'), /order changed while loading/);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
