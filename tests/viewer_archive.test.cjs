// Execute the shipped navigation with an archived workstream: hidden from the sidebar
// and never the default view, shown on request, still reachable by link with its archive
// reason, and left out of the add-to-workstream picker. Layout is checked in a browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const roots = new Map();
function element(tag = "div") {
  const classes = new Set();
  return {tag, children: [], textContent: "", value: "", hidden: false, dataset: {}, style: {}, attributes: {},
    classList: {add(c) { classes.add(c); }, remove(c) { classes.delete(c); }, toggle() {}, contains(c) { return classes.has(c); }},
    append(...children) { this.children.push(...children); },
    prepend() {}, replaceChildren(...children) { this.children = children; },
    setAttribute(k, v) { this.attributes[k] = v; }, addEventListener(type, fn) { this["on" + type] = fn; },
    querySelector() { return null; }, scrollIntoView() {}};
}
const get = id => roots.get(id) || roots.set(id, element()).get(id);
const stored = new Map();
const context = vm.createContext({
  document: {getElementById: get, createElement: element, createElementNS: () => element(),
    addEventListener() {}, querySelectorAll() { return []; }},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: k => stored.get(k) ?? null, setItem: (k, v) => stored.set(k, v)},
  history: {replaceState() {}, pushState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
const text = node => typeof node === "string" ? node : [node.textContent, ...(node.children || []).map(text)].join("");
const streams = [
  {id: "old", project_id: "p", branch: "old-branch", status: {scoped_count: 2}, archive: {archived: true, reason: "Inactive snapshot", revision: 1}},
  {id: "main", project_id: "p", branch: "main", status: {scoped_count: 1}},
  {id: "back", project_id: "p", branch: "back", status: {scoped_count: 0}, archive: {archived: false, reason: "In use", revision: 2}},
];
context.streams = streams;
context.calls = [];
const navLabels = () => {
  const sub = get("nav").children.find(c => c.className === "nav-sub");
  return sub.children.slice(2).map(text);
};
async function test() {
  run(`state.projects = [{id: "p", name: "Project"}];
    renderDetail = () => {}; selectTask = async () => {}; toast = (m) => toasts.push(m);
    api = async (action, data) => {
      calls.push([action, data]);
      if (action === "workstreams") return {items: data.include_archived ? streams : streams.filter(s => !s.archive?.archived), next_offset: null};
      if (action === "tasks") return {items: [{id: "t", title: "T", view: "ready"}], next_offset: null, workstream_order_revision: 1};
      if (action === "notes") return {notes: {}};
      if (action === "next-action") return {action: null, diagnostics: {}};
      throw new Error("unexpected " + action);
    };`);
  context.toasts = [];
  // Opening the project never defaults to an archived workstream, and every workstream
  // read includes archived ones so names and links resolve.
  await run("chooseProject('p')");
  assert.equal(run("state.stream"), "main");
  assert.ok(context.calls.filter(([a]) => a === "workstreams").every(([, d]) => d.include_archived === true));
  assert.deepEqual(navLabels(), ["main1", "back0", "Show 1 archived"]);
  // Showing archived workstreams lists them, marked, and is remembered for this tab.
  const toggle = () => get("nav").children.find(c => c.className === "nav-sub").children.at(-1);
  toggle().onclick();
  assert.deepEqual(navLabels(), ["old-brancharchived", "main1", "back0", "Hide archived"]);
  assert.equal(toggle().attributes["aria-pressed"], "true");
  assert.equal(stored.get("task-viewer-show-archived"), "1");
  toggle().onclick();
  assert.deepEqual(navLabels(), ["main1", "back0", "Show 1 archived"]);
  // A linked archived workstream still opens, stays visible while open and shows why.
  await run("openLocation('#/project/p/workstream/old')");
  assert.equal(run("state.stream"), "old");
  assert.deepEqual(navLabels(), ["old-brancharchived", "main1", "back0", "Show 1 archived"]);
  const tag = get("heading").children.find(c => c.className === "tag-archived");
  assert.equal(tag.textContent, "Archived");
  assert.equal(tag.title, "Archived: Inactive snapshot");
  assert.equal(context.toasts.length, 0, context.toasts.join("; "));
  // Its next agent action is requested explicitly, as the user opened it.
  const next = get("subheading").children.find(c => c.tag === "button");
  context.calls = [];
  await next.onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(context.calls.find(([a]) => a === "next-action")[1])), {workstream_id: "old", include_archived: true});
  get("subheading").children = []; // Setting textContent replaces children in a browser.
  await run("changeScope('main')");
  assert.doesNotMatch(text(get("heading")), /Archived/);
  context.calls = [];
  await get("subheading").children.find(c => c.tag === "button").onclick();
  assert.equal(context.calls.find(([a]) => a === "next-action")[1].include_archived, false);

  // The add picker leaves archived workstreams out; the remove picker keeps memberships.
  run(`openDialog = () => {}; placementAction = () => {}; submissionPending = false;`);
  await run(`membershipDialog({id: "t", title: "T", project_id: "p", workstream_ids: []}, true)`);
  const options = () => get("fields").children.at(-1).children[1].children.map(o => o.value);
  assert.deepEqual(options(), ["main", "back"]);
  get("fields").children = [];
  await run(`membershipDialog({id: "t", title: "T", project_id: "p", workstream_ids: ["old", "main"]}, false)`);
  assert.deepEqual(options(), ["old", "main"]);
  assert.match(text(get("fields").children.at(-1).children[1].children[0]), /\(archived\)/);
  console.log("viewer archive ok");
}
test().catch(error => { console.error(error); process.exit(1); });
