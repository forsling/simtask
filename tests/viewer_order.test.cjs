// Exercise the shipped drag-and-drop ordering handlers with a small DOM double.
// This verifies order computation, payloads and reconciliation; the real browser
// interaction is checked separately against a live viewer.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", disabled: false, hidden: false, draggable: false,
    value: "", checked: false, required: false, dataset: {}, className: "", parentElement: null,
    get classList() {
      const self = this, names = () => self.className.split(/\s+/).filter(Boolean);
      const list = {
        add: (...c) => { self.className = [...new Set([...names(), ...c])].join(" "); },
        remove: (...c) => { self.className = names().filter(n => !c.includes(n)).join(" "); },
        toggle: (c, on) => { (on ?? !names().includes(c)) ? list.add(c) : list.remove(c); },
        contains: c => names().includes(c),
      };
      return list;
    },
    append(...children) { for (const child of children) {
      this.children.push(child); if (child && typeof child === "object") child.parentElement = this;
    } },
    prepend(...children) { this.append(...children); },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    closest(selector) {
      for (let n = this; n; n = n.parentElement) if (selector === ".row" && n.classList.contains("row")) return n;
      return null;
    },
    contains(other) { for (let n = other; n; n = n.parentElement) if (n === this) return true; return false; },
    getBoundingClientRect() { return {top: 100, height: 40}; },
    setAttribute() {}, querySelector() { return null; }, scrollIntoView() {}, showModal() {}, close() {}, remove() {},
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
  document: {getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag), createDocumentFragment: element,
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}, querySelectorAll: () => []},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
function descendants(node) {return [node, ...(node.children || []).flatMap(descendants)];}
const rowsIn = () => descendants(get("list")).filter(n => n.classList.contains("row"));
const rowFor = id => rowsIn().find(n => n.dataset.id === id);
const reorders = () => context.calls.filter(c => c.action === "reorder");
const deq = (actual, expected, message) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), expected, message);
const toasts = () => get("toasts").children.map(n => n.textContent);
function event(target, clientY = 110, relatedTarget = null) {
  const e = {target, clientY, relatedTarget, prevented: false, dataTransfer: {data: {}, setData(k, v) { this.data[k] = v; }},
    preventDefault() { this.prevented = true; }};
  return e;
}

// Server double: A holds done, alpha, inbox, hidden, beta; B holds the same tasks in its own order.
run(`
  server = {revision: {a: 7, b: 3}, order: {a: ["done", "alpha", "inbox", "hidden", "beta"], b: ["beta", "alpha", "done"]}};
  views = {done: "done", alpha: "ready", inbox: "inbox", hidden: "deferred", beta: "ready"};
  calls = []; nextReorder = null;
  api = async (action, payload) => {
    calls.push({action, payload: JSON.parse(JSON.stringify(payload))});
    if (action === "tasks") {
      const ids = payload.workstream_id ? server.order[payload.workstream_id] : ["done", "alpha", "inbox", "hidden", "beta"];
      return {workstream_order_revision: payload.workstream_id ? server.revision[payload.workstream_id] : undefined, next_offset: null,
        items: ids.map((id, i) => ({id, title: id.toUpperCase(), view: views[id], workstream_order_key: i + 1}))};
    }
    if (action === "reorder") {
      if (nextReorder) return nextReorder(payload);
      const w = payload.workstream_id;
      if (payload.expected_order_revision !== server.revision[w]) { const e = new Error("revision_conflict"); e.conflict = true; throw e; }
      const rest = server.order[w].filter(id => !payload.task_ids.includes(id));
      server.order[w] = [...payload.task_ids, ...rest]; server.revision[w]++;
      return {workstream_id: w, workstream_order_revision: server.revision[w], changed: true};
    }
    throw new Error("unexpected " + action);
  };
  pages = async () => [{id: "a", branch: "A", project_id: "project"}, {id: "b", branch: "B", project_id: "project"}];
  selectTask = async () => {};
  state.project = "project"; state.projects = [{id: "project", name: "Project"}]; state.stream = "a";
`);

async function test() {
  // The modal and its launch control are gone; completed tasks render no reorder button.
  assert.equal(run("typeof moveTask"), "undefined");
  context.task = {id: "done", title: "Signed off task", project_id: "project", status: "done",
    object_type: "task", revision: 9, body: "Scope", acceptance_criteria: "Proof",
    workstream_ids: ["a", "b"], unresolved_items: [], blocked_by: [], attempts: [], gate_proposals: []};
  run("state.streams = [{id: 'a', branch: 'A'}, {id: 'b', branch: 'B'}]; renderDetail(task)");
  assert.ok(!descendants(get("detail")).some(n => /Reorder/.test(n.textContent)), "No ordering modal launcher");

  // Pure order computation: invalid and unchanged drops yield nothing to save.
  const ids = ["a", "b", "c", "d"];
  const order = (...args) => { const r = run("droppedOrder")(...args); return r && Array.from(r); };
  deq(order(ids, "d", "b", "before"), ["a", "d", "b", "c"]);
  deq(order(ids, "a", "c", "after"), ["b", "c", "a", "d"]);
  assert.equal(order(ids, "b", "b", "after"), null, "Self drop");
  assert.equal(order(ids, "b", "c", "before"), null, "Already before c");
  assert.equal(order(ids, "b", "a", "after"), null, "Already after a");
  assert.equal(order(ids, "x", "a", "after"), null, "Unknown task");
  assert.equal(order(ids, "a", "b", "inside"), null, "Unknown placement");

  // A named workstream loads its complete order and revision; rows become draggable.
  await run("reload()");
  assert.equal(run("state.orderRevision"), 7);
  assert.equal(rowFor("alpha").draggable, true);
  assert.ok(rowFor("done") === undefined, "Closed section starts collapsed");

  // Cancelled drag: start, hover, then dragend without a drop issues no mutation.
  const list = get("list");
  let e = event(rowFor("beta"));
  list.ondragstart(e);
  assert.equal(e.prevented, false);
  assert.equal(e.dataTransfer.data["application/x-task-mcp-task"], "beta");
  e = event(rowFor("alpha"), 105);
  list.ondragover(e);
  assert.equal(e.prevented, true);
  assert.ok(rowFor("alpha").classList.contains("drop-before"));
  assert.match(get("drop-hint").textContent, /Place before “ALPHA” · position 2 of 5 in A/);
  list.ondragend(event(rowFor("beta")));
  assert.ok(!rowFor("alpha").classList.contains("drop-before"));
  assert.equal(get("drop-hint").hidden, true);
  assert.equal(reorders().length, 0, "Cancelled drag saves nothing");

  // Self and unchanged targets are not droppable and save nothing.
  list.ondragstart(event(rowFor("alpha")));
  e = event(rowFor("alpha"), 105);
  list.ondragover(e);
  assert.equal(e.prevented, false, "Dropping on itself is not allowed");
  list.ondragend(event(rowFor("alpha")));
  assert.equal(await run('commitDrop("inbox", "alpha", "after")'), false, "Already immediately after alpha");
  assert.equal(await run('commitDrop("beta", "beta", "after")'), false);
  assert.equal(reorders().length, 0, "No-op and invalid drops save nothing");

  // A real drop with a search filter active: the hidden and collapsed members keep their order.
  get("search").value = "BETA";
  run("renderList()");
  deq(rowsIn().map(r => r.dataset.id), ["beta"]);
  get("search").value = "";
  run("renderList()");
  list.ondragstart(event(rowFor("beta")));
  const drop = event(rowFor("inbox"), 130); // lower half: after
  list.ondragover(drop);
  assert.ok(rowFor("inbox").classList.contains("drop-after"));
  assert.match(get("drop-hint").textContent, /stays under In progress, because status sets the section/);
  list.ondrop(drop);
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reorders().length, 1);
  deq(reorders()[0].payload, {workstream_id: "a", task_ids: ["done", "alpha", "inbox", "beta"], expected_order_revision: 7});
  deq(run("server.order.a"), ["done", "alpha", "inbox", "beta", "hidden"], "Indicated placement matches result");
  deq(run("server.order.b"), ["beta", "alpha", "done"], "Workstream B keeps its own order");
  assert.equal(run("state.orderRevision"), 8, "Reconciled revision after save");
  deq(Array.from(run("state.rows.map(r => r.id)")), ["done", "alpha", "inbox", "beta", "hidden"]);
  assert.ok(toasts().includes("Order saved"));

  // Completed tasks stay reorderable, even moving across sections.
  assert.equal(await run('commitDrop("done", "beta", "after")'), true);
  deq(reorders().at(-1).payload.task_ids, ["alpha", "inbox", "beta", "done"]);
  assert.equal(run("state.orderRevision"), 9);

  // Stale revision: another writer changed A. Nothing is overwritten; the board reloads the current order.
  run('server.order.a = ["hidden", "beta", "alpha", "inbox", "done"]; server.revision.a = 20;');
  assert.equal(await run('commitDrop("alpha", "beta", "before")'), false);
  deq(reorders().at(-1).payload.expected_order_revision, 9);
  deq(run("server.order.a"), ["hidden", "beta", "alpha", "inbox", "done"], "Concurrent order survives");
  deq(Array.from(run("state.rows.map(r => r.id)")), ["hidden", "beta", "alpha", "inbox", "done"]);
  assert.equal(run("state.orderRevision"), 20);
  assert.match(toasts().at(-1), /A changed elsewhere, so this move was not saved/);
  assert.equal(run("state.orderSaving"), false);

  // Other failures also restore the recorded order and explain the outcome.
  run('nextReorder = async () => { throw new Error("Local service error"); };');
  assert.equal(await run('commitDrop("done", "hidden", "before")'), false);
  assert.match(toasts().at(-1), /Couldn't confirm the new order \(Local service error\)/);
  deq(Array.from(run("state.rows.map(r => r.id)")), ["hidden", "beta", "alpha", "inbox", "done"]);

  // While a save is pending, further drags and drops are refused.
  let release;
  run("nextReorder = () => pending;");
  context.pending = new Promise(resolve => { release = resolve; });
  const saving = run('commitDrop("done", "hidden", "before")');
  deq(Array.from(run("state.rows.map(r => r.id)")), ["done", "hidden", "beta", "alpha", "inbox"], "Optimistic order shown while saving");
  const before = reorders().length;
  assert.equal(await run('commitDrop("beta", "hidden", "before")'), false);
  e = event(rowFor("beta"));
  list.ondragstart(e);
  assert.equal(e.prevented, true);
  assert.equal(reorders().length, before);
  release({changed: true, workstream_order_revision: 21});
  run('server.order.a = ["done", "hidden", "beta", "alpha", "inbox"]; server.revision.a = 21; nextReorder = null;');
  assert.equal(await saving, true);
  assert.equal(run("state.orderRevision"), 21);

  // Reconciling reloads can be slow and the user may click a row meanwhile.
  run(`
    baseApi = api; tasksGate = null;
    api = async (action, payload) => { if (action === "tasks" && tasksGate) await tasksGate; return baseApi(action, payload); };
    hold = () => { tasksGate = new Promise(resolve => { releaseTasks = () => { tasksGate = null; resolve(); }; }); };
  `);
  const ticks = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
  const shown = () => Array.from(run("state.rows.map(r => r.id)"));
  const recorded = ["done", "hidden", "beta", "alpha", "inbox"];

  // Failed save, then a row click during the reconciling reload: the board shows the
  // loaded order at once, dragging stays off until the reload lands, and the next move
  // saves only what the user did after the failure.
  run('hold(); nextReorder = async () => { throw new Error("transient"); };');
  let pending = run('commitDrop("inbox", "done", "before")');
  await ticks();
  deq(shown(), recorded, "Failed move is not kept on the board");
  assert.equal(run("state.orderRevision"), null);
  assert.equal(rowsIn().some(r => r.draggable), false, "Dragging waits for the reconcile");
  e = event(rowFor("beta"));
  list.ondragstart(e);
  assert.equal(e.prevented, true);
  let count = reorders().length;
  assert.equal(await run('commitDrop("beta", "hidden", "before")'), false);
  assert.equal(reorders().length, count, "No save from an unreconciled board");
  run("state.generation++; nextReorder = null;"); // a row click starts a detail load
  run("releaseTasks()");
  assert.equal(await pending, false);
  deq(shown(), recorded);
  assert.equal(run("state.orderRevision"), 21);
  assert.equal(await run('commitDrop("alpha", "done", "before")'), true);
  deq(reorders().at(-1).payload, {workstream_id: "a", task_ids: ["alpha"], expected_order_revision: 21});
  deq(run("server.order.a"), ["alpha", "done", "hidden", "beta", "inbox"], "Only the later move was saved");

  // Conflict, interrupted the same way: the concurrent order is shown, never overwritten.
  run('server.order.a = ["inbox", "alpha", "done", "hidden", "beta"]; server.revision.a = 30; hold();');
  pending = run('commitDrop("beta", "alpha", "before")');
  await ticks();
  deq(shown(), ["alpha", "done", "hidden", "beta", "inbox"], "Stale order restored, not the failed move");
  assert.equal(run("state.orderRevision"), null);
  run("state.generation++; releaseTasks();");
  assert.equal(await pending, false);
  deq(shown(), ["inbox", "alpha", "done", "hidden", "beta"]);
  assert.equal(run("state.orderRevision"), 30);
  assert.match(toasts().at(-1), /A changed elsewhere/);

  // Success while the reconciling reload is still pending: the saved revision is adopted
  // from the reorder response, so another move saves without a false conflict.
  run("hold();");
  pending = run('commitDrop("beta", "inbox", "before")');
  await ticks();
  deq(shown(), ["beta", "inbox", "alpha", "done", "hidden"]);
  assert.equal(run("state.orderRevision"), 31, "Revision from the reorder response");
  assert.equal(rowsIn().find(r => r.dataset.id === "beta").draggable, true);
  run("state.generation++;");
  const second = run('commitDrop("hidden", "inbox", "after")');
  await ticks();
  deq(reorders().at(-1).payload, {workstream_id: "a", task_ids: ["beta", "inbox", "hidden"], expected_order_revision: 31});
  run("releaseTasks()");
  assert.equal(await pending, true);
  assert.equal(await second, true);
  deq(run("server.order.a"), ["beta", "inbox", "hidden", "alpha", "done"]);
  deq(shown(), ["beta", "inbox", "hidden", "alpha", "done"]);
  assert.equal(run("state.orderRevision"), 32);
  run("api = baseApi;");

  // Project-wide and group views have no workstream order to edit.
  await run("changeScope(null)");
  assert.equal(run("orderEditable()"), false);
  assert.equal(rowsIn().some(r => r.draggable), false);
  assert.equal(await run('commitDrop("done", "inbox", "after")'), false);
  e = event(rowsIn()[0]);
  list.ondragstart(e);
  assert.equal(e.prevented, true);
  run('pages = async (action) => action === "groups" ? [] : [{id: "a", branch: "A", project_id: "project"}];');
  await run('chooseGroups("project")');
  assert.equal(run("orderEditable()"), false);

  // Switching to B edits only B, with B's own revision.
  await run('changeScope("b")');
  assert.equal(run("state.orderRevision"), 3);
  assert.equal(await run('commitDrop("done", "beta", "before")'), true);
  deq(reorders().at(-1).payload, {workstream_id: "b", task_ids: ["done"], expected_order_revision: 3});
  deq(run("server.order.b"), ["done", "beta", "alpha"]);
  deq(run("server.order.a"), ["beta", "inbox", "hidden", "alpha", "done"]);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
