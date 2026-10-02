// Render actual prerequisite handlers from compact facts, with no eager proof reads.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
function element() {
  return {textContent: "", children: [], classList: {add() {}, remove() {}, toggle() {}},
    append(...children) {this.children.push(...children);}, setAttribute() {},
    replaceChildren(...children) {this.children = children;}};
}
const context = vm.createContext({
  document: {getElementById: element, createElement: element, addEventListener() {}},
  window: {addEventListener() {}}, setTimeout() {}, location: {hash: ""},
  sessionStorage: {getItem() {return "";}}, history: {replaceState() {}},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
const run = code => vm.runInContext(code, context);
run(source.replace(/boot\(\);\s*$/, ""));
run('markdown = (s) => node("p", s); activity = () => null; icon = () => node("i");');
function text(n) {
  return typeof n === "string" ? n : [n?.textContent || "", ...(n?.children || []).map(text)].join(" ");
}
function descendants(n) {
  return [n, ...(n.children || []).filter(c => typeof c === "object").flatMap(descendants)];
}
context.task = {id: "local", status: "open", spec_revision: 1, body: "Local requirements",
  acceptance_criteria: "Observable", attempts: [], unresolved_items: [], gate_proposals: [],
  blocked_by: ["remote", "global-group"], prerequisites: [
    {id: "remote", title: "Remote task <script>", object_type: "task", project_id: "p2",
      project_name: "Second project", state: "deferred", complete: false, blocking: true},
    {id: "global-group", title: "Global feature", object_type: "group", project_id: null,
      project_name: null, state: "complete", complete: true, blocking: false},
  ]};
let reads = 0, navigated, openedGroup;
context.apiMock = async () => {reads++; throw Error("Rendering must not fetch remote proof");};
context.navigateMock = async p => {navigated = p;};
context.groupMock = async id => {openedGroup = id;};
run('api = apiMock; navigateMember = navigateMock; openGroup = groupMock;');
async function main() {
  const section = run('body(task)').find(n => text(n).includes("Prerequisites"));
  assert.equal(reads, 0);
  assert.match(text(section), /Remote task <script>.*remote.*Second project.*p2.*Blocking · deferred/);
  assert.match(text(section), /Global feature.*global-group.*Global group.*Complete/);
  const links = descendants(section).filter(n => n.onclick);
  assert.equal(links.length, 2);
  await links[0].onclick();
  assert.equal(navigated.id, "remote");
  assert.equal(navigated.project_id, "p2");
  await links[1].onclick();
  assert.equal(openedGroup, "global-group");
  assert.equal(reads, 0);
  // A group's canonical open disposition must not override aggregate completion.
  context.task.prerequisites[0].state = "done";
  context.task.prerequisites[0].complete = true;
  context.task.prerequisites[0].blocking = false;
  assert.doesNotMatch(run('body(task)').map(text).join(" "), /Blocking · deferred/);
}
main().catch(e => {console.error(e); process.exitCode = 1;});
