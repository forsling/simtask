// Exercise the shipped + Idea control: its dialog, payload, confirmation and the idea's
// next-step hand-off. This verifies form/payload behavior; it makes no layout claim.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const all = [];
const roots = new Map();
function element(tag = "div") {
  const node = {tag, children: [], textContent: "", disabled: false, hidden: false, style: {},
    value: "", required: false, dataset: {}, open: false, closeCount: 0,
    set innerHTML(_) { throw new Error("Task text must never be parsed as HTML"); },
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute() {}, querySelector() { return null; },
    showModal() { this.open = true; }, close() { this.open = false; this.closeCount++; }, focus() {},
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
// Model the shipped form's field subtree rather than supplying a hand-written payload.
// FormData here implements successful named controls; this remains a DOM double.
get("form").append(get("fields"));
class FormData {
  constructor(form) {
    this.values = new Map(descendants(form).filter(n => n.name && !n.disabled).map(n => [n.name, n.value]));
  }
  get(name) { return this.values.get(name) ?? null; }
}
const context = vm.createContext({
  FormData,
  document: {getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag),
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: "", pathname: "/w/aaaa1111"},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}, pushState(_, __, p) { context.pushed = p; }},
});
let source = fs.readFileSync(path.join(__dirname, "../src/simtask/viewer_assets/app.js"), "utf8");
const run = code => vm.runInContext(code, context);
const descendants = node => [node, ...(node.children || []).flatMap(descendants)];
const texts = node => descendants(node).map(n => n.textContent).join(" ");

async function test() {
  const [origin, token, project] = process.argv.slice(2);
  if (origin) {
    source = await (await fetch(origin + "/app.js")).text();
    const html = await (await fetch(origin + "/")).text();
    assert.match(html, /<form id="form">[\s\S]*<div id="fields"[\s\S]*id="submit"[^>]*type="submit"/);
    context.fetch = (url, options) => fetch(origin + url, {...options,
      headers: {...options.headers, Origin: origin, "X-Task-Token": token}});
  }
  vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
  run(`state.projects = [{id: "prj_bbbb2222", name: "Demo"}]; state.project = "prj_bbbb2222";
    state.streams = [{id: "wst_aaaa1111", project_id: "prj_bbbb2222", branch: "main"}]; state.stream = "wst_aaaa1111";
    toasts = []; toast = (text, error = false) => toasts.push({text, error});
    requests = []; refusal = null; api = async (action, payload) => {requests.push({action, payload});
      if (action === "idea-id") return {public_id: "colour-code-stale-workstreams", limit: 40};
      if (refusal) throw new Error(refusal);
      return {id: payload.public_id || "colour-code-stale-workstreams", project_id: "prj_bbbb2222", revision: 1, workstream_ids: []};};
    reloads = 0; reload = async () => { reloads++; };
    // Run the ID prefill's debounce at once, keeping its promise to await.
    pending = null; setTimeout = (f) => { pending = f(); return 1; }; clearTimeout = () => {};`);
  const typeTitle = async (value) => {
    get("field-text").value = value;
    get("field-text").oninput();
    await context.pending;
  };
  const typeId = (value) => {
    get("field-public_id").value = value;
    get("field-public_id").oninput();
  };
  const submit = () => get("form").onsubmit({preventDefault() {}});

  if (origin) run(`state.project = ${JSON.stringify(project)}; state.projects = [{id: ${JSON.stringify(project)}, name: "Demo"}];`);

  // The dialog names where the idea goes: the project's Unassigned tasks, not this
  // workstream, until it is assigned or processed.
  run("captureIdea()");
  assert.equal(get("dialog-title").textContent, "Save an idea");
  assert.match(get("dialog-description").textContent, /saved to Unassigned in Demo, not this workstream.*Design until it is assigned or processed/);
  assert.doesNotMatch(get("dialog-description").textContent, /inbox/i);
  assert.equal(get("submit").textContent, "Save idea");
  assert.equal(get("field-text").required, true);
  assert.equal(get("field-text").maxLength, 200);
  assert.equal(get("field-note").required, false);
  assert.equal(get("field-note").maxLength, 500);
  assert.equal(get("field-text").tag, "input");
  assert.equal(get("field-note").tag, "textarea");
  assert.equal(get("field-note").rows, 4);
  assert.match(texts(get("fields")), /Idea title \(required\).*ID.*filled in from the title.*Details \(optional\)/);
  // The ID is optional, editable and prefilled from the title by the server's rule.
  assert.equal(get("field-public_id").tag, "input");
  assert.equal(get("field-public_id").required, false);
  assert.equal(get("field-public_id").value, "");
  // A blank line saves nothing; submit through the shipped handler and FormData.
  get("field-text").value = "  ";
  await get("form").onsubmit({preventDefault() {}});
  assert.match(get("form-error").textContent, /Write a short idea title/);
  assert.equal(get("dialog").open, true);
  assert.equal(context.requests.length, 0);
  await typeTitle("Colour-code stale workstreams");
  assert.equal(get("field-public_id").value, "colour-code-stale-workstreams");
  get("field-note").value = "After a week.\n\n  Keep indentation.  ";
  if (origin) {
    // Exercise the shipped api() and the actual dispatch on a disposable database.
    run(source.slice(source.indexOf("async function api("), source.indexOf("async function pages(")));
    get("field-note").value = "x".repeat(501);
    await get("form").onsubmit({preventDefault() {}});
    assert.equal(get("dialog").open, true);
    assert.match(get("form-error").textContent, /details of up to 500 characters/);
    assert.equal(get("field-note").value, "x".repeat(501), "Backend errors preserve the draft");
    assert.equal(get("field-text").value, "Colour-code stale workstreams");
    get("field-note").value = "After a week.\n\n  Keep indentation.  ";
    // Invalid, over-long and taken IDs are refused plainly; the draft stays.
    for (const [value, message] of [
      ["x".repeat(41), /^An ID has at most 40 characters; this one has 41\. Choose a shorter ID\.$/],
      ["Stale Colours", /^Use up to 40 lowercase letters, digits and single hyphens, beginning with a letter.*Choose another ID\.$/],
      ["taken-idea", /^The ID “taken-idea” is already taken; IDs stay reserved even after a task closes\. Choose another ID\.$/],
    ]) {
      typeId(value);
      await submit();
      assert.equal(get("dialog").open, true);
      assert.match(get("form-error").textContent, message);
      assert.equal(get("field-public_id").value, value);
    }
    // An edited ID stays put while the title changes, and is saved as typed.
    typeId("stale-colours");
    await typeTitle("Colour-code stale workstreams");
    assert.equal(get("field-public_id").value, "stale-colours");
    await submit();
    assert.equal(get("dialog").open, false);
    assert.equal(get("form-error").textContent, "");
    assert.equal(run("state.stream"), null);
    assert.equal(run("state.unassigned"), true);
    const id = run("state.selected");
    assert.equal(id, "stale-colours");
    assert.equal(context.pushed, `/p/${project.slice(4, 12)}/u/t/id/${id}`);
    assert.equal(context.reloads, 1);
    console.log(JSON.stringify({id}));
    return;
  }
  await get("form").onsubmit({preventDefault() {}});
  // An untouched prefill sends no ID: the server derives it from the final title.
  assert.deepEqual(JSON.parse(JSON.stringify(context.requests)), [
    {action: "idea-id", payload: {title: "Colour-code stale workstreams"}},
    {action: "idea", payload: {project: "prj_bbbb2222", text: "Colour-code stale workstreams", note: "After a week.\n\n  Keep indentation.  "}}]);
  // Saving confirms and shows the idea in the project's Unassigned view, selected.
  assert.equal(get("dialog").open, false);
  assert.match(context.toasts.at(-1).text, /Idea saved to Unassigned.*Design until it is assigned or processed/);
  assert.equal(run("state.stream"), null);
  assert.equal(run("state.unassigned"), true);
  assert.equal(run("state.selected"), "colour-code-stale-workstreams");
  assert.equal(context.pushed, "/p/bbbb2222/u/t/id/colour-code-stale-workstreams");
  assert.equal(context.reloads, 1);

  // An edited ID is sent as typed; clearing the title clears an untouched prefill only.
  run("captureIdea(); requests.length = 0");
  await typeTitle("Dark mode");
  assert.equal(get("field-public_id").value, "colour-code-stale-workstreams");
  await typeTitle("");
  assert.equal(get("field-public_id").value, "");
  typeId("  dark-mode ");
  await typeTitle("Dark mode for the viewer");
  assert.equal(get("field-public_id").value, "  dark-mode ");
  assert.equal(context.requests.filter(r => r.action === "idea-id").length, 1);
  // A refusal from the server is shown in plain words and nothing closes.
  run(`refusal = "public_id_conflict: dark-mode is already reserved; choose a different descriptive public_id and retry (closed task IDs cannot be reused)"`);
  await submit();
  assert.equal(get("dialog").open, true);
  assert.equal(get("form-error").textContent, "The ID “dark-mode” is already taken; IDs stay reserved even after a task closes. Choose another ID.");
  run("refusal = null");
  await submit();
  assert.equal(get("dialog").open, false);
  assert.deepEqual(JSON.parse(JSON.stringify(context.requests.filter(r => r.action === "idea").at(-1).payload)),
    {project: "prj_bbbb2222", text: "Dark mode for the viewer", note: "", public_id: "dark-mode"});
  assert.equal(run("state.selected"), "dark-mode");

  // An idea's next step hands it to an agent instead of offering to build it.
  const idea = {id: "colour-code-stale-workstreams", title: "Colour-code stale workstreams", project_id: "prj_bbbb2222",
    status: "open", standing: "decision", workstream_ids: [], prerequisites: [], attempts: [], spec_revision: 1,
    unresolved_items: [{id: "unr_1", text: "Idea to process: turn into a proper brief or task with the user"}]};
  run("state.stream = null");
  context.idea = idea;
  assert.equal(run("taskStanding(idea)"), "decision");
  const panel = run("nextStep(idea, taskStanding(idea))");
  assert.match(texts(panel), /An idea waiting to be processed/);
  assert.doesNotMatch(texts(panel), /Add this task to a workstream/);
  const go = descendants(panel).find(n => n.textContent === "Go through it with an agent");
  context.navigator = {clipboard: {writeText: async text => { context.copied = text; }}};
  await go.onclick({currentTarget: go});
  assert.equal(context.copied, "Go through my ideas, starting with colour-code-stale-workstreams — Colour-code stale workstreams");
  assert.equal(context.toasts.at(-1).text, "Copied. Paste it into your agent to start the walkthrough.");
  // Once the item is resolved it is an ordinary task again.
  idea.unresolved_items = [];
  idea.standing = "open";
  assert.doesNotMatch(texts(run("nextStep(idea, taskStanding(idea))")), /idea/i);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
