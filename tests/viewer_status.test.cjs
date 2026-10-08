// Exercise the shipped status sections, card badges and sign-off prose with a small
// DOM double. This makes no claim about browser layout.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
const listeners = new Map();
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", hidden: false, draggable: false, value: "",
    dataset: {}, style: {}, className: "", title: "", parentElement: null,
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
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener(name, listener) { listeners.set(name, listener); }, querySelectorAll: () => []},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}, pushState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/simtask/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
const kids = n => (n.children || []).filter(c => c && typeof c === "object");
const descendants = n => [n, ...kids(n).flatMap(descendants)];
const text = n => typeof n === "string" ? n : n.textContent + (n.children || []).map(text).join("");

// Workstream cards carry this workstream's current-spec result counts and the standing
// the server derived from them (Store._standing; test_viewer.py pins that rule against
// these same cases). The viewer only sections and labels the server's standing.
const counts = (o = {}) => ({review: 0, passed: 0, human_review: 0, rework: 0, ...o});
const card = (id, title, extra = {}) => ({id, title, object_type: "task", status: "open",
  unresolved_count: 0, attempt_counts: counts(), workstream_order_key: 0, ...extra});
const cards = [
  card("signoff", "Passed review", {attempt_counts: counts({passed: 1})}),
  card("reworked", "Rework then passed", {attempt_counts: counts({rework: 1, passed: 1}),
    latest_rejection: {source: "review", verdict: "rework", attempt_id: "att_old", workstream_id: "w", spec_revision: 1}}),
  card("human", "Human reviewed", {attempt_counts: counts({human_review: 1})}),
  card("brief", "Design brief", {unresolved_count: 1}),
  card("revised", "Revised at sign-off", {unresolved_count: 1, attempt_counts: counts({passed: 1}),
    latest_rejection: {source: "signoff", verdict: "revise", attempt_id: "att_r", workstream_id: "w", spec_revision: 1}}),
  card("review", "Under review", {attempt_counts: counts({review: 1})}),
  card("fixing", "Being fixed", {attempt_counts: counts({rework: 1}), status: "rework",
    latest_rejection: {source: "signoff", verdict: "rework", attempt_id: "att_f", workstream_id: "w", spec_revision: 1}}),
  card("ready", "Ready"),
  card("blocked", "Blocked", {gate_diagnostics: ["prerequisites"], prerequisite_count: 1}),
  card("deferred", "Deferred", {status: "deferred", unresolved_count: 1}),
  card("done", "Done", {status: "done", attempt_counts: counts({passed: 1})}),
  card("dropped", "Dropped", {status: "dropped"}),
].map((c, i) => ({...c, workstream_order_key: [9, 3, 7, 1, 12, 5, 2, 11, 4, 6, 8, 10][i]}));
const expected = {signoff: "signoff", reworked: "signoff", human: "signoff", brief: "decision",
  revised: "decision", review: "progress", fixing: "progress", ready: "open", blocked: "open",
  deferred: "deferred", done: "done", dropped: "dropped"};
for (const c of cards) c.standing = expected[c.id];
// The viewer has no standing rule of its own: a card without the server's standing (an
// older backend) is visibly unknown, never guessed from its fields.
assert.equal(run("typeof standingOf"), "undefined");
assert.equal(run("taskStanding")({status: "open", unresolved_count: 1}), "unknown");

async function test() {
  context.board = [...cards].sort((a, b) => a.workstream_order_key - b.workstream_order_key);
  run(`
    state.project = "p"; state.projects = [{id: "p", name: "Project"}];
    state.stream = "w"; state.streams = [{id: "w", branch: "main", project_id: "p", status: {scoped_count: 12, standings: {signoff: 3, decision: 2}}}];
    api = async (action, payload) => {
      if (action === "tasks") return {items: board, next_offset: null, workstream_order_revision: 4};
      throw new Error("unexpected " + action);
    };
    pages = async () => state.streams;
    selectTask = async () => {};
  `);
  await run("reload()");
  const list = get("list");
  const sections = [];
  for (const n of kids(list)) {
    if (n.classList.contains("section-head")) sections.push({title: n.children[n.children.length - 2].textContent, rows: []});
    else if (n.classList.contains("row")) sections.at(-1).rows.push(n);
  }
  assert.deepEqual(sections.map(s => s.title), ["Signoff", "In progress", "Open", "Design", "Later", "Done"]);
  const ids = s => sections.find(x => x.title === s).rows.map(r => r.dataset.id);
  assert.deepEqual(ids("Signoff"), ["reworked", "human", "signoff"], "canonical relative order within Signoff");
  assert.deepEqual(ids("Design"), ["brief", "revised"], "canonical relative order within Design");
  assert.deepEqual(Array.from(run("orderedRows().map(r => r.id)")), ["reworked", "human", "signoff", "fixing", "review", "blocked", "ready", "brief", "revised", "deferred"]);
  assert.deepEqual(Array.from(run("state.rows.map(r => r.id)")), context.board.map(r => r.id), "grouping changes no stored order");
  assert.deepEqual(ids("In progress"), ["fixing", "review"]);
  assert.deepEqual(ids("Open"), ["blocked", "ready"]);
  assert.deepEqual(ids("Later"), ["deferred"]);
  assert.deepEqual(ids("Done"), [], "Done starts collapsed");

  // j/k follows the displayed section order, including search results.
  run(`selectTask = async id => { state.selected = id; }; state.selected = "signoff";`);
  const press = key => listeners.get("keydown")({key, target: {tagName: "BODY"}, preventDefault() {}});
  press("j");
  assert.equal(run("state.selected"), "fixing");
  press("k");
  assert.equal(run("state.selected"), "signoff");
  run(`state.selected = "ready";`);
  press("j");
  assert.equal(run("state.selected"), "brief", "Design follows Open");
  press("k");
  assert.equal(run("state.selected"), "ready");
  get("search").value = "Design brief";
  run("renderList()");
  assert.deepEqual(kids(get("list")).filter(n => n.classList.contains("row")).map(n => n.dataset.id), ["brief"]);
  press("j");
  assert.equal(run("state.selected"), "brief");
  get("search").value = "";
  run("renderList()");

  // Only Sign-off badges; no stage words, rejections or numbering.
  const badges = {};
  for (const r of sections.flatMap(s => s.rows))
    badges[r.dataset.id] = descendants(r).filter(n => n.classList.contains("badge")).map(n => n.textContent);
  assert.deepEqual(badges, {brief: [], reworked: ["Sign-off"], human: ["Sign-off"], signoff: ["Sign-off"],
    revised: [], fixing: [], review: [], blocked: [], ready: [], deferred: []});
  for (const r of sections.find(s => s.title === "Design").rows)
    assert.ok(descendants(r).some(n => n.classList.contains("dot") && n.classList.contains("tone-warn")), "Design keeps its orange dot");
  // Everything shown except the task titles themselves.
  const listText = descendants(list).filter(n => !n.classList.contains("row-title")).map(n => n.textContent).join(" ");
  assert.doesNotMatch(listText, /Rejected|Rework|Review\b|Ready|Blocked|Question|Sign off\b/);
  assert.ok(!descendants(list).some(n => /row-order|row-meta/.test(n.className)), "No position numbers or stage labels");
  // The badge sits inside the title text, after it, so it never narrows the title.
  const reworked = sections[0].rows[0];
  const title = descendants(reworked).find(n => n.classList.contains("row-title"));
  assert.equal(title.textContent, "Rework then passed");
  assert.equal(title.children.at(-1).textContent, "Sign-off");
  assert.match(reworked.title, /^Sign-off · position 3 in main\. Drag to reorder\.$/);

  // The sidebar counts both loaded Signoff and Design sections.
  assert.equal(run("needsYou(state.streams[0])"), 5);
  // Another workstream's count is the server's Signoff and Design standings; a server
  // without standing counts leaves it uncounted, never the gate-view status counts.
  assert.equal(run('needsYou({id: "other", status: {standings: {signoff: 2, decision: 1, progress: 4}}})'), 3);
  assert.equal(run('needsYou({id: "other", status: {counts: {signoff: 2, unresolved_items: 1}}})'), null);

  // Expanding Done shows done and dropped tasks without badges.
  const doneHead = kids(list).find(n => n.classList.contains("section-head") && text(n).includes("Done"));
  doneHead.onclick();
  const doneRows = kids(get("list")).filter(n => n.classList.contains("row") && ["done", "dropped"].includes(n.dataset.id));
  assert.equal(doneRows.length, 2);
  assert.ok(doneRows.every(r => !descendants(r).some(n => n.classList.contains("badge"))));
  assert.ok(doneRows.every(r => r.classList.contains("closed")));

  // The detail: Spec has no Latest rejection, Result or Other results feeds; it shows one
  // card for the relevant result, and every round (with its rejection) is in Activity.
  run(`markdown = (t) => node("p", t); activity = () => node("div"); resultCard = (a) => node("article", "card:" + a.id);`);
  context.task = {id: "reworked", title: "Rework then passed", status: "open", spec_revision: 1, revision: 6,
    workstream_ids: ["w"], unresolved_items: [], prerequisites: [], blocked_by: [], gate_proposals: [], body: "", acceptance_criteria: "",
    latest_rejection: {source: "review", verdict: "rework", reasons: "Stale revision", attempt_id: "att_old", workstream_id: "w", spec_revision: 1, timestamp: "2026-10-05T08:00:00.000000Z"},
    attempts: [
      {id: "att_old", state: "rework", spec_revision: 1, workstream_id: "w", revision: 2, implementer: "codex-gpt5 implementer", created_at: "2026-10-05T07:00:00.000000Z"},
      {id: "att_new", state: "passed", spec_revision: 1, workstream_id: "w", revision: 2, implementer: "codex-gpt5 implementer", reviewer: "opus reviewer", created_at: "2026-10-05T09:00:00.000000Z"},
    ]};
  context.task.standing = "signoff";
  assert.equal(run("taskStanding(task)"), "signoff");
  const bodyText = () => run("body(task)").flatMap(descendants).map(text).join(" ");
  assert.equal(run("typeof currentRejection"), "undefined");
  assert.doesNotMatch(bodyText(), /Latest rejection|Stale revision|Other results/);
  assert.match(bodyText(), /card:att_new/);
  assert.doesNotMatch(bodyText(), /card:att_old/);
  context.task.attempts.pop();
  context.task.attempts[0].state = "rework";
  context.task.standing = "progress";
  assert.doesNotMatch(bodyText(), /Latest rejection|Stale revision/);
  assert.match(bodyText(), /card:att_old/, "A result sent back is still with the agents");

  // The sign-off panel: plain language, a walkthrough with an agent, no IDs or agent labels.
  context.task.attempts.push({id: "att_new", state: "passed", spec_revision: 1, workstream_id: "w", revision: 2,
    implementer: "codex-gpt5 implementer", reviewer: "opus reviewer", created_at: "2026-10-05T09:00:00.000000Z"});
  const panel = run('nextStep(task, "signoff")');
  const prose = descendants(panel).filter(n => n.tag === "p" || n.tag === "strong").map(n => n.textContent).join(" ");
  assert.match(prose, /Ready for your sign-off/);
  assert.match(prose, /Sign-off is a walkthrough with an agent/);
  assert.doesNotMatch(prose, /att_|codex|opus|implementer|purpose|result|rework|review round/i);
  const labels = descendants(panel).filter(n => n.tag === "button").map(n => n.textContent);
  assert.deepEqual(labels, ["Sign off with an agent"]);
  const human = {...context.task.attempts[1], state: "human_review"};
  context.task.attempts[1] = human;
  assert.match(descendants(run('nextStep(task, "signoff")')).map(n => n.textContent).join(" "), /You reviewed the delivered work below yourself/);

  // Revised at sign-off: the open question holds it, under the new name.
  context.task.unresolved_items = [{id: "unr", text: "Keep workstream as the term"}];
  context.task.standing = "decision";
  assert.match(bodyText(), /Unresolved items/);
  assert.doesNotMatch(bodyText(), /Design\/decision/);
  assert.doesNotMatch(bodyText(), /Open questions/);
  const held = descendants(run('nextStep(task, "decision")')).map(n => n.textContent).join(" ");
  assert.match(held, /An unresolved item needs your answer/);
  assert.doesNotMatch(held, /Sign off with an agent|Answer/);

  // Picked up: the server stands a recently picked task In progress; the panel says who
  // picked it up and when.
  const picked = new Date(Date.now() - 12 * 60000).toISOString();
  run(`state.stream = "w";`);
  context.task = {...context.task, unresolved_items: [], attempts: [], latest_rejection: null,
    standing: "progress", picks: [{workstream_id: "w", action: "implement", picked_at: picked}]};
  const working = descendants(run('nextStep(task, "progress")')).map(n => n.textContent).join(" ");
  assert.match(working, /An agent picked this up 12 minutes ago\. No result is recorded yet\./);
  context.task.attempts = [{id: "att_r", state: "review", spec_revision: 1, workstream_id: "w", revision: 1, created_at: "2026-10-05T09:00:00.000000Z"}];
  context.task.picks = [{workstream_id: "w", action: "review", picked_at: picked}];
  assert.match(descendants(run('nextStep(task, "progress")')).map(n => n.textContent).join(" "),
    /with the agents for independent review\. A reviewer picked it up 12 minutes ago\./);
  assert.equal(run("pickedAgo")(new Date(Date.now() - 61 * 60000).toISOString()), "1 hour ago");
}

// Every workstream's sidebar count comes with the workstream list: the server's count
// of each standing, by the rule that gives the board its standings. No other card list
// is read; group boards refresh the counts too; a failed read is visible and retried.
async function sidebar() {
  const settle = () => new Promise(resolve => setImmediate(resolve));
  // side: a question with a result under review (decision) and a passed result
  // (signoff); main: nothing needs input (zero, shown as its task total).
  const streams = () => [
    {id: "w", branch: "main", project_id: "p", status: {scoped_count: 2, standings: {open: 2}}},
    {id: "s", branch: "side", project_id: "p", status: {scoped_count: 3, standings: {decision: 1, signoff: 1, open: 1}}},
  ];
  const boards = {
    w: [card("shared", "Shared, passed only in side", {standing: "open"}), card("plain", "Plain open task", {standing: "open"})],
    s: [
      card("ur", "Question and a result under review", {view: "review", unresolved_count: 1, attempt_counts: counts({review: 1}), standing: "decision"}),
      card("shared", "Shared, passed only in side", {view: "signoff", attempt_counts: counts({passed: 1}), standing: "signoff"}),
      card("idle", "Open", {view: "ready", standing: "open"}),
    ],
  };
  const project = [boards.s[0], boards.s[1], boards.w[1], boards.s[2]];
  const requests = [];
  context.harness = {streams: streams(), boards, project, requests, fail: false};
  context.toasts = [];
  run(`
    state.project = "p"; state.projects = [{id: "p", name: "Project"}]; state.streams = harness.streams;
    state.groups = false; state.selected = null; state.boardStream = null; state.rows = [];
    toast = (m, error) => toasts.push(m);
    api = async (action, payload) => {
      harness.requests.push(action + ":" + (payload.workstream_id || ""));
      if (action === "workstreams") {
        if (harness.fail) throw new Error("Local service error");
        return {items: harness.streams, next_offset: null};
      }
      if (action === "groups") return {items: [], next_offset: null};
      if (action === "tasks") return {items: payload.workstream_id ? harness.boards[payload.workstream_id] : harness.project,
        next_offset: null, workstream_order_revision: 1};
      throw new Error("unexpected " + action);
    };
    pages = async (action, data) => (await api(action, data)).items;
  `);
  const navItem = name => descendants(get("nav")).find(n => n.classList.contains("nav-item") && text(n).startsWith(name));
  const navCount = name => {
    const count = kids(navItem(name)).find(n => n.classList.contains("count"));
    return {count: count.textContent, attention: count.classList.contains("attention"), stale: count.classList.contains("stale")};
  };
  const inputRows = () => {
    let inInput = false, n = 0;
    for (const node of kids(get("list"))) {
      if (node.classList.contains("section-head")) inInput = /^(Signoff|Design)/.test(text(node));
      else if (inInput && node.classList.contains("row")) n++;
    }
    return n;
  };
  const visit = async (stream, groups = false) => {
    requests.length = 0;
    run(`state.stream = ${JSON.stringify(stream)}; state.groups = ${JSON.stringify(groups)};`);
    await run("reload()");
    for (let i = 0; i < 5; i++) await settle();
  };

  // From main: side's count is the server's, read with the workstream list; no card list
  // other than the open board is read.
  await visit("w");
  assert.equal(run('needsYou(state.streams[1])'), 2, "question + result under review counts as needing input");
  assert.deepEqual(navCount("side"), {count: "2", attention: true, stale: false});
  assert.deepEqual(navCount("main"), {count: "2", attention: false, stale: false}, "nothing needs input: the task total");
  assert.match(navItem("main").title, /2 tasks · none need you$/);
  assert.match(navItem("side").title, /3 tasks · 2 need you$/);
  assert.deepEqual([...requests].sort(), ["tasks:w", "workstreams:"]);
  // From All tasks: the same single workstream read.
  await visit(null);
  assert.deepEqual(navCount("side"), {count: "2", attention: true, stale: false});
  assert.deepEqual([...requests].sort(), ["tasks:", "workstreams:"]);
  // Opening side: both input sections total exactly the sidebar count.
  await visit("s");
  assert.equal(inputRows(), 2);
  assert.deepEqual(navCount("side"), {count: "2", attention: true, stale: false});
  assert.deepEqual([...requests].sort(), ["tasks:s", "workstreams:"]);

  // A group board refreshes the counts too: a change made meanwhile shows.
  context.harness.streams = streams();
  context.harness.streams[0].status.standings = {signoff: 1, open: 1};
  await visit(null, "project");
  assert.deepEqual(requests, ["workstreams:", "groups:"]);
  assert.deepEqual(navCount("main"), {count: "1", attention: true, stale: false});

  // A failed read keeps the last counts, marks them out of date, says so once and offers
  // a retry; the next successful refresh clears the mark.
  context.harness.fail = true;
  await visit(null, "project");
  assert.deepEqual(navCount("main"), {count: "1", attention: true, stale: true});
  assert.deepEqual(navCount("side"), {count: "2", attention: true, stale: true});
  assert.match(navItem("side").title, /may be out of date: the last refresh failed \(Local service error\)/);
  assert.equal(context.toasts.filter(m => /Couldn't refresh the workstream counts/.test(m)).length, 1);
  const retry = navItem("Counts may be out of date");
  assert.ok(retry, "a retry entry in the sidebar");
  await visit(null, "project");
  assert.equal(context.toasts.filter(m => /Couldn't refresh the workstream counts/.test(m)).length, 1, "said once, not on every refresh");
  context.harness.fail = false;
  requests.length = 0;
  retry.onclick();
  for (let i = 0; i < 5; i++) await settle();
  assert.deepEqual(requests, ["workstreams:", "groups:"]);
  assert.deepEqual(navCount("main"), {count: "1", attention: true, stale: false});
  assert.equal(navItem("Counts may be out of date"), undefined);

  // A server without standing counts (an older backend): uncounted, not zero.
  context.harness.streams = streams().map(s => ({...s, status: {scoped_count: s.status.scoped_count}}));
  await visit(null, "project");
  assert.deepEqual(navCount("side"), {count: "?", attention: false, stale: false});
  assert.match(navItem("side").title, /not counted; restart the viewer/);
}
test().then(sidebar).catch(error => {console.error(error); process.exitCode = 1;});
