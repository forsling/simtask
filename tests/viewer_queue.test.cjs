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
    setAttribute() {}, querySelector() { return null; }, showModal() {}, close() {}, remove() {},
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
  document: {getElementById: get, createElement: element, createDocumentFragment: element,
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
function descendants(node) {return [node, ...(node.children || []).flatMap(descendants)];}
async function test() {
  run(`state.project = "project"; state.stream = "main";
    state.projects = [{id: "project", name: "Project"}];
    state.streams = [{id: "main", project_id: "project", name: "Main", branch: "main"}, {id: "feature", project_id: "project", name: "Feature", branch: "feature"}];
    pages = async () => state.streams;
    api = async (action, payload) => {captured = {action, payload}; return {id: "created", revision: 1};};
    createTask();`);
  assert.equal(get("field-creation_mode").name, undefined);
  context.values = new Map([["title", "Build"], ["body", "Scope"], ["acceptance_criteria", "Proof"], ["user_request", "Actual request"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.payload.workstream_id, "main");
  assert.equal("approval" in context.captured.payload, false);
  assert.equal("scope" in context.captured.payload, false);
  run("state.stream = null; createTask();");
  await run("submitAction(values)");
  assert.equal(context.captured.payload.workstream_id, null);
  context.task = {id: "task", project_id: "project", title: "Task", body: "Scope", acceptance_criteria: "Proof", revision: 8, specification_etag: "token", queue_workstream_id: "main", object_type: "task", status: "open", attempts: [], unresolved_items: []};
  run("state.task = task; moveToInbox(task);");
  assert.equal(get("fields").children.length, 0);
  await run("submitAction(values)");
  assert.equal(context.captured.action, "unqueue");
  assert.deepEqual(Object.keys(context.captured.payload).sort(), ["expected_revision", "task_id"]);
  assert.equal(context.captured.payload.expected_revision, 8);
  await run("queueTask(task)");
  assert.equal(get("field-workstream_id").children.length, 2);
  context.values.set("workstream_id", "feature");
  await run("submitAction(values)");
  assert.equal(context.captured.action, "queue");
  assert.equal(context.captured.payload.workstream_id, "feature");
  assert.equal("note" in context.captured.payload, false);
  run("editTask(task);");
  assert.equal(get("field-save_mode").name, undefined);
  context.values = new Map([["title", "Edited"], ["body", "Complete replacement"], ["acceptance_criteria", "New proof"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.payload.specification_etag, "token");
  assert.equal("approval" in context.captured.payload, false);
  // Placement conflicts preserve the selected branch until explicit current-state review.
  await run("queueTask(task)");
  context.values.set("workstream_id", "feature");
  run(`api = async (action, payload) => {
    if (action === "details") return {items: [{...task, revision: 9, queue_workstream_id: "feature"}]};
    throw Object.assign(new Error("revision_conflict"), {conflict: true});
  }; markdown = text => node("p", text);`);
  await assert.rejects(run("submitAction(values)"), /revision_conflict/);
  assert.equal(context.values.get("workstream_id"), "feature");
  await get("conflict").children[0].onclick();
  const box = get("conflict").children[0];
  await box.children[box.children.length - 1].onclick();
  run(`api = async (action, payload) => {captured = {action, payload}; return {id: task.id, revision: 10};};`);
  await run("submitAction(values)");
  assert.equal(context.captured.payload.expected_revision, 9);
  assert.equal(context.captured.payload.workstream_id, "feature");
}
test().catch(error => {console.error(error); process.exitCode = 1;});
