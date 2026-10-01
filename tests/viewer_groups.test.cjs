// Execute the shipped navigation and group rendering against mixed group data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
function element() {
  const classes = new Set();
  return {textContent: "", value: "", dataset: {}, style: {}, children: [],
    classList: {add(c) {classes.add(c);}, remove(c) {classes.delete(c);}, toggle() {}, contains(c) {return classes.has(c);}},
    append(...children) {this.children.push(...children);}, setAttribute() {},
    replaceChildren(...children) {this.children = children;}, scrollIntoView() {}};
}
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, element());
  return elements.get(id);
};
const context = vm.createContext({
  document: {getElementById: get, createElement: element, addEventListener() {}, querySelectorAll() {return [];}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem() {return "";}}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
const run = code => vm.runInContext(code, context);
run(source.replace(/boot\(\);\s*$/, ""));
run('icon = () => node("i"); activity = () => null; markdown = (s) => node("p", s); toast = (message) => {throw Error(message);};');
function text(n) {
  return typeof n === "string" ? n : [n?.textContent || "", ...(n?.children || []).map(text)].join(" ");
}
function descendants(n) {
  return [n, ...(n.children || []).filter(c => typeof c === "object").flatMap(descendants)];
}
const group = (id, projects) => ({id, title: id, object_type: "group", body: "", acceptance_criteria: "",
  complete: false, progress: {total: projects.length, done: 0,
    by_project: Object.fromEntries(projects.map(p => [p, {total: 1, done: 0}]))}, member_details: []});
const groups = [group("local", ["p1"]), group("remote", ["p2"]),
  group("shared", ["p1", "p2"]), group("empty-local", []), group("empty-remote", [])];
const streams = [
  {id: "w1", project_id: "p1", project_name: "First", branch: "main", name: "main", checkout_path: "/first", groups: ["shared", "local"]},
  {id: "w2", project_id: "p2", project_name: "Second", branch: null, name: "Release", checkout_path: "/second", groups: ["shared", "empty-remote"]},
  {id: "member-only", project_id: "p1", project_name: "First", branch: "member-only", groups: [], scope: ["shared-member"]},
];
const requests = [];
context.apiMock = async (action, data) => {
  requests.push({action, ...data});
  if (action === "groups") {
    const items = groups.filter(g => !data.project || g.progress.by_project[data.project] ||
      g.id === (data.project === "p1" ? "empty-local" : "empty-remote"));
    // Deliberately paginate before filtering: shared groups must survive later pages.
    return {items: items.slice(data.offset, data.offset + 2), next_offset: data.offset + 2 < items.length ? data.offset + 2 : null};
  }
  if (action === "workstreams") {
    const items = streams.filter(w => !data.project || w.project_id === data.project);
    return {items: items.slice(data.offset, data.offset + 1), next_offset: data.offset + 1 < items.length ? data.offset + 1 : null};
  }
  if (action === "tasks") return {items: [], next_offset: null};
  if (action === "details") return {items: [groups.find(g => g.id === data.ids[0])]};
  throw Error(action);
};
run('api = apiMock; state.project = "p1"; state.projects = [{id: "p1", name: "First"}, {id: "p2", name: "Second"}];');
const rowIds = () => JSON.parse(run('JSON.stringify(state.rows.map(g => g.id))'));
async function main() {
  await run('chooseGroups("shared")');
  assert.deepEqual(rowIds(), ["shared"]);
  assert.match(text(get("heading")), /Shared task groups/);
  assert.match(text(get("list")), /Shared group · 2 projects/);
  assert.ok(requests.filter(r => r.action === "groups").every(r => !r.project));
  await run('chooseGroups("project")');
  assert.deepEqual(rowIds(), ["local", "shared", "empty-local"]);
  assert.equal(text(get("heading")).trim(), "Task groups");
  assert.match(text(get("subheading")), /First · 3 groups/);
  assert.match(text(get("list")), /Project group · 1 project/);
  assert.match(text(get("list")), /Shared group · 2 projects/);
  assert.match(text(get("list")), /Empty group · 0 projects/);
  assert.doesNotMatch(text(get("nav")), /All groups/);
  const navButtons = descendants(get("nav")).filter(n => n.onclick);
  assert.ok(navButtons.find(n => text(n).trim() === "Task groups"));
  await navButtons.find(n => text(n).trim() === "Shared task groups").onclick();
  assert.deepEqual(rowIds(), ["shared"]);
  await run('openGroup("local")');
  assert.equal(run('state.groups'), "project");
  assert.equal(run('state.selected'), "local");
  assert.match(text(get("detail")), /Project group/);
  await run('selectTask("shared")');
  assert.match(text(get("detail")), /Shared group/);
  assert.match(text(get("detail")), /Included in workstreams/);
  assert.match(text(get("detail")), /First.*main/);
  assert.match(text(get("detail")), /Second.*Release/);
  assert.doesNotMatch(text(get("detail")), /member-only/);
  const streamLink = descendants(get("detail")).find(n => n.onclick && /Second.*Release/.test(text(n)));
  await streamLink.onclick();
  assert.equal(run('state.project'), "p2");
  assert.equal(run('state.stream'), "w2");
  assert.equal(run('state.groups'), false);
  assert.ok(requests.some(r => r.action === "tasks" && r.project === "p2" && r.workstream_id === "w2"));
  await run('chooseProject("p1")');
  // Foreign prerequisites need not be discoverable from the dependent project.
  await run('openGroup("remote")');
  assert.equal(run('state.project'), "p2");
  assert.equal(run('state.selected'), "remote");
  assert.match(text(get("detail")), /remote/);
  await run('openGroup("shared")');
  assert.equal(run('state.groups'), "shared");
  assert.equal(run('state.selected'), "shared");
  await run('chooseProject("p1")');
  await run('openGroup("empty-remote")');
  assert.equal(run('state.selected'), "empty-remote");
  assert.match(text(get("detail")), /Opened from a task link/);
  await run('reload({quiet: true})');
  assert.equal(run('state.selected'), "empty-remote", "Refresh preserves linked empty group");
  assert.match(text(get("detail")), /Second.*Release/, "Empty groups can have workstream references");
  // An older foreign-group link cannot override a newer navigation choice.
  let release, began;
  const held = new Promise(resolve => {release = resolve;});
  const started = new Promise(resolve => {began = resolve;});
  context.delayedApi = async (action, data) => {
    if (action === "workstreams" && data.project === "p2") {began(); await held;}
    return context.apiMock(action, data);
  };
  run('api = delayedApi;');
  const pending = run('openGroup("remote")');
  await started;
  await run('chooseGroups("shared")');
  release();
  await pending;
  assert.equal(run('state.groups'), "shared");
  assert.equal(run('state.selected'), "shared");
  assert.equal(run('state.project'), "p1");
  run('api = apiMock;');
  // Workstream links also cancel their delayed project lookup on newer navigation.
  let releaseLink, beganLink;
  const heldLink = new Promise(resolve => {releaseLink = resolve;});
  const startedLink = new Promise(resolve => {beganLink = resolve;});
  context.delayedLinkApi = async (action, data) => {
    if (action === "workstreams" && data.project === "p2") {beganLink(); await heldLink;}
    return context.apiMock(action, data);
  };
  context.targetStream = streams[1];
  run('api = delayedLinkApi;');
  const pendingLink = run('navigateWorkstream(targetStream)');
  await startedLink;
  await run('chooseGroups("shared")');
  releaseLink();
  await pendingLink;
  assert.equal(run('state.groups'), "shared");
  assert.equal(run('state.project'), "p1");
  // Selecting another group row also supersedes a pending workstream link.
  let releaseRow, beganRow;
  const heldRow = new Promise(resolve => {releaseRow = resolve;});
  const startedRow = new Promise(resolve => {beganRow = resolve;});
  context.delayedRowApi = async (action, data) => {
    if (action === "workstreams" && data.project === "p2") {beganRow(); await heldRow;}
    return context.apiMock(action, data);
  };
  run('api = delayedRowApi;');
  const pendingRow = run('navigateWorkstream(targetStream)');
  await startedRow;
  await run('selectTask("local")');
  releaseRow();
  await pendingRow;
  assert.equal(run('state.selected'), "local");
  assert.equal(run('state.project'), "p1");
  assert.match(text(get("detail")), /local/);
  run('api = apiMock;');
  await run('chooseProject("p2")');
  assert.equal(run('state.groups'), false);
  await run('chooseGroups("project")');
  assert.deepEqual(rowIds(), ["remote", "shared", "empty-remote"]);
  assert.match(text(get("detail")), /Not included as a group in any workstream/);
  run('$("search").value = "shared"; renderList();');
  assert.doesNotMatch(text(get("list")), /remote/);
  run('$("search").value = "";');
  groups.splice(2, 1); // No cross-project members remain.
  await run('chooseGroups("shared")');
  assert.deepEqual(rowIds(), []);
  assert.match(text(get("detail")), /No shared task groups/);
  assert.match(text(get("detail")), /more than one project/);
  // A canceled detail request must not keep the empty destination dimmed.
  let releaseEmpty, beganEmpty;
  const heldEmpty = new Promise(resolve => {releaseEmpty = resolve;});
  const startedEmpty = new Promise(resolve => {beganEmpty = resolve;});
  context.delayedEmptyApi = async (action, data) => {
    if (action === "workstreams" && !data.project) {beganEmpty(); await heldEmpty;}
    return context.apiMock(action, data);
  };
  run('api = delayedEmptyApi;');
  const pendingEmpty = run('selectTask("remote")');
  await startedEmpty;
  assert.ok(get("detail").classList.contains("loading"));
  await run('chooseGroups("shared")');
  assert.equal(get("detail").classList.contains("loading"), false);
  releaseEmpty();
  await pendingEmpty;
  assert.equal(get("detail").classList.contains("loading"), false);
  assert.match(text(get("detail")), /No shared task groups/);
}
main().catch(e => {console.error(e); process.exitCode = 1;});
