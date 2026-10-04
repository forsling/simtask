// Run the shipped selection, detail and decision handlers; no browser/layout claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
function element() {
  return {textContent: "", children: [], classList: {add() {}, remove() {}, toggle() {}},
    append(...children) {this.children.push(...children);}, setAttribute() {},
    replaceChildren(...children) {this.children = children;}, querySelector() {return null;}};
}
const context = vm.createContext({
  document: {getElementById: element, createElement: element, addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem() {return "";}}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const attempt = (id, workstream_id, state, spec_revision = 2) => ({
  id, workstream_id, state, spec_revision, revision: 7, implementer: id,
});
context.task = {id: "task", spec_revision: 2, revision: 12, status: "open", workstream_ids: ["main", "alt"], prerequisites: [], attempts: [
  attempt("main-passed", "main", "passed"),
  attempt("alt-review", "alt", "review"),
  attempt("alt-rework", "alt", "rework"),
  attempt("main-stale", "main", "passed", 1),
], unresolved_items: [], blocked_by: [], gate_proposals: []};
vm.runInContext('decision = (options) => {captured = options;};', context);
const run = code => vm.runInContext(code, context);
function descendants(n) {
  if (!n) return [];
  return [n, ...(n.children || []).flatMap(c => c && typeof c === "object" ? descendants(c) : [])];
}
function click(view, label, id) {
  const panel = run(`nextStep(task, ${JSON.stringify(view)})`);
  const button = descendants(panel).find(n => n.textContent === label);
  assert.ok(button, label);
  button.onclick();
  assert.equal(context.captured.data.attempt_id, id);
  assert.equal(context.captured.data.expected_revision, view === "review" ? 7 : 12);
}
for (const stream of ["main", ""]) {
  run(`state.stream = ${JSON.stringify(stream)};`);
  click("signoff", "Approve & sign off", "main-passed");
  click("signoff", "Request changes", "main-passed");
}
run('state.stream = "alt";');
click("review", "Record my review", "alt-review");
assert.equal(run('nextStep(task, "signoff")'), null, "No eligible scoped sign-off candidate");
run('state.stream = "outside";');
let outsidePanel = descendants(run('nextStep(task, "signoff")'));
assert.ok(outsidePanel.some(n => n.textContent === "Add this task to a workstream"));
assert.ok(!outsidePanel.some(n => n.textContent === "Approve & sign off"));
run('state.stream = "";');
click("review", "Record my review", "alt-review");
// A human-reviewed result also qualifies, even when followed by a rework result.
context.task.attempts[0].state = "human_review";
click("signoff", "Approve & sign off", "main-passed");
// Factual proof coexists with gates; no autonomous review or sign-off is implied.
run('state.stream = "alt";');
context.task.unresolved_items = [{id: "question", text: "Unsettled requirement"}];
let panel = descendants(run('nextStep(task, "review")'));
assert.ok(panel.some(n => n.textContent === "One question needs an answer"));
assert.ok(panel.some(n => /A factual result is saved below/.test(n.textContent)));
assert.ok(!panel.some(n => n.textContent === "Next agent action: review"));
assert.ok(panel.some(n => n.textContent === "Record my review"), "Explicit human review remains available");
context.task.unresolved_items = [];
context.task.prerequisites = [{blocking: true}];
run('state.stream = "main";');
panel = descendants(run('nextStep(task, "signoff")'));
assert.ok(panel.some(n => n.textContent === "Waiting on prerequisites"));
assert.ok(!panel.some(n => n.textContent === "Approve & sign off"));
context.task.prerequisites = [];
// Deterministic newest-first / ID tie breaking, scoped and current-spec only.
context.task.attempts.push({...attempt("a-pending", "alt", "review"), created_at: "2030-01-01"},
  {...attempt("b-pending", "alt", "review"), created_at: "2030-01-01"});
run('state.stream = "alt";');
assert.equal(run('currentAttempt(task, ["review"]).id'), "a-pending");
context.task.attempts.splice(-2);
run('markdown = (text) => node("p", text);');
context.proof = {...attempt("proof", "alt", "review"), summary: "Built", evidence: "Full context",
  artifacts: [{kind: "commit", reference: "abcdef0123456789"}], verification: "Actual check passed",
  concerns: [{kind: "value", text: "Changing priority changes scope.", source: "implementer", author: "Builder"},
    {kind: "design", text: "<img src=x onerror=alert(1)>\nDifferent design needs a task change.", source: "reviewer", author: "Checker"}]};
panel = descendants(run('attemptCard(proof, task)'));
assert.ok(panel.some(n => n.textContent === "commit: abcdef0123456789"));
assert.ok(panel.some(n => n.textContent === "Actual check passed"));
assert.ok(panel.some(n => n.textContent === "Value · Implementer Builder"));
assert.ok(panel.some(n => n.textContent === "Design · Reviewer Checker"));
assert.ok(panel.some(n => n.textContent.includes("(alt) · spec 2")), "Concern provenance stays with the original result");
assert.ok(panel.some(n => n.textContent === context.proof.concerns[1].text));
assert.equal(run('concernPanel({...proof, concerns: []})'), null);
// Check the actual detail body uses the same action candidate. Isolate unrelated
// Markdown/activity rendering so the DOM double need not implement a browser.
run('markdown = (text) => node("p", text); activity = () => null; attemptCard = (a) => node("article", a.id);');
function shownResult(view, stream) {
  run(`state.rows = [{id: "task", view: ${JSON.stringify(view)}}]; state.stream = ${JSON.stringify(stream)};`);
  const section = run('body(task)').find(n => n && descendants(n).some(c => c.textContent === "Result"));
  return section?.children[1].textContent;
}
assert.equal(shownResult("signoff", "main"), "main-passed");
assert.equal(shownResult("signoff", ""), "main-passed");
assert.equal(shownResult("review", "alt"), "alt-review");
context.task.status = "done";
context.task.selected_attempt_id = "main-passed";
for (const stream of ["main", "alt", ""]) {
  assert.equal(shownResult("done", stream), "main-passed", "Completed result is the selected attempt");
}
