// Execute the shipped board load with project/workstream notes: both notes show as
// plain text with author and time, empty ones are omitted, and a failed notes read
// hides the panel without breaking the board. Layout is checked in a real browser.
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
const notes = {
  project: {text: "Rules <b>for</b> the repo", revision: 2, updated_at: new Date().toISOString(), updated_by: "simon"},
  workstream: {text: "Deployed v2\nRollback: v1", revision: 5, updated_at: new Date().toISOString(), updated_by: "agent"},
};
context.notes = notes;
async function test() {
  run(`state.project = "p"; state.stream = "w";
    state.projects = [{id: "p", name: "Project"}];
    state.streams = [{id: "w", project_id: "p", branch: "main"}];
    renderDetail = () => {}; selectTask = async () => {}; toast = (m) => toasts.push(m);
    api = async (action, data) => {
      calls.push([action, data]);
      if (action === "notes") return failNotes ? Promise.reject(new Error("gone")) : {notes: shownNotes};
      if (action === "tasks") return {items: [{id: "t", title: "T", view: "ready"}], next_offset: null, workstream_order_revision: 1};
      if (action === "workstreams") return {items: state.streams, next_offset: null};
      throw new Error("unexpected " + action);
    };`);
  context.calls = [];
  context.toasts = [];
  context.failNotes = false;
  context.shownNotes = notes;
  await run("reload()");
  const box = get("notes");
  assert.equal(JSON.stringify(context.calls.find(([a]) => a === "notes")[1]), JSON.stringify({project: "p", workstream_id: "w"}));
  assert.equal(box.hidden, false);
  assert.equal(box.children.map(d => d.dataset.kind).join(), "project,workstream");
  const [project, workstream] = box.children;
  // Note text is inserted as text, never parsed as markup, and keeps its line breaks.
  assert.equal(project.children[1].textContent, "Rules <b>for</b> the repo");
  assert.equal(workstream.children[1].textContent, "Deployed v2\nRollback: v1");
  assert.match(text(project.children[0]), /Project note.*just now · simon/);
  assert.match(text(workstream.children[0]), /Workstream note.*agent/);
  assert.equal(project.open, true);
  // A collapsed note stays collapsed across reloads.
  project.open = false;
  project.ontoggle();
  await run("reload()");
  assert.equal(get("notes").children[0].open, false);
  assert.equal(get("notes").children[1].open, true);

  // Empty notes are omitted; with none the panel is hidden.
  context.shownNotes = {workstream: notes.workstream};
  await run("reload()");
  assert.equal(get("notes").children.map(d => d.dataset.kind).join(), "workstream");
  context.shownNotes = {};
  await run("reload()");
  assert.equal(get("notes").hidden, true);

  // A failed notes read hides notes but keeps the board.
  context.shownNotes = notes;
  context.failNotes = true;
  await run("reload()");
  assert.equal(get("notes").hidden, true);
  assert.equal(run("state.rows.length"), 1);

  // Group views show no notes and do not request them.
  context.failNotes = false;
  context.calls = [];
  run(`state.groups = "project"; pages = async () => [];`);
  await run("reload()");
  assert.equal(get("notes").hidden, true);
  assert.equal(context.calls.some(([a]) => a === "notes"), false);
  assert.equal(context.toasts.length, 0, context.toasts.join("; "));
  console.log("viewer notes ok");
}
test().catch(error => { console.error(error); process.exit(1); });
