// Exercise the shipped creation and decision handlers with compact server ACKs.
// This verifies form/payload behavior; it makes no visual layout claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", disabled: false, hidden: false, open: false,
    value: "", checked: false, required: false, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { for (const child of children) {
      this.children.push(child); if (child && typeof child === "object") child.parentElement = this;
    } },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute() {}, querySelector() { return null; },
    showModal() { this.open = true; },
    close() { this.open = false; this.onclose?.(); }, remove() {},
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

  // An empty queue picker must not disable the next use of the shared dialog.
  run("pages = async () => []; state.stream = null;");
  for (const [open, title] of [
    [() => run("createTask()"), "New task"],
    [() => run("editTask(task)"), "Edit task"],
    [() => run("askQuestion(task)"), "Ask a question"],
    [() => get("stop").onclick(), "Stop the viewer?"],
  ]) {
    await run("queueTask(task)");
    assert.equal(get("submit").disabled, true, "Empty picker cannot queue a task");
    assert.match(descendants(get("fields")).map(node => node.textContent).join(" "), /Initialize a branch/);
    get("cancel").onclick();
    assert.equal(get("dialog").open, false);
    open();
    assert.equal(get("dialog-title").textContent, title);
    assert.equal(get("submit").disabled, false, `${title} must be saveable after canceling the picker`);
    assert.equal(get("cancel").disabled, false);
    assert.equal(get("close").disabled, false);
    get("close").onclick();
  }

  // Fetch failures leave subsequent dialogs usable; save failures preserve the
  // new draft and release the controls so the real submit handler can retry.
  run('pages = async () => { throw new Error("Synthetic picker failure"); };');
  await assert.rejects(run("queueTask(task)"), /Synthetic picker failure/);
  run("createTask(); api = async () => { throw new Error('Synthetic save failure'); };");
  context.values = new Map([["title", "Retryable task"], ["body", "Scope"], ["acceptance_criteria", "Proof"]]);
  get("field-title").value = "Retryable task";
  run("FormData = class extends Map { constructor() { super(values); } };");
  assert.equal(get("submit").disabled, false);
  await get("form").onsubmit({preventDefault() {}});
  assert.equal(get("dialog").open, true, "Failed save keeps the new-task draft open");
  assert.equal(get("field-title").value, "Retryable task");
  assert.match(get("form-error").textContent, /Synthetic save failure/);
  assert.equal(get("submit").disabled, false);
  assert.equal(get("cancel").disabled, false);
  assert.equal(get("close").disabled, false);
  run("api = async (action, payload) => {captured = {action, payload}; return {id: 'recovered', revision: 1, stopped: true};};");
  await get("form").onsubmit({preventDefault() {}});
  assert.equal(context.captured.action, "create");
  assert.equal(context.captured.payload.title, "Retryable task");
  assert.equal(get("dialog").open, false, "Retry can save and close the dialog");
}
test().catch(error => {console.error(error); process.exitCode = 1;});
