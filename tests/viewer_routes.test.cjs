// Execute the shipped location URL handling against a small history/location double:
// short path routes, token stripping, push/replace per navigation, back/forward, prefix
// ambiguity and stale fallbacks. Real-browser behavior (reload, bookmarks, address bar
// edits, the server serving these paths) is checked separately.
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
// IDs are a type prefix and 32 hex characters; URLs show the first 8 unless ambiguous.
const id = (kind, head, tail = "0") => `${kind}_${head}${tail.repeat(24)}`;
const P1 = id("prj", "11111111"), P2 = id("prj", "22222222");
const W1 = id("wst", "a1a1a1a1"), W2 = id("wst", "b2b2b2b2"), W3 = id("wst", "c3c3c3c3");
// Two workstreams in different projects share a short prefix: only the server sees both.
const W4 = id("wst", "dddddddd", "1"), W5 = id("wst", "dddddddd", "2");
const [T1, T2, T3, T4, T5, T6] = ["10000001", "10000002", "10000003", "10000004", "10000005", "10000006"].map(h => id("tsk", h));
// Two tasks in one board share a short prefix.
const T7 = id("tsk", "77777777", "1"), T8 = id("tsk", "77777777", "2");
const G1 = id("tsk", "e1e1e1e1"), G2 = id("tsk", "e2e2e2e2"), G3 = id("tsk", "e3e3e3e3");
const G4 = id("tsk", "eeeeeeee", "1"), G5 = id("tsk", "eeeeeeee", "2");
const hex = x => x.slice(4), short = x => x.slice(4, 12);

const projects = [{id: P1, name: "One"}, {id: P2, name: "Two"}];
const streams = {
  [P1]: [{id: W1, project_id: P1, branch: "A"}, {id: W2, project_id: P1, branch: "B"}, {id: W5, project_id: P1, branch: "D"}],
  [P2]: [{id: W3, project_id: P2, branch: "M"}, {id: W4, project_id: P2, branch: "N"}],
};
const tasks = {[W1]: [T1, T2], [W2]: [T3, T4, T7, T8], [W3]: [T5], [W4]: [], [W5]: [T6], [P1]: [T1, T2, T3, T4, T6, T7, T8], [P2]: [T5]};
// Group homes: G1 lives in One, G2 spans both projects, G3 is empty and originates in Two.
const groupInfo = {
  [G1]: {origin: P1, by: [P1]}, [G2]: {origin: P1, by: [P1, P2]}, [G3]: {origin: P2, by: []},
  [G4]: {origin: P1, by: [P1]}, [G5]: {origin: P2, by: [P2]},
};
const groupLists = {[P1]: [G1, G2, G4], [P2]: [G2, G3, G5]};
const allStreams = Object.values(streams).flat();

function viewer(initial, stored = "") {
  const entries = [{...initial}], listeners = {}, notices = [], calls = [];
  let index = 0, reloaded = 0, storedToken = stored;
  const elements = new Map();
  const get = name => {
    if (!elements.has(name)) elements.set(name, element());
    return elements.get(name);
  };
  const urlOf = url => {
    const [p, h] = String(url).split("#");
    return {path: p, hash: h === undefined ? "" : "#" + h};
  };
  const context = vm.createContext({
    document: {getElementById: get, createElement: element, createElementNS: () => element(), addEventListener() {}, querySelectorAll() {return [];}},
    window: {addEventListener(type, fn) {(listeners[type] ||= []).push(fn);}},
    setTimeout() {},
    location: {get pathname() {return entries[index].path;}, get hash() {return entries[index].hash;}, reload() {reloaded++;}},
    sessionStorage: {getItem() {return storedToken;}, setItem(k, v) {storedToken = v;}},
    history: {
      pushState(_, __, url) {entries.splice(index + 1); entries.push(urlOf(url)); index++;},
      replaceState(_, __, url) {entries[index] = urlOf(url);},
    },
  });
  vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
  const run = code => vm.runInContext(code, context);
  context.apiMock = async (action, data) => {
    calls.push([action, data]);
    if (action === "projects") return {items: projects, next_offset: null};
    if (action === "workstreams") return {items: streams[data.project] || [], next_offset: null};
    if (action === "tasks") {
      const ids = tasks[data.workstream_id || data.project] || [];
      return {items: ids.map(t => ({id: t, title: t, view: "ready"})), next_offset: null, workstream_order_revision: data.workstream_id ? 1 : null};
    }
    if (action === "groups") {
      const ids = data.project ? groupLists[data.project] : Object.keys(groupInfo);
      return {items: ids.map(g => ({id: g, title: g, object_type: "group", project_id: null, project_count: groupInfo[g].by.length})), next_offset: null};
    }
    if (action === "resolve-prefix") {
      const pool = data.kind === "workstream" ? allStreams.map(s => [s.id, s.project_id]) : Object.entries(groupInfo).map(([g, i]) => [g, i.origin]);
      const exact = pool.filter(([x]) => x === data.prefix);
      const items = (exact.length ? exact : pool.filter(([x]) => /^[a-z]+_[0-9a-f]{32}$/.test(x) && hex(x).startsWith(data.prefix))).sort().slice(0, 2).map(([x, p]) => ({id: x, project_id: p}));
      return {items};
    }
    if (action === "details") {
      const x = data.ids[0];
      if (groupInfo[x]) return {items: [{id: x, object_type: "group", project_id: null, origin_project_id: groupInfo[x].origin,
        progress: {by_project: Object.fromEntries(groupInfo[x].by.map(p => [p, {}]))}}]};
      if (Object.values(tasks).flat().includes(x)) return {items: [{id: x, object_type: "task"}]};
      throw new Error("unknown_task");
    }
    throw new Error("unexpected " + action);
  };
  run("api = apiMock; renderDetail = () => {}; includedWorkstreams = async () => []; toast = (m) => noticeSink(m);");
  context.noticeSink = m => notices.push(m);
  const settle = async () => {
    for (let i = 0; i < 4; i++) await new Promise(r => setImmediate(r));
  };
  const fire = async (...types) => {
    for (const type of types) for (const fn of listeners[type] || []) fn();
    await settle();
  };
  return {
    run: async code => { const result = await run(code); await settle(); return result; },
    entries, notices, calls,
    token: () => storedToken,
    reloads: () => reloaded,
    path: () => entries[index].path,
    count: () => entries.length,
    at: () => ({project: run("state.project"), stream: run("state.stream"), groups: run("state.groups"), selected: run("state.selected")}),
    // Browser traversal fires popstate (and hashchange only when the fragment changes).
    back: () => { index--; return fire("popstate"); },
    forward: () => { index++; return fire("popstate"); },
    edit: (p, hash = "") => { entries.splice(index + 1); entries.push({path: p, hash}); index++; return fire(hash ? "hashchange" : "popstate"); },
  };
}

async function main() {
  // The launch link's token is stored and stripped before any request; the default
  // location then replaces the address without a new history entry.
  const v = viewer({path: "/", hash: "#launch-token-123"});
  assert.equal(v.token(), "launch-token-123");
  assert.deepEqual(v.entries[0], {path: "/", hash: ""});
  await v.run("boot()");
  assert.equal(v.path(), `/w/a1a1a1a1/t/${short(T1)}`);
  assert.equal(v.count(), 1);
  assert.equal(v.run && (await v.run("token")), "launch-token-123");

  // Deliberate navigation pushes one entry; the automatic first-row selection replaces it.
  await v.run(`changeScope("${W2}")`);
  assert.equal(v.path(), `/w/b2b2b2b2/t/${short(T3)}`);
  assert.equal(v.count(), 2);
  // A task whose short prefix is shared within the board is spelled in full.
  await v.run(`selectTask("${T7}", {open: true, entry: "push"})`);
  assert.equal(v.path(), `/w/b2b2b2b2/t/${hex(T7)}`);
  await v.run(`selectTask("${T4}", {open: true, entry: "push"})`);
  assert.equal(v.path(), `/w/b2b2b2b2/t/${short(T4)}`);
  assert.equal(v.count(), 4);
  // Keyboard moves update the address in place.
  await v.run("moveSelection(-1)");
  assert.equal(v.path(), `/w/b2b2b2b2/t/${short(T3)}`);
  assert.equal(v.count(), 4);
  // Project-wide views keep the project segment.
  await v.run("changeScope(null)");
  assert.equal(v.path(), `/p/11111111/t/${short(T1)}`);
  // A group that lives in this project needs no project segment once its home is known.
  await v.run('chooseGroups("project")');
  assert.equal(v.path(), `/g/${short(G1)}`);
  // A shared group shown in a project's list keeps that project; in Shared groups it does not.
  await v.run(`selectTask("${G2}", {open: true, entry: "push"})`);
  assert.equal(v.path(), `/p/11111111/g/${short(G2)}`);
  await v.run('chooseGroups("shared")');
  assert.equal(v.path(), `/g/${short(G2)}`);
  await v.run(`chooseProject("${P2}")`);
  assert.equal(v.path(), `/w/c3c3c3c3/t/${short(T5)}`);
  const visited = v.count();
  assert.equal(visited, 9);

  // Back/forward reopen each visited location without adding entries.
  await v.back();
  assert.deepEqual(v.at(), {project: P2, stream: null, groups: "shared", selected: G2});
  await v.back();
  assert.deepEqual(v.at(), {project: P1, stream: null, groups: "project", selected: G2});
  await v.back();
  assert.deepEqual(v.at(), {project: P1, stream: null, groups: "project", selected: G1});
  await v.back();
  assert.deepEqual(v.at(), {project: P1, stream: null, groups: false, selected: T1});
  await v.back();
  await v.back();
  assert.deepEqual(v.at(), {project: P1, stream: W2, groups: false, selected: T7});
  assert.equal(v.path(), `/w/b2b2b2b2/t/${hex(T7)}`);
  await v.forward();
  assert.deepEqual(v.at(), {project: P1, stream: W2, groups: false, selected: T3});
  assert.equal(v.count(), visited);
  assert.deepEqual(v.notices, []);

  // Opening a task location reads the workstream prefix and that workstream's board only;
  // the sidebar's counts read each other workstream's cards once, beside the board.
  v.calls.length = 0;
  await v.edit(`/w/a1a1a1a1/t/${short(T2)}`);
  assert.deepEqual(v.at(), {project: P1, stream: W1, groups: false, selected: T2});
  const counted = v.calls.filter(([a, d]) => a === "tasks" && d.workstream_id !== W1);
  assert.deepEqual(v.calls.filter((c) => !counted.includes(c)).map(([a]) => a),
    ["resolve-prefix", "workstreams", "tasks", "workstreams", "details"]);
  assert.deepEqual(counted.map(([, d]) => d.workstream_id), [W2, W5]);

  // A workstream whose short prefix another project's workstream shares is respelled in
  // full once the server reports the ambiguity; it still resolves from that address.
  await v.run(`chooseProject("${P1}")`);
  await v.run(`changeScope("${W5}")`);
  assert.equal(v.path(), `/w/${hex(W5)}/t/${short(T6)}`);
  await v.edit(`/w/${hex(W2)}`);
  assert.deepEqual(v.at(), {project: P1, stream: W2, groups: false, selected: T3});
  // A full or shorter prefix that is unambiguous is rewritten to the short form.
  assert.equal(v.path(), `/w/b2b2b2b2/t/${short(T3)}`);
  await v.edit("/w/a1a1");
  assert.equal(v.path(), `/w/a1a1a1a1/t/${short(T1)}`);
  // An empty group implies the project it originates in.
  await v.edit(`/g/${short(G3)}`);
  assert.deepEqual(v.at(), {project: P2, stream: null, groups: "project", selected: G3});
  assert.equal(v.path(), `/g/${short(G3)}`);
  assert.deepEqual(v.notices, []);

  // Stale, ambiguous or unknown locations fall back to the nearest valid view with a notice.
  const stale = async (address, expected, canonical, notice) => {
    v.notices.length = 0;
    await v.edit(address);
    assert.deepEqual(v.at(), expected, address);
    assert.equal(v.path(), canonical, address);
    assert.equal(v.notices.length, 1, `${address}: ${v.notices}`);
    assert.match(v.notices[0], notice, address);
  };
  await v.run(`chooseProject("${P1}")`);
  await stale(`/w/99999999/t/${short(T2)}`, {project: P1, stream: null, groups: false, selected: T2},
    `/p/11111111/t/${short(T2)}`, /workstream no longer exists\. Showing One · All tasks/);
  await stale("/w/dddddddd", {project: P1, stream: null, groups: false, selected: T1},
    `/p/11111111/t/${short(T1)}`, /matches more than one workstream\. Showing One · All tasks/);
  await stale(`/w/b2b2b2b2/t/${short(T1)}`, {project: P1, stream: W2, groups: false, selected: T3},
    `/w/b2b2b2b2/t/${short(T3)}`, /not in B\. Showing the first task/);
  await stale("/w/b2b2b2b2/t/77777777", {project: P1, stream: W2, groups: false, selected: T3},
    `/w/b2b2b2b2/t/${short(T3)}`, /matches more than one task in B\. Showing the first task/);
  await stale(`/p/99999999/t/${short(T5)}`, {project: P1, stream: W1, groups: false, selected: T1},
    `/w/a1a1a1a1/t/${short(T1)}`, /project no longer exists\. Showing One · A/);
  await stale("/p/11111111/g/99999999", {project: P1, stream: null, groups: "project", selected: G1},
    `/g/${short(G1)}`, /group no longer exists\. Showing One task groups/);
  await stale("/g/eeeeeeee", {project: P1, stream: null, groups: "project", selected: G1},
    `/g/${short(G1)}`, /matches more than one task group/);
  await stale("/sg/99999999", {project: P1, stream: null, groups: "shared", selected: G2},
    `/g/${short(G2)}`, /group no longer exists\. Showing Shared task groups/);
  await stale(`/p/11111111/t/${short(T5)}`, {project: P1, stream: null, groups: false, selected: T1},
    `/p/11111111/t/${short(T1)}`, /not in this project/);
  await stale("/elsewhere", {project: P1, stream: W1, groups: false, selected: T1},
    `/w/a1a1a1a1/t/${short(T1)}`, /not a viewer location/);
  await stale("/p/11111111/t", {project: P1, stream: W1, groups: false, selected: T1},
    `/w/a1a1a1a1/t/${short(T1)}`, /not a viewer location/);

  // A launch link pasted into this tab reloads so startup adopts and strips its token.
  await v.edit(v.path(), "#new-launch-token");
  assert.equal(v.reloads(), 1);
  assert.ok(v.entries.every(e => !e.path.includes("launch-token") && !e.path.includes("token")));

  // A bookmarked location in a tab holding the token opens directly, without a new entry.
  const b = viewer({path: `/w/b2b2b2b2/t/${short(T4)}`, hash: ""}, "stored-token");
  await b.run("boot()");
  assert.deepEqual(b.at(), {project: P1, stream: W2, groups: false, selected: T4});
  assert.equal(b.path(), `/w/b2b2b2b2/t/${short(T4)}`);
  assert.equal(b.count(), 1);
  assert.equal(await b.run("token"), "stored-token");
  assert.deepEqual(b.notices, []);
  // A bookmarked shared group opens Shared groups; its origin project is expanded.
  const s = viewer({path: `/g/${short(G2)}`, hash: ""}, "stored-token");
  await s.run("boot()");
  assert.deepEqual(s.at(), {project: P1, stream: null, groups: "shared", selected: G2});
  assert.equal(s.path(), `/g/${short(G2)}`);
  // Project-wide views reload as such.
  const g = viewer({path: "/p/22222222/g", hash: ""}, "stored-token");
  await g.run("boot()");
  assert.deepEqual(g.at(), {project: P2, stream: null, groups: "project", selected: G2});
  assert.equal(g.path(), `/p/22222222/g/${short(G2)}`);
  // An old hash address is neither a token nor a location: it is dropped.
  const o = viewer({path: "/", hash: "#/project/prj_x/all"}, "stored-token");
  assert.equal(o.token(), "stored-token");
  assert.deepEqual(o.entries[0], {path: "/", hash: ""});
  // New public names stay whole, including when one name is another's prefix.
  const readable = "restore-readable-task-ids-across-design-and-signoff";
  tasks[W1].push(readable, readable + "-next");
  tasks[P1].push(readable, readable + "-next");
  await v.edit(`/w/${short(W1)}/t/${readable}`);
  assert.equal(v.at().selected, readable);
  assert.equal(v.path(), `/w/${short(W1)}/t/${readable}`);
  await v.run(`selectTask("${readable}-next", {open: true, entry: "push"})`);
  assert.equal(v.path(), `/w/${short(W1)}/t/${readable}-next`);
  await v.back();
  assert.equal(v.at().selected, readable);
  const reloaded = viewer({path: `/w/${short(W1)}/t/${readable}`, hash: ""}, "stored-token");
  await reloaded.run("boot()");
  assert.equal(reloaded.at().selected, readable);
  const readableGroup = "readable-group-reference";
  groupInfo[readableGroup] = {origin: P1, by: [P1]};
  groupLists[P1].push(readableGroup);
  await v.edit(`/g/${readableGroup}`);
  assert.equal(v.at().selected, readableGroup);
  assert.equal(v.path(), `/g/${readableGroup}`);
  assert.equal(await v.run('parseRoute("/w/a1a1a1a1/t/double--dash")'), null);
  assert.equal(await v.run('parseRoute("/w/a1a1a1a1/t/' + 'x'.repeat(97) + '")'), null);
  console.log("viewer route tests passed");
}
main().catch(error => {
  console.error(error);
  process.exit(1);
});
