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
    value: "", required: false, dataset: {},
    set innerHTML(_) { throw new Error("Task text must never be parsed as HTML"); },
    classList: {add() {}, remove() {}, toggle() {}},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    setAttribute() {}, querySelector() { return null; }, showModal() {}, close() {}, focus() {},
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
  document: {getElementById: get, createElement: element, createElementNS: (_, tag) => element(tag),
    createTextNode: text => Object.assign(element(), {textContent: text}), addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: "", pathname: "/w/aaaa1111"},
  sessionStorage: {getItem: () => ""}, history: {replaceState() {}, pushState(_, __, p) { context.pushed = p; }},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
const run = code => vm.runInContext(code, context);
const descendants = node => [node, ...(node.children || []).flatMap(descendants)];
const texts = node => descendants(node).map(n => n.textContent).join(" ");

async function test() {
  run(`state.projects = [{id: "prj_bbbb2222", name: "Demo"}]; state.project = "prj_bbbb2222";
    state.streams = [{id: "wst_aaaa1111", project_id: "prj_bbbb2222", branch: "main"}]; state.stream = "wst_aaaa1111";
    toasts = []; toast = (text, error = false) => toasts.push({text, error});
    requests = []; api = async (action, payload) => {requests.push({action, payload});
      return {id: "colour-code-stale-workstreams", project_id: "prj_bbbb2222", revision: 1, workstream_ids: []};};
    reloads = 0; reload = async () => { reloads++; };`);

  // The dialog names where the idea goes: the project's inbox, not this workstream.
  run("captureIdea()");
  assert.equal(get("dialog-title").textContent, "Save an idea");
  assert.match(get("dialog-description").textContent, /inbox of Demo, not this workstream.*Needs input/);
  assert.equal(get("submit").textContent, "Save idea");
  assert.equal(get("field-text").required, true);
  assert.equal(get("field-text").maxLength, 200);
  assert.equal(get("field-note").required, false);
  assert.equal(get("field-note").maxLength, 500);
  // A blank line saves nothing.
  context.values = new Map([["text", "  "], ["note", ""]]);
  await assert.rejects(run("submitAction(values)"), error => error.local);
  assert.equal(context.requests.length, 0);
  context.values = new Map([["text", "Colour-code stale workstreams"], ["note", "After a week."]]);
  const result = await run("submitAction(values)");
  assert.deepEqual(JSON.parse(JSON.stringify(context.requests)), [{action: "idea",
    payload: {project: "prj_bbbb2222", text: "Colour-code stale workstreams", note: "After a week."}}]);
  // Saving confirms and shows the idea in the project's All tasks, selected.
  await result.afterSave();
  assert.match(context.toasts.at(-1).text, /Idea saved to the inbox.*Needs input/);
  assert.equal(run("state.stream"), null);
  assert.equal(run("state.selected"), "colour-code-stale-workstreams");
  assert.equal(context.pushed, "/p/bbbb2222/t/id/colour-code-stale-workstreams");
  assert.equal(context.reloads, 1);

  // An idea's next step hands it to an agent instead of offering to build it.
  const idea = {id: "colour-code-stale-workstreams", title: "Colour-code stale workstreams", project_id: "prj_bbbb2222",
    status: "open", workstream_ids: [], prerequisites: [], attempts: [], spec_revision: 1,
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
  assert.doesNotMatch(texts(run("nextStep(idea, taskStanding(idea))")), /idea/i);
}
test().catch(error => {console.error(error); process.exitCode = 1;});
