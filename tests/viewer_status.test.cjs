// Exercise the shipped status sections, card badges and sign-off prose with a small
// DOM double. Layout (no title truncation at 1280 px) is checked in a real browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
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
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}, querySelectorAll: () => []},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}, pushState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
const kids = n => (n.children || []).filter(c => c && typeof c === "object");
const descendants = n => [n, ...kids(n).flatMap(descendants)];
const text = n => typeof n === "string" ? n : n.textContent + (n.children || []).map(text).join("");

// Workstream cards carry this workstream's current-spec result counts; project cards
// carry every workstream's. Neither counts results for an older specification.
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
for (const c of cards) assert.equal(run("standingOf")(c), expected[c.id], c.id);
// Project boards: aggregate counts across workstreams, no view field.
assert.equal(run("standingOf")({status: "open", unresolved_count: 0, aggregate_attempt_counts: counts({passed: 1}), gate_diagnostics: []}), "signoff");
assert.equal(run("standingOf")({status: "rework", unresolved_count: 0, aggregate_attempt_counts: counts({rework: 1}), gate_diagnostics: []}), "progress");
assert.equal(run("standingOf")({status: "open", unresolved_count: 0, gate_diagnostics: ["inbox"]}), "open");

async function test() {
  context.board = [...cards].sort((a, b) => a.workstream_order_key - b.workstream_order_key);
  run(`
    state.project = "p"; state.projects = [{id: "p", name: "Project"}];
    state.stream = "w"; state.streams = [{id: "w", branch: "main", project_id: "p", status: {counts: {signoff: 1, unresolved_items: 0}}}];
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
  assert.deepEqual(sections.map(s => s.title), ["Needs input", "In progress", "Open", "Later", "Done"]);
  const ids = s => sections.find(x => x.title === s).rows.map(r => r.dataset.id);
  assert.deepEqual(ids("Needs input"), ["brief", "reworked", "human", "signoff", "revised"], "workstream order within the section");
  assert.deepEqual(ids("In progress"), ["fixing", "review"]);
  assert.deepEqual(ids("Open"), ["blocked", "ready"]);
  assert.deepEqual(ids("Later"), ["deferred"]);
  assert.deepEqual(ids("Done"), [], "Done starts collapsed");

  // Only Sign-off and Design/decision badges; no stage words, rejections or numbering.
  const badges = {};
  for (const r of sections.flatMap(s => s.rows))
    badges[r.dataset.id] = descendants(r).filter(n => n.classList.contains("badge")).map(n => n.textContent);
  assert.deepEqual(badges, {brief: ["Design/decision"], reworked: ["Sign-off"], human: ["Sign-off"], signoff: ["Sign-off"],
    revised: ["Design/decision"], fixing: [], review: [], blocked: [], ready: [], deferred: []});
  // Everything shown except the task titles themselves.
  const listText = descendants(list).filter(n => !n.classList.contains("row-title")).map(n => n.textContent).join(" ");
  assert.doesNotMatch(listText, /Rejected|Rework|Review\b|Ready|Blocked|Question|Sign off\b/);
  assert.ok(!descendants(list).some(n => /row-order|row-meta/.test(n.className)), "No position numbers or stage labels");
  // The badge sits inside the title text, after it, so it never narrows the title.
  const reworked = sections[0].rows[1];
  const title = descendants(reworked).find(n => n.classList.contains("row-title"));
  assert.equal(title.textContent, "Rework then passed");
  assert.equal(title.children.at(-1).textContent, "Sign-off");
  assert.match(reworked.title, /^Sign-off · position 3 in main\. Drag to reorder\.$/);

  // The sidebar count matches the loaded Needs input section.
  assert.equal(run("needsYou(state.streams[0])"), 5);
  assert.equal(run('needsYou({id: "other", status: {counts: {signoff: 2, unresolved_items: 1}}})'), 3);

  // Expanding Done shows done and dropped tasks without badges.
  const doneHead = kids(list).find(n => n.classList.contains("section-head") && text(n).includes("Done"));
  doneHead.onclick();
  const doneRows = kids(get("list")).filter(n => n.classList.contains("row") && ["done", "dropped"].includes(n.dataset.id));
  assert.equal(doneRows.length, 2);
  assert.ok(doneRows.every(r => !descendants(r).some(n => n.classList.contains("badge"))));
  assert.ok(doneRows.every(r => r.classList.contains("closed")));

  // The detail: the latest rejection appears only while no newer result exists.
  run(`markdown = (t) => node("p", t); activity = () => node("div"); attemptCard = (a) => node("article", a.id);`);
  context.task = {id: "reworked", title: "Rework then passed", status: "open", spec_revision: 1, revision: 6,
    workstream_ids: ["w"], unresolved_items: [], prerequisites: [], blocked_by: [], gate_proposals: [], body: "", acceptance_criteria: "",
    latest_rejection: {source: "review", verdict: "rework", reasons: "Stale revision", attempt_id: "att_old", workstream_id: "w", spec_revision: 1, timestamp: "2026-10-05T08:00:00.000000Z"},
    attempts: [
      {id: "att_old", state: "rework", spec_revision: 1, workstream_id: "w", revision: 2, implementer: "codex-gpt5 implementer", created_at: "2026-10-05T07:00:00.000000Z"},
      {id: "att_new", state: "passed", spec_revision: 1, workstream_id: "w", revision: 2, implementer: "codex-gpt5 implementer", reviewer: "opus reviewer", created_at: "2026-10-05T09:00:00.000000Z"},
    ]};
  assert.equal(run("taskStanding(task)"), "signoff");
  const bodyText = () => run("body(task)").flatMap(descendants).map(text).join(" ");
  assert.equal(run("currentRejection(task)"), null);
  assert.doesNotMatch(bodyText(), /Latest rejection|Stale revision/);
  assert.match(bodyText(), /Other results/, "Earlier rounds stay in the history");
  context.task.attempts.pop();
  context.task.attempts[0].state = "rework";
  assert.equal(run("taskStanding(task)"), "progress");
  assert.match(bodyText(), /Latest rejection/);
  assert.match(bodyText(), /Stale revision/);

  // The sign-off panel: plain language, the three questions, no IDs or agent labels.
  context.task.attempts.push({id: "att_new", state: "passed", spec_revision: 1, workstream_id: "w", revision: 2,
    implementer: "codex-gpt5 implementer", reviewer: "opus reviewer", created_at: "2026-10-05T09:00:00.000000Z"});
  const panel = run('nextStep(task, "signoff")');
  const prose = descendants(panel).filter(n => n.tag === "p" || n.tag === "strong").map(n => n.textContent).join(" ");
  assert.match(prose, /Ready for your sign-off/);
  assert.match(prose, /Worth doing\? Right approach\? Built well\?/);
  assert.doesNotMatch(prose, /att_|codex|opus|implementer|purpose|result|rework|review round/i);
  const human = {...context.task.attempts[1], state: "human_review"};
  context.task.attempts[1] = human;
  assert.match(descendants(run('nextStep(task, "signoff")')).map(n => n.textContent).join(" "), /You reviewed the delivered work below yourself/);

  // Revised at sign-off: the open question holds it, under the new name.
  context.task.unresolved_items = [{id: "unr", text: "Keep workstream as the term"}];
  assert.equal(run("taskStanding(task)"), "decision");
  assert.match(bodyText(), /Design\/decision/);
  assert.doesNotMatch(bodyText(), /Open questions/);
  const held = descendants(run('nextStep(task, "decision")')).map(n => n.textContent).join(" ");
  assert.match(held, /A design\/decision needs your answer/);
  assert.doesNotMatch(held, /Approve & sign off/);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
