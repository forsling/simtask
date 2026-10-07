// Exercise the shipped Unassigned view: its sidebar entry below All tasks, the board
// request (a server-side placement filter, paged), sections, empty state, location URLs
// through refresh and back/forward, no reordering, task actions, and the All tasks
// marker. A DOM/history double; layout is checked in a browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");

function element(tag = "div") {
  const classes = new Set();
  const node = {tag, children: [], textContent: "", value: "", hidden: false, dataset: {}, style: {}, attributes: {},
    get className() { return [...classes].join(" "); },
    set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
    classList: {add(c) { classes.add(c); }, remove(...c) { c.forEach(x => classes.delete(x)); },
      toggle(c, on) { (on === undefined ? !classes.has(c) : on) ? classes.add(c) : classes.delete(c); }, contains(c) { return classes.has(c); }},
    append(...children) { for (const c of children) { this.children.push(c); if (c && typeof c === "object") c.parentElement = this; } },
    prepend() {}, replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute(k, v) { this.attributes[k] = v; }, scrollIntoView() {}, querySelector() { return null; },
    closest() { return null; }};
  return node;
}
const descendants = n => [n, ...(n.children || []).flatMap(c => (c && typeof c === "object" ? descendants(c) : []))];
const text = n => typeof n === "string" ? n : [n.textContent, ...(n.children || []).map(text)].join("");
const texts = n => descendants(n).map(x => x.textContent).join(" ");
const hasClass = (n, c) => (n.className || "").split(/\s+/).includes(c);

const P = "prj_11111111" + "0".repeat(24), Q = "prj_22222222" + "0".repeat(24);
const W = "wst_a1a1a1a1" + "0".repeat(24);
const card = (id, standing, ws = [], extra = {}) => ({id, title: `Task ${id}`, standing, status: standing === "deferred" || standing === "done" || standing === "dropped" ? standing : "open",
  workstream_ids: ws, workstream_order_key: 1, ...extra});
// One unassigned task in every standing, plus assigned tasks for All tasks.
const unassigned = [card("sign", "signoff"), card("prog", "progress"), card("open-one", "open"), card("idea", "decision"),
  card("later", "deferred"), card("finished", "done")];
const assigned = [card("member", "open", [W]), card("member-design", "decision", [W])];

function viewer(initialPath, {stubDetail = true} = {}) {
  const entries = [{path: initialPath, hash: ""}], listeners = {}, calls = [], toasts = [];
  let index = 0;
  const elements = new Map();
  const get = id => elements.get(id) || elements.set(id, element()).get(id);
  const context = vm.createContext({
    document: {getElementById: get, createElement: element, createElementNS: () => element(), addEventListener() {}, querySelectorAll() { return []; }},
    window: {addEventListener(type, fn) { (listeners[type] ||= []).push(fn); }},
    setTimeout() {},
    location: {get pathname() { return entries[index].path; }, get hash() { return entries[index].hash; }, reload() {}},
    sessionStorage: {getItem() { return "token"; }, setItem() {}},
    history: {
      pushState(_, __, url) { entries.splice(index + 1); entries.push({path: url, hash: ""}); index++; },
      replaceState(_, __, url) { entries[index] = {path: url, hash: ""}; },
    },
  });
  vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
  const run = code => vm.runInContext(code, context);
  context.board = {unassigned: [...unassigned], all: [...unassigned, ...assigned], [W]: [...assigned]};
  context.apiMock = async (action, data) => {
    calls.push([action, JSON.parse(JSON.stringify(data))]);
    if (action === "projects") return {items: [{id: P, name: "One"}, {id: Q, name: "Two"}], next_offset: null};
    if (action === "workstreams") return {items: data.project === P ? [{id: W, project_id: P, branch: "main", status: {scoped_count: 2, standings: {}}}] : [], next_offset: null};
    if (action === "tasks") {
      const rows = data.project !== P ? [] : data.unassigned ? context.board.unassigned : data.workstream_id ? context.board[data.workstream_id] : context.board.all;
      // Pages of two, as the server pages over matches only.
      const items = rows.slice(data.offset, data.offset + 2);
      return {items, next_offset: data.offset + 2 < rows.length ? data.offset + 2 : null, workstream_order_revision: data.workstream_id ? 1 : undefined};
    }
    if (action === "resolve-prefix") return {items: data.prefix === "a1a1a1a1" ? [{id: W, project_id: P}] : []};
    if (action === "details") return {items: [{...[...unassigned, ...assigned].find(t => t.id === data.ids[0]), object_type: "task"}]};
    throw new Error("unexpected " + action);
  };
  run("api = apiMock; toast = (m) => toastSink(m);" + (stubDetail ? " renderDetail = () => {};" : ""));
  context.toastSink = m => toasts.push(m);
  const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
  return {
    run: async code => { const r = await run(code); await settle(); return r; },
    get, calls, toasts, entries,
    path: () => entries[index].path,
    back: async () => { index--; (listeners.popstate || []).forEach(f => f()); await settle(); },
    forward: async () => { index++; (listeners.popstate || []).forEach(f => f()); await settle(); },
  };
}
const navSub = v => v.get("nav").children.find(c => hasClass(c, "nav-sub"));
const rowsOf = v => v.get("list").children.filter(c => hasClass(c, "row"));
const sectionsOf = v => v.get("list").children.filter(c => hasClass(c, "section-head")).map(c => c.children[c.children.length - 2].textContent);

async function main() {
  const v = viewer("/p/11111111/u");
  await v.run("boot()");
  // Sidebar: Unassigned directly below All tasks, active here.
  const sub = navSub(v);
  assert.deepEqual(sub.children.slice(0, 3).map(text), ["All tasks", "Unassigned", "Task groups"]);
  assert.ok(hasClass(sub.children[1], "active"));
  assert.ok(!hasClass(sub.children[0], "active"));
  // The board is the server's Unassigned filter, every page of it, with no workstream.
  const boards = v.calls.filter(([a]) => a === "tasks");
  assert.deepEqual(boards.map(([, d]) => d), [0, 2, 4].map(offset => ({project: P, unassigned: true, limit: 100, offset})));
  assert.equal(text(v.get("heading")), "Unassigned");
  assert.match(v.get("subheading").textContent, /^One · 6 tasks in no workstream$/);
  assert.match(v.get("subheading").title, /belong to no workstream/);
  // The usual sections, in the usual order (Done collapsed).
  assert.deepEqual(sectionsOf(v), ["Signoff", "In progress", "Open", "Design", "Later", "Done"]);
  // No reordering without a workstream, and no redundant placement marker here.
  assert.equal(await v.run("orderEditable()"), false);
  assert.ok(rowsOf(v).every(r => !r.draggable && !hasClass(r, "draggable")));
  assert.ok(rowsOf(v).every(r => !texts(r).includes("Unassigned")));
  // The first task is selected and the address keeps the view.
  assert.equal(v.path(), "/p/11111111/u/t/id/sign");

  // Refresh keeps the view and selection.
  await v.run(`selectTask("idea", {entry: "push"})`);
  assert.equal(v.path(), "/p/11111111/u/t/id/idea");
  await v.run("reload()");
  assert.equal(await v.run("state.unassigned"), true);
  assert.equal(await v.run("state.selected"), "idea");
  assert.equal(v.path(), "/p/11111111/u/t/id/idea");

  // Search filters as elsewhere.
  v.get("search").value = "open-one";
  await v.run("renderList()");
  assert.deepEqual(rowsOf(v).map(r => r.dataset.id), ["open-one"]);
  v.get("search").value = "";

  // All tasks: unassigned cards carry a small marker after the title; members do not.
  await v.run("changeScope(null)");
  assert.equal(v.path(), "/p/11111111/t/id/sign");
  assert.equal(await v.run("state.unassigned"), false);
  assert.ok(hasClass(navSub(v).children[0], "active") && !hasClass(navSub(v).children[1], "active"));
  const marker = r => descendants(r).find(n => hasClass(n, "tone-placement"));
  for (const r of rowsOf(v)) {
    const isMember = ["member", "member-design"].includes(r.dataset.id);
    assert.equal(!!marker(r), !isMember, r.dataset.id);
    if (!isMember) {
      const title = descendants(r).find(n => hasClass(n, "row-title"));
      assert.equal(marker(r).textContent, "Unassigned");
      assert.ok(title.children.includes(marker(r)), "inline after the title");
      assert.ok(title.textContent.startsWith("Task "), "the title is kept whole");
    }
  }
  assert.equal(text(v.get("heading")), "All tasks");

  // Back returns to Unassigned with its selection; forward to All tasks.
  await v.back();
  assert.equal(v.path(), "/p/11111111/u/t/id/idea");
  assert.equal(await v.run("state.unassigned"), true);
  assert.equal(await v.run("state.selected"), "idea");
  assert.equal(v.calls.filter(([a]) => a === "tasks").at(-1)[1].unassigned, true);
  await v.forward();
  assert.equal(await v.run("state.unassigned"), false);

  // A workstream view shows only its members, without the marker; Unassigned is cleared.
  await v.run(`changeScope("${W}")`);
  assert.equal(await v.run("state.unassigned"), false);
  assert.ok(rowsOf(v).every(r => !marker(r)));
  assert.deepEqual(rowsOf(v).map(r => r.dataset.id).sort(), ["member", "member-design"]);
  await v.run("chooseUnassigned()");
  assert.equal(v.path(), "/p/11111111/u/t/id/sign");

  // Adding a task to a workstream removes it on the next refresh; removing its last
  // membership brings it back.
  await v.run(`selectTask("open-one", {entry: "push"})`);
  v.run(`board.unassigned = board.unassigned.filter(t => t.id !== "open-one")`);
  await v.run("reload({quiet: true})");
  assert.ok(!rowsOf(v).some(r => r.dataset.id === "open-one"));
  assert.equal(await v.run("state.selected"), "sign", "the next task is selected instead");
  v.run(`board.unassigned.push(board.all.find(t => t.id === "member"))`);
  await v.run("reload({quiet: true})");
  assert.ok(rowsOf(v).some(r => r.dataset.id === "member"));

  // Empty state.
  v.run("board.unassigned = []");
  await v.run("reload()");
  assert.match(texts(v.get("detail")), /No unassigned tasks.*belongs to a workstream.*belong to no workstream/);
  assert.equal(v.get("subheading").textContent, "One · 0 tasks in no workstream");

  // Other projects never mix in, and a typed address with a task opens it.
  const w = viewer("/p/11111111/u/t/id/prog");
  await w.run("boot()");
  assert.equal(await w.run("state.selected"), "prog");
  assert.equal(w.path(), "/p/11111111/u/t/id/prog");
  // A task that is not unassigned falls back to the first one with a notice.
  const x = viewer("/p/11111111/u/t/id/member");
  await x.run("boot()");
  assert.match(x.toasts.join(" "), /That task is not in Unassigned/);
  assert.equal(x.path(), "/p/11111111/u/t/id/sign");
  // Malformed Unassigned addresses are not viewer locations.
  for (const bad of ["/p/11111111/u/x", "/p/11111111/u/t", "/p/11111111/u/t/id/Bad", "/p/11111111/u/t/abc/d"])
    assert.equal(await x.run(`parseRoute(${JSON.stringify(bad)})`), null, bad);
  console.log("viewer unassigned ok");
}

// Detail rendering with the real renderDetail (the harness above stubs it).
async function detail() {
  const v = viewer("/p/11111111/u", {stubDetail: false});
  await v.run(`projectName = () => "One"; ago = () => "now"; activity = () => node("div"); markdown = t => node("p", t);
    state.project = "${P}"; state.streams = [{id: "${W}", project_id: "${P}", branch: "main"}]; state.stream = null; state.unassigned = true;`);
  const t = {id: "q", title: "Question task", project_id: P, revision: 2, spec_revision: 1, status: "open", standing: "decision",
    workstream_ids: [], prerequisites: [], blocked_by: [], attempts: [], gate_proposals: [], unresolved_items: [{id: "u1", text: "Which branch?"}],
    body: "Spec", acceptance_criteria: "Criteria", updated_at: "2026-10-07T00:00:00Z"};
  await v.run(`renderDetail(${JSON.stringify(t)})`);
  const page = v.get("detail");
  const all = texts(page);
  assert.match(all, /Unassigned/);
  assert.doesNotMatch(all, /Inbox/);
  const buttons = descendants(page).filter(n => n.tag === "button").map(n => n.textContent);
  assert.ok(buttons.includes("Design with agent"), "Design with agent needs no placement");
  assert.ok(buttons.includes("Add to workstream"), "Add to workstream stays available");
  assert.ok(buttons.includes("Actions"));
  // The crumbs name the view.
  assert.match(texts(descendants(page).find(n => hasClass(n, "crumbs"))), /One.*Unassigned/);
  // A passed result keeps its sign-off action without a workstream; placement stays in
  // the Actions menu.
  const passed = {...t, id: "s", title: "Passed task", standing: "signoff", unresolved_items: [],
    attempts: [{id: "a1", revision: 2, spec_revision: 1, state: "passed", workstream_id: W, created_at: "2026-10-07T00:00:00Z"}]};
  await v.run(`renderDetail(${JSON.stringify(passed)})`);
  const signoff = descendants(v.get("detail")).filter(n => n.tag === "button").map(n => n.textContent);
  assert.ok(signoff.includes("Sign off with an agent"), JSON.stringify(signoff));
  assert.match(texts(v.get("detail")), /Ready for your sign-off/);
  console.log("viewer unassigned detail ok");
}

main().then(detail).catch(error => { console.error(error); process.exit(1); });
