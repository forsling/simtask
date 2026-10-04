// Execute the shipped location URL handling against a small history/location double:
// token stripping, push/replace per navigation, back/forward and stale fallbacks.
// Real-browser behavior (reload, bookmarks, address bar edits) is checked separately.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");

function element() {
  const classes = new Set();
  return {textContent: "", value: "", dataset: {}, style: {}, children: [],
    classList: {add(c) {classes.add(c);}, remove(...c) {c.forEach(x => classes.delete(x));}, toggle() {}, contains(c) {return classes.has(c);}},
    append(...children) {this.children.push(...children);}, prepend() {}, setAttribute() {},
    replaceChildren(...children) {this.children = children;}, scrollIntoView() {}};
}
const projects = [{id: "p1", name: "One"}, {id: "p2", name: "Two"}];
const streams = {p1: [{id: "w1", project_id: "p1", branch: "A"}, {id: "w2", project_id: "p1", branch: "B"}], p2: [{id: "w3", project_id: "p2", branch: "M"}]};
const tasks = {w1: ["t1", "t2"], w2: ["t3", "t4"], w3: ["t5"], p1: ["t1", "t2", "t3", "t4", "t6"], p2: ["t5"]};
const groups = {p1: ["g1", "g2"], p2: []};

function viewer(initialHash, stored = "") {
  const entries = [initialHash], listeners = {}, notices = [], calls = [];
  let index = 0, reloaded = 0, storedToken = stored;
  const hashOf = url => (String(url).includes("#") ? "#" + String(url).split("#")[1] : "");
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {getElementById: get, createElement: element, createElementNS: () => element(), addEventListener() {}, querySelectorAll() {return [];}},
    window: {addEventListener(type, fn) {(listeners[type] ||= []).push(fn);}},
    setTimeout() {},
    location: {get hash() {return entries[index];}, reload() {reloaded++;}},
    sessionStorage: {getItem() {return storedToken;}, setItem(k, v) {storedToken = v;}},
    history: {
      pushState(_, __, url) {entries.splice(index + 1); entries.push(hashOf(url)); index++;},
      replaceState(_, __, url) {entries[index] = hashOf(url);},
    },
  });
  vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
  const run = code => vm.runInContext(code, context);
  context.apiMock = async (action, data) => {
    calls.push(action);
    if (action === "projects") return {items: projects, next_offset: null};
    if (action === "workstreams") return {items: streams[data.project] || [], next_offset: null};
    if (action === "tasks") {
      const ids = tasks[data.workstream_id || data.project] || [];
      return {items: ids.map(id => ({id, title: id, view: "ready"})), next_offset: null, workstream_order_revision: data.workstream_id ? 1 : null};
    }
    if (action === "groups") return {items: (groups[data.project] || []).map(id => ({id, title: id, object_type: "group", progress: {by_project: {p1: {}}}})), next_offset: null};
    if (action === "details") {
      const id = data.ids[0];
      if (!/^[tg]\d$/.test(id)) throw new Error("not_found");
      return {items: [{id, object_type: id[0] === "g" ? "group" : "task"}]};
    }
    throw new Error("unexpected " + action);
  };
  run('api = apiMock; renderDetail = () => {}; includedWorkstreams = async () => []; toast = (m) => noticeSink(m);');
  context.noticeSink = m => notices.push(m);
  const fire = async (...types) => {
    for (const type of types) for (const fn of listeners[type] || []) fn();
    await settle();
  };
  const settle = () => new Promise(resolve => setTimeout(resolve, 0)).then(() => new Promise(r => setImmediate(r)));
  return {
    run, entries, notices, calls, settle,
    token: () => storedToken,
    reloads: () => reloaded,
    hash: () => entries[index],
    count: () => entries.length,
    at: () => ({stream: run("state.stream"), groups: run("state.groups"), selected: run("state.selected"), project: run("state.project")}),
    // Browser traversal fires popstate and hashchange; the viewer must open it once.
    back: () => { index--; return fire("popstate", "hashchange"); },
    forward: () => { index++; return fire("popstate", "hashchange"); },
    edit: hash => { entries.splice(index + 1); entries.push(hash); index++; return fire("popstate", "hashchange"); },
  };
}

async function main() {
  // The launch link's token is stored and stripped before any request; the
  // default location then replaces the address without a new history entry.
  const v = viewer("#launch-token-123");
  assert.equal(v.token(), "launch-token-123");
  assert.equal(v.hash(), "");
  await v.run("boot()");
  assert.equal(v.hash(), "#/project/p1/workstream/w1/task/t1");
  assert.equal(v.count(), 1);
  assert.equal(v.run("token"), "launch-token-123");

  // Deliberate navigation pushes one entry; the automatic first-row selection replaces it.
  await v.run('changeScope("w2")');
  assert.equal(v.hash(), "#/project/p1/workstream/w2/task/t3");
  assert.equal(v.count(), 2);
  await v.run('selectTask("t4", {open: true, entry: "push"})');
  assert.equal(v.hash(), "#/project/p1/workstream/w2/task/t4");
  assert.equal(v.count(), 3);
  // Keyboard moves update the address in place.
  await v.run("moveSelection(-1)");
  assert.equal(v.hash(), "#/project/p1/workstream/w2/task/t3");
  assert.equal(v.count(), 3);
  await v.run('changeScope(null)');
  assert.equal(v.hash(), "#/project/p1/all/task/t1");
  await v.run('chooseGroups("project")');
  assert.equal(v.hash(), "#/project/p1/groups/group/g1");
  await v.run('chooseGroups("shared")');
  assert.equal(v.hash(), "#/project/p1/shared-groups");
  await v.run('chooseProject("p2")');
  assert.equal(v.hash(), "#/project/p2/workstream/w3/task/t5");
  assert.equal(v.count(), 7);

  // Back/forward reopen each visited location without adding entries.
  await v.back();
  assert.deepEqual(v.at(), {stream: null, groups: "shared", selected: null, project: "p1"});
  await v.back();
  assert.deepEqual(v.at(), {stream: null, groups: "project", selected: "g1", project: "p1"});
  await v.back();
  await v.back();
  assert.deepEqual(v.at(), {stream: "w2", groups: false, selected: "t3", project: "p1"});
  assert.equal(v.hash(), "#/project/p1/workstream/w2/task/t3");
  await v.forward();
  assert.deepEqual(v.at(), {stream: null, groups: false, selected: "t1", project: "p1"});
  assert.equal(v.count(), 7);
  assert.deepEqual(v.notices, []);

  // An edited address opens once, even though both popstate and hashchange fire.
  const before = v.calls.filter(c => c === "workstreams").length;
  await v.edit("#/project/p1/workstream/w1/task/t2");
  assert.deepEqual(v.at(), {stream: "w1", groups: false, selected: "t2", project: "p1"});
  assert.equal(v.calls.filter(c => c === "workstreams").length - before, 2); // openLocation + board reload

  // Stale or unknown locations fall back to the nearest valid view with a notice.
  const stale = async (hash, expected, canonical, notice) => {
    v.notices.length = 0;
    await v.edit(hash);
    assert.deepEqual(v.at(), expected, hash);
    assert.equal(v.hash(), canonical, hash);
    assert.equal(v.notices.length, 1, hash);
    assert.match(v.notices[0], notice, hash);
  };
  await stale("#/project/p1/workstream/gone/task/t2", {stream: null, groups: false, selected: "t2", project: "p1"},
    "#/project/p1/all/task/t2", /workstream no longer exists\. Showing One · All tasks/);
  await stale("#/project/p1/workstream/w2/task/t1", {stream: "w2", groups: false, selected: "t3", project: "p1"},
    "#/project/p1/workstream/w2/task/t3", /not in B\. Showing the first task/);
  await stale("#/project/gone/all/task/t5", {stream: "w1", groups: false, selected: "t1", project: "p1"},
    "#/project/p1/workstream/w1/task/t1", /project no longer exists\. Showing One · A/);
  await stale("#/project/p1/groups/group/zzz", {stream: null, groups: "project", selected: "g1", project: "p1"},
    "#/project/p1/groups/group/g1", /group no longer exists/);
  await stale("#/project/p1/all/task/t9", {stream: null, groups: false, selected: "t1", project: "p1"},
    "#/project/p1/all/task/t1", /not in this project/);
  await stale("#/elsewhere", {stream: "w1", groups: false, selected: "t1", project: "p1"},
    "#/project/p1/workstream/w1/task/t1", /not a viewer location/);
  await stale("#/project/p1/all/group/g1", {stream: "w1", groups: false, selected: "t1", project: "p1"},
    "#/project/p1/workstream/w1/task/t1", /not a viewer location/);
  // A linked group outside the list (opened from a task) survives as a location.
  v.notices.length = 0;
  await v.edit("#/project/p2/groups/group/g2");
  assert.deepEqual(v.at(), {stream: null, groups: "project", selected: "g2", project: "p2"});
  assert.equal(v.run("state.linkedGroup"), "g2");
  assert.deepEqual(v.notices, []);

  // A launch link pasted into this tab reloads so startup adopts and strips its token.
  await v.edit("#new-launch-token");
  assert.equal(v.reloads(), 1);
  assert.ok(v.entries.every(h => !h.includes("launch-token-123")));

  // A bookmarked location in a tab holding the token opens directly, without a new entry.
  const b = viewer("#/project/p2/workstream/w3/task/t5", "stored-token");
  assert.equal(b.hash(), "#/project/p2/workstream/w3/task/t5");
  await b.run("boot()");
  assert.deepEqual(b.at(), {stream: "w3", groups: false, selected: "t5", project: "p2"});
  assert.equal(b.count(), 1);
  assert.equal(b.run("token"), "stored-token");
  assert.deepEqual(b.notices, []);
  console.log("viewer route tests passed");
}
main().catch(error => {
  console.error(error);
  process.exit(1);
});
