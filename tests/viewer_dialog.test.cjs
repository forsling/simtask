// Exercise the shipped dialog handlers with controlled async completion and a
// tiny DOM double. This is a lifecycle regression, not a browser/layout test.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    textContent: "", disabled: false, open: false, closeCount: 0,
    replaceChildren() {},
    showModal() { this.open = true; },
    close() { this.open = false; this.closeCount++; },
  });
  return elements.get(id);
}
const context = vm.createContext({
  document: {getElementById: element},
  location: {hash: ""}, sessionStorage: {getItem: () => ""},
  history: {replaceState() {}}, FormData: class {},
});
const source = fs.readFileSync(path.join(__dirname, "../src/task_mcp/viewer_assets/app.js"), "utf8");
vm.runInContext(source.replace(/boot\(\);\s*$/, ""), context);
async function run() {
  let complete;
  context.pending = new Promise(resolve => { complete = resolve; });
  vm.runInContext('openDialog("A", "Saving A", "Save"); submitAction = () => pending;', context);
  const saving = element("form").onsubmit({preventDefault() {}});
  assert.equal(element("submit").disabled, true);
  assert.equal(element("cancel").disabled, true);
  assert.equal(element("close").disabled, true);
  element("cancel").onclick();
  element("close").onclick();
  let prevented = false;
  element("dialog").oncancel({preventDefault() { prevented = true; }});
  assert.equal(prevented, true, "Escape must not dismiss a pending write");
  assert.equal(element("dialog").open, true);
  assert.equal(element("dialog").closeCount, 0);
  vm.runInContext('openDialog("B", "Draft B", "Save");', context);
  assert.equal(element("dialog-title").textContent, "A");
  complete({stopped: true}); // Avoid unrelated queue loading in this unit harness.
  await saving;
  assert.equal(element("dialog").closeCount, 1);
  assert.equal(element("cancel").disabled, false);
  vm.runInContext('openDialog("B", "Draft B", "Save");', context);
  assert.equal(element("dialog").open, true);
  assert.equal(element("dialog-title").textContent, "B");
  context.failure = Promise.reject(new Error("Synthetic save failure"));
  vm.runInContext('submitAction = () => failure;', context);
  await element("form").onsubmit({preventDefault() {}});
  assert.equal(element("dialog").open, true, "Failures keep the draft open");
  assert.match(element("form-error").textContent, /Synthetic save failure/);
  assert.equal(element("cancel").disabled, false);
  element("cancel").onclick();
  assert.equal(element("dialog").open, false, "Cancel works again after completion");
}
run().catch(error => { console.error(error); process.exitCode = 1; });
