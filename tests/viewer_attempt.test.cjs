// Run the shipped selection and detail handlers; no browser/layout claim.
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
const run = code => vm.runInContext(code, context);
function descendants(n) {
  if (!n) return [];
  return [n, ...(n.children || []).flatMap(c => c && typeof c === "object" ? descendants(c) : [])];
}
const labels = panel => descendants(panel).map(n => n.textContent);
// The browser records no review or verdict: sign-off hands a prompt to an agent, and a
// result under review offers nothing to do.
for (const stream of ["main", ""]) {
  run(`state.stream = ${JSON.stringify(stream)};`);
  const shown = labels(run('nextStep(task, "signoff")'));
  assert.ok(shown.includes("Sign off with an agent"));
  assert.ok(!shown.some(l => /Approve|Request changes|Record my review/.test(l)));
}
run('state.stream = "alt";');
assert.ok(!labels(run('nextStep(task, "progress")')).includes("Record my review"));
assert.equal(run('nextStep(task, "signoff")'), null, "No eligible scoped sign-off candidate");
run('state.stream = "outside";');
let outsidePanel = labels(run('nextStep(task, "signoff")'));
assert.ok(outsidePanel.includes("Add this task to a workstream"));
assert.ok(!outsidePanel.includes("Sign off with an agent"));
assert.ok(!outsidePanel.includes("Edit first"));
// A human-reviewed result also qualifies, even when followed by a rework result.
run('state.stream = "";');
context.task.attempts[0].state = "human_review";
assert.ok(labels(run('nextStep(task, "signoff")')).includes("Sign off with an agent"));
// Factual proof coexists with gates; no autonomous review or sign-off is implied.
run('state.stream = "alt";');
context.task.unresolved_items = [{id: "question", text: "Unsettled requirement"}];
let panel = labels(run('nextStep(task, "decision")'));
assert.ok(panel.includes("A design/decision needs your answer"));
assert.ok(panel.some(l => /A recorded result is kept below/.test(l)));
assert.ok(!panel.includes("Record my review"));
context.task.unresolved_items = [];
// A passed result stays with the user for sign-off even while a prerequisite is open;
// the panel names the blocker in one line. Without a passed result the blocker shows.
context.task.prerequisites = [{blocking: true, title: "Publish the schema"}];
run('state.stream = "main";');
panel = labels(run('nextStep(task, "signoff")'));
assert.ok(panel.includes("Sign off with an agent"));
assert.ok(!panel.includes("Waiting on prerequisites"));
assert.ok(panel.some(l => l.includes("A prerequisite is still open: “Publish the schema”. Weigh it in the walkthrough.")));
assert.ok(!panel.some(l => /A recorded result is kept below/.test(l)));
context.task.prerequisites.push({blocking: true, title: "Second"}, {blocking: false, title: "Satisfied"});
panel = labels(run('nextStep(task, "signoff")'));
assert.ok(panel.some(l => l.includes("2 prerequisites are still open: “Publish the schema”, “Second”.")));
context.task.prerequisites = [{blocking: true, title: "Publish the schema"}];
run('state.stream = "alt";');
for (const view of ["progress", "open", "signoff"]) {
  panel = labels(run(`nextStep(task, ${JSON.stringify(view)})`));
  assert.ok(panel.includes("Waiting on prerequisites"), view);
  assert.ok(!panel.includes("Sign off with an agent"), view);
}
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
assert.ok(panel.some(n => n.textContent === "Worth-doing concern · Implementer Builder"));
assert.ok(panel.some(n => n.textContent === "Approach concern · Reviewer Checker"));
assert.ok(panel.some(n => n.textContent.includes("(alt) · spec 2")), "Concern provenance stays with the original result");
assert.ok(panel.some(n => n.textContent === context.proof.concerns[1].text));
assert.equal(run('concernPanel({...proof, concerns: []})'), null);
// Check the actual detail body uses the same action candidate. Isolate unrelated
// Markdown/activity rendering so the DOM double need not implement a browser.
run('markdown = (text) => node("p", text); activity = () => null; attemptCard = (a) => node("article", a.id);');
// The detail derives the task's standing from its own current-spec results in view.
function shownResult(standing, stream) {
  run(`state.stream = ${JSON.stringify(stream)};`);
  assert.equal(run("taskStanding(task)"), standing);
  const section = run('body(task)').find(n => n && descendants(n).some(c => c.textContent === "Result"));
  return section?.children[1].textContent;
}
assert.equal(shownResult("signoff", "main"), "main-passed");
assert.equal(shownResult("signoff", ""), "main-passed");
assert.equal(shownResult("progress", "alt"), "alt-review");
context.task.status = "done";
context.task.selected_attempt_id = "main-passed";
for (const stream of ["main", "alt", ""]) {
  assert.equal(shownResult("done", stream), "main-passed", "Completed result is the selected attempt");
}
