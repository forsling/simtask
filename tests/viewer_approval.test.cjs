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
    setAttribute() {}, querySelector() { return null; }, showModal() {}, close() {},
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
  document: {getElementById: get, createElement: element, addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
function descendants(node) {return [node, ...(node.children || []).flatMap(descendants)];}
async function test() {
  run(`state.project = "project"; state.stream = "stream"; state.streams = []; state.projects = [];
    api = async (action, payload) => {captured = {action, payload};
      return {id: "created", revision: 1, spec_revision: 1, accepted: !!payload.approval, gate_diagnostics: []};};
    createTask();`);
  const mode = get("field-creation_mode");
  assert.equal(mode.value, "pending");
  assert.equal(get("field-approval_note").disabled, true);
  assert.equal(get("field-approval_note").required, false);
  context.values = new Map([
    ["title", "Scope"], ["body", "Exact spec"], ["acceptance_criteria", "Proof"],
    ["user_request", "Design first; leave pending"], ["creation_mode", "pending"],
  ]);
  await run("submitAction(values)");
  assert.equal(context.captured.action, "create");
  assert.equal(context.captured.payload.source, "user");
  assert.equal(context.captured.payload.user_request, "Design first; leave pending");
  assert.equal("approval" in context.captured.payload, false);
  assert.equal(run("state.selected"), "created", "Compact ACK ID selects the saved task");
  mode.value = "accepted"; mode.onchange();
  assert.equal(get("field-approval_note").required, true);
  assert.equal(get("field-approval_note").disabled, false);
  context.values.set("creation_mode", "accepted");
  context.values.set("approval_note", "I approve this exact specification");
  await assert.rejects(run("submitAction(values)"), /Confirm approval/);
  context.values.set("approve_exact_spec", "on");
  await run("submitAction(values)");
  assert.equal(context.captured.payload.approval.basis, "specific");
  assert.equal(context.captured.payload.approval.note, "I approve this exact specification");
  assert.equal("approval_note" in context.captured.payload, false);
  context.task = {id: "task", title: "Scope", revision: 8, spec_revision: 1,
    accepted: true, status: "open", attempts: [], unresolved_items: []};
  run("state.task = task; withdrawAcceptance(task);");
  context.values = new Map([["note", "Recorded acceptance was mistaken"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.action, "withdraw-acceptance");
  assert.equal(context.captured.payload.expected_revision, 8);
  assert.equal(context.captured.payload.note, "Recorded acceptance was mistaken");
  context.task.revision = 9; context.task.accepted = false;
  run("accept(task);");
  context.values = new Map([["approval_note", "Renewed approval of unchanged spec"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.payload.expected_revision, 9);
  assert.equal(context.captured.payload.approval.basis, "specific");
  assert.equal("user_note" in context.captured.payload, false);
  context.task.attempts = [{id: "reviewed", spec_revision: 1, state: "passed", workstream_id: "stream"}];
  const labels = descendants(run('nextStep(task, "signoff")')).map(node => node.textContent);
  assert.ok(labels.includes("Accept spec"));
  assert.ok(!labels.includes("Approve & sign off"), "Withdrawn approval gates sign-off presentation");
}
test().catch(error => {console.error(error); process.exitCode = 1;});
