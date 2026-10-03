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
    queue_workstream_id: "main", status: "open", attempts: []};
  context.attempt = {id: "attempt", revision: 2, spec_revision: 4, state: "passed",
    summary: "Actual result", reviewer: "Independent reviewer", review_note: "Actual review proof"};
  run(`state.task = task;
    api = async (action, payload) => {captured = {action, payload}; return {id: "task", revision: 9};};
    markdown = text => node("p", text);
    signoff(task, attempt);`);
  const allText = descendants(get("fields")).map(node => node.textContent).join(" ");
  assert.doesNotMatch(allText, /Approval basis|Supporting approval/);
  assert.match(allText, /Actual result/);
  assert.match(allText, /Actual review proof/);
  const choice = get("field-decision"), reasons = get("field-reasons");
  assert.deepEqual(choice.children.map(node => node.value), ["approve", "rework", "revise", "drop"]);
  assert.equal(descendants(get("fields")).some(node => ["field-result_judgment", "field-specification_question", "field-result_note", "field-user_note"].includes(node.id)), false);
  for (const decision of ["approve", "rework", "revise", "drop"]) {
    choice.value = decision; choice.onchange();
    assert.equal(reasons.required, ["rework", "revise"].includes(decision));
    context.values = new Map([["decision", decision], ["reasons", "Actual reasons"]]);
    await run("submitAction(values)");
    const payload = context.captured.payload;
    assert.equal(payload.decision, decision);
    assert.equal(payload.reasons, "Actual reasons");
    assert.equal(payload.expected_revision, 8);
    assert.equal(payload.expected_attempt_revision, 2);
    for (const key of ["result_judgment", "result_note", "specification_question", "user_note", "note"]) assert.equal(key in payload, false);
  }
  // Both history generations render without inventing judgments or throwing.
  run(`task.unresolved_items = []; task.blocked_by = []; task.prerequisites = []; task.gate_proposals = [];
    task.body = "Spec"; task.acceptance_criteria = "Criteria";
    task.signoff_decisions = [
      {decision: "defer", disposition: "deferred", purpose_judgment: "deferred", purpose_source: "user_verdict", result_judgment: "accepted", user_note: "Old defer reasons", result_note: "Old quality note"},
      {decision: "drop", disposition: "dropped", reasons: "New drop reasons"}];
    task.latest_rejection = {source: "review", verdict: "rework", reasons: "Reviewer reasons", attempt_id: "origin-attempt", workstream_id: "origin-branch", spec_revision: 4, timestamp: "then"};
    activity = () => node("div"); rendered = body(task);`);
  const renderedText = context.rendered.flatMap(descendants).map(node => node.textContent).join(" ");
  assert.match(renderedText, /Old defer reasons/); assert.match(renderedText, /Old quality note/);
  assert.match(renderedText, /New drop reasons/); assert.match(renderedText, /Reviewer reasons/);
  assert.match(renderedText, /origin-branch/);
  run("task.status = 'dropped'; disposition(task, 'open', 'Resume');");
  assert.equal(get("field-authorization").required, true);
  context.values = new Map([["note", "Resume"], ["authorization", "Actual user revival instruction"]]);
  await run("submitAction(values)");
  assert.equal(context.captured.payload.authorization, "Actual user revival instruction");
}
test().catch(error => {console.error(error); process.exitCode = 1;});
