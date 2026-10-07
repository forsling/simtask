// Titled notes in the viewer, against a DOM double (no layout claims): the board reads no
// notes; task, group, workstream and project lists read note titles and update times, with
// archived notes only on request; a note opens with its full text (through the safe
// Markdown renderer), references and times; the add/edit dialog saves title, text and
// picker references together; a stale revision reloads the note instead of overwriting it.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
const classes = (n) => (n.className || "").split(/\s+/).filter(Boolean);
const matches = (n, selector) => selector.split(",").some((s) => classes(n).includes(s.trim().replace(/^\./, "")));
function element(tag = "div") {
  const n = {tag, children: [], textContent: "", disabled: false, hidden: false, style: {}, value: "", dataset: {},
    attributes: {}, className: "", open: false,
    set innerHTML(_) { throw new Error("Note text must never be parsed as HTML"); },
    classList: {set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, toggle() {}, contains(c) { return this.set.has(c); }},
    append(...children) {
      for (let child of children) {
        if (child === null || child === undefined) throw new Error("appended " + child);
        if (typeof child === "string") child = Object.assign(element("#text"), {textContent: child});
        this.children.push(child);
        child.parentElement = this;
      }
    },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    closest(selector) { for (let x = this; x; x = x.parentElement) if (matches(x, selector)) return x; return null; },
    querySelector(selector) { return descendants(this).slice(1).find((x) => matches(x, selector)) || null; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name]; },
    showModal() { this.open = true; }, close() { this.open = false; }, remove() {}, focus() { context.focused = this; }, select() {},
  };
  all.push(n);
  return n;
}
function get(id) {
  const found = [...all].reverse().find((n) => n.id === id && attached(n));
  if (found) return found;
  if (!roots.has(id)) roots.set(id, Object.assign(element(), {id}));
  return roots.get(id);
}
function attached(n) {
  for (let x = n; x; x = x.parentElement) if ([...roots.values()].includes(x)) return true;
  return false;
}
const context = vm.createContext({
  document: {getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag), createDocumentFragment: () => element("#fragment"),
    addEventListener() {}, querySelectorAll() { return []; }},
  window: {addEventListener() {}}, setTimeout() {}, clearTimeout() {}, location: {hash: "", pathname: "/"},
  sessionStorage: {getItem: () => "", setItem() {}}, history: {replaceState() {}, pushState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = (code) => vm.runInContext(code, context);
function descendants(n) { return [n, ...(n.children || []).flatMap(descendants)]; }
const texts = (n) => descendants(n).map((x) => (x.tag === "#text" || !x.children.length ? x.textContent : "")).join("");
const find = (n, cls) => descendants(n).filter((x) => classes(x).includes(cls));
const buttons = (n) => descendants(n).filter((x) => x.tag === "button");
const buttonNamed = (n, label) => buttons(n).find((b) => (b.textContent === label || texts(b) === label || b.attributes["aria-label"] === label) && !b.hidden);
const plain = (value) => JSON.parse(JSON.stringify(value));
const settle = () => new Promise((resolve) => setImmediate(resolve));

const PROJECT = "prj_aaaa1111", OTHER = "prj_bbbb2222", STREAM = "wst_cccc3333";
const NOTE = {id: "note_1", title: "Agreed copy", text: "## Decision\n\nUse **Sign in**.\n\n- Buttons\n- Links", archived: false,
  revision: 2, created_at: "2026-10-01T10:00:00Z", updated_at: "2026-10-02T11:00:00Z", created_by: "agent", updated_by: "simon",
  references: [{kind: "task", id: "login-form"}, {kind: "workstream", id: STREAM}, {kind: "project", id: PROJECT}, {kind: "task", id: "beta-sync"}]};
let responses = {};
run(`state.project = ${JSON.stringify(PROJECT)}; state.stream = ${JSON.stringify(STREAM)};
  state.projects = [{id: ${JSON.stringify(PROJECT)}, name: "alpha"}, {id: ${JSON.stringify(OTHER)}, name: "beta"}];
  state.streams = [{id: ${JSON.stringify(STREAM)}, project_id: ${JSON.stringify(PROJECT)}, branch: "notes-ui"}];
  state.rows = [{id: "login-form", title: "Redesign the login form", project_id: ${JSON.stringify(PROJECT)}, standing: "open"},
    {id: "csv-export", title: "Export the board as CSV", project_id: ${JSON.stringify(PROJECT)}, standing: "open"}];
  ago = () => "2h ago"; activity = () => el("details", "fold activity-stub");
  toasts = []; toast = (text, error = false) => toasts.push({text, error}); requests = [];
  api = async (action, payload) => { requests.push({action, payload}); return respond(action, payload); };`);
context.respond = (action, payload) => {
  const answer = responses[action];
  if (!answer) throw new Error("unexpected " + action);
  return typeof answer === "function" ? answer(payload) : answer;
};
const requests = (action) => plain(context.requests.filter((r) => r.action === action).map((r) => r.payload));
const reset = () => { context.requests.length = 0; context.toasts.length = 0; };

async function test() {
  // The board load reads no notes; the old notes panel is gone.
  responses = {tasks: {items: [{id: "t", title: "T", standing: "open"}], next_offset: null, workstream_order_revision: 1},
    workstreams: {items: run("state.streams"), next_offset: null}};
  run(`saved = {renderDetail, selectTask}; renderDetail = () => {}; selectTask = async () => {};`);
  await run("reload()");
  assert.deepEqual(plain(context.requests.map((r) => r.action).sort()), ["tasks", "workstreams"]);
  assert.equal(run("typeof renderNotes"), "undefined");
  const assets = path.join(__dirname, "../src/task_mcp/viewer_assets");
  assert.doesNotMatch(fs.readFileSync(path.join(assets, "index.html"), "utf8"), /id="notes"/);
  assert.match(fs.readFileSync(path.join(assets, "index.html"), "utf8"), /id="notes-open"/);
  run(`renderDetail = saved.renderDetail; selectTask = saved.selectTask;
    state.rows = [{id: "login-form", title: "Redesign the login form", project_id: ${JSON.stringify(PROJECT)}, standing: "open"},
      {id: "csv-export", title: "Export the board as CSV", project_id: ${JSON.stringify(PROJECT)}, standing: "open"}];`);
  reset();

  // Task detail: rendering reads nothing; loading lists active notes by title and update time.
  const task = {id: "login-form", title: "Redesign the login form", project_id: PROJECT, revision: 3, spec_revision: 1, status: "open",
    standing: "open", workstream_ids: [STREAM], prerequisites: [], blocked_by: [], attempts: [], gate_proposals: [],
    unresolved_items: [], body: "Spec", acceptance_criteria: "Criteria", updated_at: "2026-10-07T00:00:00Z"};
  context.task = task;
  run("state.task = task; renderDetail(task)");
  assert.equal(context.requests.length, 0, "rendering reads nothing");
  let section = find(get("detail"), "notes-block")[0];
  assert.ok(section, "task detail has a Notes section");
  let archivedOn = false;
  responses["note-list"] = (p) => ({reference: p.reference, total: archivedOn ? 2 : 1, next_offset: null,
    items: [...(archivedOn ? [{id: "note_old", title: "Old idea", updated_at: "2026-09-01T00:00:00Z", archived: true}] : []),
      {id: "note_1", title: "Agreed copy", updated_at: "2026-10-02T11:00:00Z"}],
    ...(archivedOn ? {} : {archived_hidden: 1})});
  await run("loadNoteLists()");
  assert.deepEqual(requests("note-list"), [{reference: {kind: "task", id: "login-form"}, include_archived: false, limit: 20, offset: 0}]);
  let rows = find(section, "note-row");
  assert.equal(rows.length, 1);
  assert.equal(find(rows[0], "note-title")[0].textContent, "Agreed copy");
  const time = descendants(rows[0]).find((x) => x.tag === "time");
  assert.equal(time.textContent, "Updated 2h ago");
  assert.equal(time.dateTime, "2026-10-02T11:00:00Z");
  assert.equal(find(section, "question-count")[0].textContent, "1");
  const toggle = buttonNamed(section, "Show 1 archived");
  assert.ok(toggle, "archived notes are offered, not listed");
  assert.ok(buttonNamed(section, "+ Note"));
  // Archived notes appear on request, tagged, and hide again.
  archivedOn = true;
  toggle.onclick();
  await settle();
  assert.equal(requests("note-list").at(-1).include_archived, true);
  rows = find(section, "note-row");
  assert.deepEqual(rows.map((r) => find(r, "note-title")[0].textContent), ["Old idea", "Agreed copy"]);
  assert.equal(find(rows[0], "tag-archived").length, 1);
  assert.equal(toggle.textContent, "Hide archived");
  archivedOn = false;
  toggle.onclick();
  await settle();
  assert.equal(find(section, "note-row").length, 1);
  // No notes: the section says so.
  responses["note-list"] = (p) => ({reference: p.reference, total: 0, items: [], next_offset: null, archived_hidden: 0});
  run("renderDetail(task)");
  await run("loadNoteLists()");
  section = find(get("detail"), "notes-block")[0];
  assert.equal(find(section, "empty-text")[0].textContent, "No notes reference this task yet.");
  assert.ok(!buttons(section).some((b) => !b.hidden && /archived/.test(b.textContent)), "no archived toggle without archived notes");

  // Workstream and project lists, in the Notes panel.
  reset();
  run("openScopeNotes()");
  await settle();
  assert.deepEqual(requests("note-list").map((p) => p.reference), [{kind: "workstream", id: STREAM}, {kind: "project", id: PROJECT}]);
  assert.deepEqual(find(get("detail"), "notes-block").map((s) => descendants(s).find((x) => x.tag === "h3").children[0].textContent),
    ["Workstream notes", "Project notes"]);
  assert.deepEqual(plain(run("state.panel")), {kind: "scope"});
  reset();
  run("state.stream = null; openScopeNotes()");
  await settle();
  assert.deepEqual(requests("note-list").map((p) => p.reference), [{kind: "project", id: PROJECT}]);
  run(`state.stream = ${JSON.stringify(STREAM)}`);

  // Opening a note: full text through the Markdown renderer, references, times.
  reset();
  responses["note-read"] = {items: [NOTE]};
  responses.details = (p) => ({items: p.ids.map((id) => ({id, title: "Sync settings between devices", project_id: OTHER, object_type: "task"}))});
  await run("openNote('note_1', 'scope')");
  assert.deepEqual(requests("note-read"), [{ids: ["note_1"]}]);
  assert.deepEqual(requests("details"), [{ids: ["beta-sync"]}], "only unknown task names are read");
  const detail = get("detail");
  assert.equal(descendants(detail).find((x) => x.tag === "h2").textContent, "Agreed copy");
  const md = find(detail, "md")[0];
  assert.ok(descendants(md).some((x) => x.tag === "h4" && texts(x) === "Decision"));
  assert.ok(descendants(md).some((x) => x.tag === "strong" && x.textContent === "Sign in"));
  assert.equal(descendants(md).filter((x) => x.tag === "li").length, 2);
  const all = texts(detail);
  assert.match(all, /Created .*by agent/);
  assert.match(all, /Updated .*by simon/);
  const refRows = find(detail, "links")[0].children;
  assert.deepEqual(refRows.map((r) => texts(r).replace(/\s+/g, " ")), [
    "TaskRedesign the login formlogin-form", `Workstreamnotes-ui${STREAM}`, `Projectalpha${PROJECT}`,
    "TaskSync settings between devicesbeta-sync · beta"]);
  assert.ok(buttonNamed(detail, "Edit") && buttonNamed(detail, "Archive"));
  assert.ok(!buttons(detail).some((b) => /Delete|Pin/.test(texts(b))), "no delete or pin");
  // One way back on a phone: the crumb link, not also the list's Back button.
  assert.ok(buttonNamed(detail, "Notes") && !buttonNamed(detail, "Back to list"));
  assert.deepEqual(plain(run("state.panel")), {kind: "note", id: "note_1", back: "scope"});

  // Adding: the context reference is prefilled; at least one reference is required.
  reset();
  responses.workstreams = {items: run("state.streams"), next_offset: null};
  responses.groups = {items: [{id: "board-polish", title: "Board polish", project_id: PROJECT}], next_offset: null};
  context.savedAck = null;
  run(`noteDialog(null, {references: [{kind: "project", id: ${JSON.stringify(PROJECT)}}], saved: (ack) => { savedAck = ack; }})`);
  await settle();
  assert.equal(get("dialog-title").textContent, "Add a note");
  const fields = get("fields");
  const chipTitles = () => find(fields, "ref-chip").map((c) => find(c, "ref-title")[0].textContent);
  assert.deepEqual(chipTitles(), ["alpha"]);
  const local = (values) => run("submitAction")(new Map(Object.entries(values))).then(() => null, (e) => e);
  let error = await local({title: "", text: "x"});
  assert.ok(error.local && /title/.test(error.message));
  buttonNamed(fields, "Remove reference: Project alpha").onclick();
  assert.deepEqual(chipTitles(), []);
  assert.match(texts(find(fields, "ref-chips")[0]), /None yet/);
  error = await local({title: "T", text: "Body"});
  assert.ok(error.local && /at least one reference/.test(error.message), error.message);
  assert.equal(requests("note-create").length, 0, "nothing is sent without a reference");
  // The picker: typing searches candidates of every kind; choosing adds a chip.
  const search = get("field-ref-search");
  search.value = "polish";
  search.oninput();
  assert.deepEqual(find(fields, "ref-option").map((b) => b.attributes["aria-label"]), ["Add group Board polish"]);
  find(fields, "ref-option")[0].onclick();
  search.value = "notes-ui";
  search.oninput();
  // Enter adds the first match and is kept from submitting the form.
  let prevented = false;
  search.onkeydown({key: "Enter", preventDefault() { prevented = true; }});
  assert.ok(prevented);
  search.value = "beta";
  search.oninput();
  buttonNamed(fields, "Add project beta").onclick();
  // A task elsewhere, by its complete ID.
  search.value = "beta-sync";
  search.oninput();
  await buttonNamed(fields, "Look up “beta-sync” by ID").onclick();
  assert.deepEqual(chipTitles(), ["Board polish", "notes-ui", "beta", "Sync settings between devices"]);
  responses["note-create"] = (p) => ({id: "note_new", revision: 1, title: p.title, changed: true});
  const result = await run("submitAction")(new Map([["title", "Picker note"], ["text", "Written **here**."]]));
  assert.deepEqual(requests("note-create"), [{title: "Picker note", text: "Written **here**.", references: [
    {kind: "group", id: "board-polish"}, {kind: "workstream", id: STREAM}, {kind: "project", id: OTHER}, {kind: "task", id: "beta-sync"}]}]);
  await result.afterSave();
  assert.equal(context.savedAck.id, "note_new");
  assert.equal(context.toasts.at(-1).text, "Note added.");
  // A Store refusal of the note's own fields is shown plainly, with the draft kept.
  responses["note-create"] = () => { throw new Error("note_title_too_long: a title holds at most 120 characters; this one has 121"); };
  error = await local({title: "x".repeat(121), text: "Body"});
  assert.equal(error.local, true);
  assert.equal(error.message, "Nothing was saved. A title holds at most 120 characters; this one has 121.");

  // In a workstream view the picker also finds the project's tasks outside the view by
  // title (read through the board's tasks listing when it opens), and other projects' tasks
  // once the user types; the complete-ID lookup stays the fallback.
  run(`state.stream = ${JSON.stringify(STREAM)}; state.unassigned = false; state.groups = false;
    state.rows = [{id: "login-form", title: "Redesign the login form", project_id: ${JSON.stringify(PROJECT)}, standing: "open"}];`);
  reset();
  const boardTasks = responses.tasks;
  responses.tasks = (p) => ({next_offset: null, items: p.project === PROJECT
    ? [{id: "login-form", title: "Redesign the login form", project_id: PROJECT, object_type: "task"},
       {id: "csv-export", title: "Export the board as CSV", project_id: PROJECT, object_type: "task"}]
    : [{id: "beta-sync", title: "Sync settings between devices", project_id: OTHER, object_type: "task"}]});
  run(`noteDialog(null, {references: [{kind: "workstream", id: ${JSON.stringify(STREAM)}}]})`);
  const pickerFields = get("fields");
  const pickerSearch = get("field-ref-search");
  const offered = () => find(pickerFields, "ref-option").map((b) => b.attributes["aria-label"] || texts(b));
  pickerSearch.value = "export";
  pickerSearch.oninput();
  assert.deepEqual(offered(), ["Look up “export” by ID"], "before the read, only the ID lookup");
  await settle();
  assert.deepEqual(offered(), ["Add task Export the board as CSV"]);
  assert.deepEqual(requests("tasks").sort((x, y) => x.project.localeCompare(y.project)),
    [{project: PROJECT, limit: 100, offset: 0}, {project: OTHER, limit: 100, offset: 0}],
    "the project's tasks on open, the other project's once typing starts");
  pickerSearch.value = "sync settings";
  pickerSearch.oninput();
  await settle();
  assert.deepEqual(offered(), ["Add task Sync settings between devices"]);
  assert.match(texts(find(pickerFields, "ref-option")[0]), /beta-sync · beta/);
  assert.equal(requests("tasks").length, 2, "other projects are read once per picker");
  // Adding a reference clears the missing-reference error at once.
  get("form-error").textContent = run("NO_REFERENCES");
  pickerSearch.value = "export";
  pickerSearch.oninput();
  pickerSearch.onkeydown({key: "Enter", preventDefault() {}});
  assert.deepEqual(find(pickerFields, "ref-chip").map((c) => find(c, "ref-title")[0].textContent), ["notes-ui", "Export the board as CSV"]);
  assert.equal(get("form-error").textContent, "");
  // A failed tasks read leaves the ID lookup; All tasks already shows every project task.
  reset();
  responses.tasks = () => { throw new Error("tasks unavailable"); };
  run(`noteDialog(null, {references: [{kind: "workstream", id: ${JSON.stringify(STREAM)}}]})`);
  await settle();
  get("field-ref-search").value = "csv-export";
  get("field-ref-search").oninput();
  await settle();
  assert.deepEqual(find(get("fields"), "ref-option").map((b) => texts(b)), ["Look up “csv-export” by ID"]);
  run("state.stream = null");
  reset();
  run(`noteDialog(null, {references: [{kind: "project", id: ${JSON.stringify(PROJECT)}}]})`);
  await settle();
  assert.deepEqual(requests("tasks"), [], "All tasks reads no extra tasks when the picker opens");
  // Other projects' tasks are bounded: at most 5 pages of 100 per project.
  reset();
  responses.tasks = (p) => ({items: [{id: "t" + p.offset, title: "T", project_id: OTHER, object_type: "task"}], next_offset: p.offset + 100});
  assert.equal((await run("otherProjectTasks()")).length, 5);
  assert.deepEqual(requests("tasks").map((r) => [r.project, r.offset]), [0, 100, 200, 300, 400].map((o) => [OTHER, o]));
  responses.tasks = boardTasks;
  run(`state.stream = ${JSON.stringify(STREAM)}`);

  // Editing at a stale revision: nothing is overwritten; the note reloads beside the draft.
  reset();
  const latest = {...NOTE, revision: 3, text: "Changed by an agent.", updated_by: "agent-2", references: [{kind: "project", id: PROJECT}]};
  run("state.panel = {kind: 'note', id: 'note_1', back: 'scope'}");
  context.edited = null;
  run(`noteDialog(${JSON.stringify(NOTE)}, {saved: () => { edited = true; }})`);
  await settle();
  assert.equal(get("dialog-title").textContent, "Edit note");
  assert.equal(get("field-title").value, "Agreed copy");
  assert.equal(chipTitles().length, 4);
  get("field-text").value = "My draft.";
  let updates = 0;
  responses["note-update"] = (p) => {
    updates++;
    if (p.expected_revision !== 3) throw Object.assign(new Error("revision_conflict: expected 2, current 3"), {conflict: true});
    return {id: "note_1", revision: 4, changed: true};
  };
  responses["note-read"] = {items: [latest]};
  error = await local({title: "Agreed copy", text: "My draft."});
  assert.ok(error.local && /changed elsewhere, so nothing was saved/.test(error.message), error.message);
  assert.equal(updates, 1, "a stale save is not retried");
  assert.deepEqual(requests("note-update")[0], {note_id: "note_1", expected_revision: 2, title: "Agreed copy", text: "My draft.",
    references: NOTE.references});
  assert.deepEqual(requests("note-read"), [{ids: ["note_1"]}], "the note reloads");
  const conflict = find(get("conflict"), "conflict-box")[0];
  assert.match(texts(conflict), /Changed by an agent\./);
  assert.match(texts(conflict), /by agent-2/);
  assert.equal(get("field-text").value, "My draft.", "the draft is kept");
  assert.match(texts(find(get("detail"), "md")[0]), /Changed by an agent\./, "the note behind the dialog shows the current version");
  // Keep my draft: adopts the current revision; the next save sends the draft against it.
  buttonNamed(conflict, "Keep my draft").onclick();
  assert.match(get("form-error").textContent, /replaces the current version/);
  let saved = await run("submitAction")(new Map([["title", "Agreed copy"], ["text", "My draft."]]));
  assert.equal(requests("note-update").at(-1).expected_revision, 3);
  await saved.afterSave();
  assert.equal(context.edited, true);
  // Edit the current version: the form takes the current title, text and references.
  reset();
  updates = 0;
  run(`noteDialog(${JSON.stringify(NOTE)}, {saved: () => {}})`);
  await settle();
  error = await local({title: "Agreed copy", text: "Another draft."});
  assert.ok(error.local);
  buttonNamed(find(get("conflict"), "conflict-box")[0], "Edit the current version").onclick();
  assert.equal(get("field-text").value, "Changed by an agent.");
  assert.deepEqual(chipTitles(), ["alpha"]);
  saved = await run("submitAction")(new Map([["title", "Agreed copy"], ["text", "Changed by an agent. Plus mine."]]));
  assert.deepEqual(requests("note-update").at(-1), {note_id: "note_1", expected_revision: 3, title: "Agreed copy",
    text: "Changed by an agent. Plus mine.", references: [{kind: "project", id: PROJECT}]});

  // Archive and unarchive at the shown revision; a stale one saves nothing and reloads.
  reset();
  responses["note-update"] = (p) => ({id: "note_1", revision: p.expected_revision + 1, changed: true});
  responses["note-read"] = {items: [{...NOTE, archived: true, revision: 3}]};
  await run(`archiveNote(${JSON.stringify(NOTE)}, "scope", true)`);
  assert.deepEqual(requests("note-update"), [{note_id: "note_1", expected_revision: 2, archived: true}]);
  assert.match(context.toasts.at(-1).text, /Note archived/);
  assert.ok(buttonNamed(get("detail"), "Unarchive"), "the reloaded note offers Unarchive");
  assert.match(texts(get("detail")), /Archived/);
  reset();
  responses["note-update"] = () => { throw Object.assign(new Error("revision_conflict"), {conflict: true}); };
  responses["note-read"] = {items: [latest]};
  await run(`archiveNote({...${JSON.stringify(NOTE)}, archived: true, revision: 3}, "scope", false)`);
  assert.deepEqual(plain(context.toasts.at(-1)), {text: "This note changed elsewhere, so nothing was saved. Showing its current version; try again if you still want to.", error: true});
  assert.deepEqual(requests("note-read"), [{ids: ["note_1"]}]);
  assert.match(texts(find(get("detail"), "md")[0]), /Changed by an agent\./);

  // A row click or navigation closes the note; a quiet refresh keeps it open.
  run("state.panel = {kind: 'note', id: 'note_1', back: null}");
  responses.details = {items: [task]};
  reset();
  await run("selectTask('login-form', {quiet: true})");
  assert.equal(run("state.panel?.kind"), "note");
  assert.deepEqual(requests("note-read"), [{ids: ["note_1"]}]);
  await run("selectTask('login-form')");
  assert.equal(run("state.panel"), null);
  assert.equal(descendants(get("detail")).find((x) => x.tag === "h2").textContent, "Redesign the login form");
  console.log("viewer notes tests passed");
}
test().catch((error) => { console.error(error); process.exit(1); });
