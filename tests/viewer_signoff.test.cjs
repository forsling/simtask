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
  context.task = {id: "task", title: "Purpose", revision: 8, spec_revision: 4,
    accepted: true, status: "open", acceptance_basis: "delegated", acceptance_note: "Actual delegated authority",
    approval_decision: {decision_ref: 7, task_revision: 1, spec_revision: 4, active: true}, attempts: []};
  context.attempt = {id: "attempt", revision: 2, spec_revision: 4, state: "passed",
    summary: "Actual result", reviewer: "Independent reviewer", review_note: "Actual review proof"};
  run(`state.task = task;
    api = async (action, payload) => {captured = {action, payload}; return {id: "task", revision: 9};};
    markdown = text => node("p", text);
    signoff(task, attempt);`);
  const allText = descendants(get("fields")).map(node => node.textContent).join(" ");
  assert.match(allText, /Actual delegated authority/);
  assert.match(allText, /actual purpose judgment/);
  assert.match(allText, /Supporting approval decision 7/);
  assert.match(allText, /Actual result/);
  assert.match(allText, /Actual review proof/);
  const choice = get("field-decision"), quality = get("field-result_judgment");
  assert.deepEqual(choice.children.map(node => node.value), ["approve", "rework", "revise", "drop", "defer"]);
  context.values = new Map([["decision", "approve"], ["user_note", "I approve purpose and result"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.payload.decision, "approve");
  assert.equal(context.captured.payload.expected_revision, 8);
  assert.equal(context.captured.payload.expected_attempt_revision, 2);
  assert.equal("result_judgment" in context.captured.payload, false);
  assert.equal("verdict" in context.captured.payload, false);
  assert.equal("rejection" in context.captured.payload, false);
  for (const decision of ["rework", "revise", "drop", "defer"]) {
    choice.value = decision; choice.onchange();
    context.values = new Map([["decision", decision], ["user_note", "Actual verdict"],
      ["specification_question", "Which format should be accepted?"], ["result_judgment", "not_judged"]]);
    await run("submitAction(values)");
    const payload = context.captured.payload;
    assert.equal(payload.decision, decision);
    assert.equal("specification_question" in payload, decision === "revise");
    assert.equal(get("field-specification_question").required, decision === "revise");
    assert.equal("result_judgment" in payload, decision !== "rework");
    if (decision !== "rework") assert.equal(payload.result_judgment, "not_judged");
    assert.equal("result_note" in payload, false);
  }
  quality.value = "accepted"; quality.onchange();
  assert.equal(get("field-result_note").required, true);
  context.values.set("result_judgment", "accepted");
  context.values.set("result_note", "Actual separately supplied technical judgment");
  await run("submitAction(values)");
  assert.equal(context.captured.payload.result_note, "Actual separately supplied technical judgment");
  context.task.acceptance_basis = "specific";
  assert.match(descendants(run("purposeSummary(task)")).map(n => n.textContent).join(" "), /Purpose already approved/);
  run("task.status = 'dropped'; disposition(task, 'open', 'Resume');");
  assert.equal(get("field-authorization").required, true);
  context.values = new Map([["note", "Resume"], ["authorization", "Actual user revival instruction"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.payload.authorization, "Actual user revival instruction");
}
test().catch(error => {console.error(error); process.exitCode = 1;});
