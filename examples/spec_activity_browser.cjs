// Check task details' Spec and Activity tabs in headless Chrome, on a disposable viewer
// over a copy made by examples/spec_activity_seed.py (never the live database):
//
//   python examples/spec_activity_seed.py COPY --copy-from ~/.local/share/task-mcp/tasks.sqlite3 > ids.json
//   TASK_MCP_DB=COPY ./run.sh        # prints the private link
//   node examples/spec_activity_browser.cjs LINK COPY ids.json SHOTS_DIR
//   TASK_MCP_DB=COPY ./run.sh --stop
//
// Needs google-chrome and Node 22 or later (global WebSocket). It drives the page over the
// DevTools protocol, wraps window.fetch to log Activity reads and to simulate a failing
// or empty page, and writes one question to COPY between loads. Screenshots go to SHOTS_DIR.
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");


async function launch(profile, port = 9333) {
  fs.mkdirSync(profile, { recursive: true });
  const chrome = spawn("google-chrome", ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900", "about:blank"],
    { stdio: "ignore" });
  for (let i = 0; i < 200; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (r.ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const waiting = new Map();
  const listeners = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && waiting.has(msg.id)) {
      const { resolve, reject } = waiting.get(msg.id);
      waiting.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    } else if (msg.method) listeners.forEach((l) => l(msg));
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const n = ++id;
    waiting.set(n, { resolve, reject });
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  await send("Page.enable");
  await send("Runtime.enable");
  const consoleErrors = [];
  listeners.push((msg) => {
    if (msg.method === "Runtime.exceptionThrown") consoleErrors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") consoleErrors.push(msg.params.args.map((a) => a.value ?? a.description).join(" "));
  });
  const api = {
    send,
    consoleErrors,
    async viewport(width, height, mobile = false) {
      await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
    },
    async goto(url) {
      await send("Page.navigate", { url });
      await new Promise((r) => setTimeout(r, 800));
    },
    async eval(expression) {
      const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async waitFor(expression, timeout = 8000) {
      const start = Date.now();
      while (Date.now() - start < timeout) {
        if (await api.eval(`!!(${expression})`)) return true;
        await new Promise((r) => setTimeout(r, 100));
      }
      throw new Error("Timed out waiting for " + expression);
    },
    async key(key, code = key, keyCode = 0) {
      for (const type of ["keyDown", "keyUp"])
        await send("Input.dispatchKeyEvent", { type, key, code, windowsVirtualKeyCode: keyCode });
    },
    async shot(file) {
      const r = await send("Page.captureScreenshot", { format: "png" });
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, Buffer.from(r.data, "base64"));
    },
    async close() {
      ws.close();
      chrome.kill();
    },
  };
  return api;
}

const [launchUrl, db, idsFile, shots] = process.argv.slice(2);
const ids = JSON.parse(fs.readFileSync(idsFile, "utf8"));
const repo = path.resolve(__dirname, "..");
const origin = launchUrl.split("/#")[0];
const at = (p) => origin + p;
const results = [];
function ok(name, detail = "") {
  results.push(`ok   ${name}${detail ? " — " + detail : ""}`);
  console.log(results.at(-1));
}

(async () => {
  // CHROME_PROFILE picks the throwaway Chrome profile directory (default: a new temporary one).
  const b = await launch(process.env.CHROME_PROFILE || fs.mkdtempSync(path.join(require("node:os").tmpdir(), "spec-activity-chrome-")));
  try {
    await b.viewport(1280, 900);
    await b.goto(launchUrl);
    await b.waitFor(`document.querySelector(".row")`);
    // Log every activity/attempt request the page makes.
    const instrument = `(() => { if (window.__log) return; window.__log = []; const f = window.fetch;
      window.__fail = null; window.__empty = null;
      window.fetch = async (url, opts) => { const body = JSON.parse(opts?.body || "{}");
        if (/api\\/(activity|attempt)$/.test(url)) window.__log.push({url: url.replace(/.*api\\//, ""), ...body});
        if (window.__fail && /api\\/activity$/.test(url) && window.__fail(body)) return new Response(JSON.stringify({error: "Simulated failure"}), {status: 500, headers: {"Content-Type": "application/json"}});
        if (window.__empty && /api\\/activity$/.test(url) && body.task_id === window.__empty) return new Response(JSON.stringify({task_id: body.task_id, object_type: "task", items: [], newest_sequence: null}), {headers: {"Content-Type": "application/json"}});
        return f(url, opts); }; })()`;
    const open = async (p) => {
      await b.goto(at(p));
      await b.eval(instrument);
      await b.waitFor(`document.querySelector("#detail-tabs")`);
    };
    const entries = () => b.eval(`document.querySelectorAll("#panel-activity li.entry").length`);
    const text = (sel) => b.eval(`(document.querySelector(${JSON.stringify(sel)})?.innerText || "")`);

    // 1. Awaiting sign-off: tabs, identity and next step above them, the card.
    await open(ids.paths.signoff);
    const layout = await b.eval(`(() => { const c = document.querySelector(".content");
      return [...c.children].map(n => n.getAttribute("role") || n.className); })()`);
    assert.deepEqual(layout, ["detail-head", "next tone-go", "tablist", "tabpanel", "tabpanel"]);
    assert.equal(await b.eval(`document.querySelector("#tab-spec").getAttribute("aria-selected")`), "true");
    assert.ok((await text(".next")).includes("Sign off with an agent"));
    const card = await text(".result-card");
    assert.match(card, /Passed independent review; waiting for your sign-off/);
    assert.match(card, new RegExp(ids.alt_name));
    assert.match(card, /spec v2 · current/);
    assert.match(card, /d9464f2/);
    const spec = await text("#panel-spec");
    for (const gone of ["Latest rejection", "Other results", "Sign-off decisions", "Evidence for:"]) assert.ok(!spec.includes(gone), gone);
    assert.equal(await b.eval(`window.__log.length`), 0, "Activity not read before it is shown");
    ok("sign-off task opens on Spec; identity, status, Sign off with an agent above tabs; compact card", card.split("\n").slice(0, 2).join(" / "));
    await b.shot(`${shots}/01-signoff-spec-1280.png`);

    // 2. Activity: 20 newest first, Load more keeps entries and scroll, exhausted hides it.
    await b.eval(`document.querySelector("#tab-activity").click()`);
    await b.waitFor(`document.querySelectorAll("#panel-activity li.entry").length === 20`);
    await b.shot(`${shots}/02-signoff-activity-1280.png`);
    const seqs = await b.eval(`[...document.querySelectorAll("#panel-activity li.entry")].map(li => Number(li.dataset.key.split(":")[0]))`);
    assert.deepEqual(seqs, [...seqs].sort((x, y) => y - x));
    const firstResult = await text(`#panel-activity li[data-attempt]`);
    assert.match(firstResult, new RegExp(ids.alt_name + " · spec v2 · current"));
    await b.eval(`(() => { const p = document.querySelector("#detail"); p.scrollTop = p.scrollHeight; })()`);
    const before = await b.eval(`document.querySelector("#detail").scrollTop`);
    await b.eval(`document.querySelector("#panel-activity .feed-more button").click()`);
    await b.waitFor(`document.querySelectorAll("#panel-activity li.entry").length > 20`);
    const after = await b.eval(`document.querySelector("#detail").scrollTop`);
    assert.equal(after, before, "scroll position kept");
    const n2 = await entries();
    ok("Activity first page 20 newest first; Load more appended", `${n2} entries, scrollTop ${before} -> ${after}, focus on ${await b.eval(`document.activeElement?.dataset?.key`)}`);
    while (await b.eval(`!!document.querySelector("#panel-activity .feed-more button")`)) {
      await b.eval(`document.querySelector("#panel-activity .feed-more button").click()`);
      await new Promise((r) => setTimeout(r, 400));
    }
    assert.ok((await text("#panel-activity")).includes("Beginning of history"));
    const superseded = await b.eval(`[...document.querySelectorAll("#panel-activity li[data-attempt]")].map(li => li.querySelector(".entry-meta")?.innerText)`);
    assert.ok(superseded.some((s) => /main · spec v1 · superseded \(now v2\)/.test(s)), superseded.join(" | "));
    ok("Load more hidden when exhausted; results show workstream and current/superseded spec", superseded.join(" | "));
    await b.shot(`${shots}/03-signoff-activity-exhausted-1280.png`);

    // 3. A result entry opens with its full proof (attempt read).
    await b.eval(`document.querySelector("#panel-activity li[data-attempt] .entry-actions button").click()`);
    await b.waitFor(`document.querySelector("#panel-activity .entry-proof .attempt")`);
    const proof = await text("#panel-activity .entry-proof");
    for (const part of ["Evidence", "Durable artifacts", "Actual verification", "Review by", "A seeded concern"]) assert.ok(proof.includes(part), part);
    assert.equal(await b.eval(`window.__log.filter(r => r.url === "attempt").length`), 1);
    ok("opening a result fetches and shows evidence, artifacts, verification, review and concerns");
    await b.eval(`document.querySelector("#panel-activity li[data-attempt]").scrollIntoView({block: "start"}); document.querySelector("#detail").scrollTop -= 110;`);
    await b.shot(`${shots}/04-result-proof-1280.png`);

    // 4. Background refresh: keeps tab and position; newer entries offered as a refresh.
    await b.eval(`document.querySelector("#detail").scrollTop = 900`);
    const pos = await b.eval(`document.querySelector("#detail").scrollTop`);
    const loaded = await entries();
    execFileSync(process.env.PYTHON || path.join(repo, ".venv/bin/python"),
      [path.join(repo, "examples/spec_activity_seed.py"), db, "--add-question", ids.signoff, "A question added between loads?"],
      { env: { ...process.env, PYTHONPATH: path.join(repo, "src") } });
    await new Promise((r) => setTimeout(r, 3200));
    await b.eval(`document.querySelector("#refresh").click()`);
    await b.waitFor(`document.querySelector("#panel-activity .feed-bar")`);
    assert.equal(await b.eval(`document.querySelector("#tab-activity").getAttribute("aria-selected")`), "true");
    assert.equal(await entries(), loaded);
    assert.equal(await b.eval(`document.querySelector("#detail").scrollTop`), pos);
    assert.ok(await b.eval(`!!document.querySelector("#panel-activity .entry-proof .attempt")`), "opened result kept");
    ok("background refresh kept tab, entries, opened result and scroll; offered newer entries", `scrollTop ${pos}`);
    await b.eval(`document.querySelector("#detail").scrollTop = 0`);
    await b.shot(`${shots}/05-newer-offered-1280.png`);
    await b.eval(`document.querySelector("#panel-activity .feed-bar button").click()`);
    await b.waitFor(`!document.querySelector("#panel-activity .feed-bar")`);
    assert.equal(await entries(), loaded + 1);
    assert.match(await text("#panel-activity li.entry"), /A question added between loads\?/);
    ok("Show newer merged the new entry on top without duplicates", `${loaded} -> ${loaded + 1}`);

    // 5. Unresolved items on Spec with Design with agent and the copy fallback.
    await b.eval(`document.querySelector("#tab-spec").click()`);
    assert.equal(await b.eval(`document.querySelector("#panel-spec #unresolved-title") !== null`), true);
    await b.eval(`navigator.clipboard.writeText = () => Promise.reject(new Error("denied"))`);
    await b.eval(`[...document.querySelectorAll("#panel-spec .unresolved button")].find(x => x.textContent === "Design with agent").click()`);
    await b.waitFor(`document.querySelector("#panel-spec .unresolved .block-head .prompt-copy")`);
    ok("Unresolved items on Spec, heading row #unresolved-title hosts Design with agent and the copy fallback");
    await b.eval(`document.querySelector("#detail").scrollTop = 0`);
    await b.shot(`${shots}/06-unresolved-on-spec-1280.png`);

    // 6. Keyboard: arrows move between tabs and select them.
    await b.eval(`document.querySelector("#tab-spec").focus()`);
    await b.key("ArrowRight", "ArrowRight", 39);
    assert.equal(await b.eval(`document.activeElement.id`), "tab-activity");
    assert.equal(await b.eval(`document.querySelector("#panel-activity").hidden`), false);
    await b.key("Home", "Home", 36);
    assert.equal(await b.eval(`document.activeElement.id + document.querySelector("#tab-spec").getAttribute("aria-selected")`), "tab-spectrue");
    await b.key("ArrowLeft", "ArrowLeft", 37);
    assert.equal(await b.eval(`document.activeElement.id`), "tab-activity");
    ok("keyboard: ArrowRight/ArrowLeft/Home move focus and selection between tabs");

    // 7. Open full result outside the loaded page: target page only, a gap above.
    await open(ids.paths.rework);
    assert.match(await text(".result-card"), /With the agents: sent back for changes/);
    await b.eval(`[...document.querySelectorAll(".result-card button")].find(x => x.textContent === "Open full result").click()`);
    await b.waitFor(`document.querySelector("#panel-activity .entry-proof .attempt")`);
    const log = await b.eval(`window.__log.map(r => r.url + ":" + (r.attempt_id || r.target || r.cursor || "first"))`);
    assert.deepEqual(log.slice(0, 2), ["activity:first", log[1]]);
    assert.ok(log[1].startsWith("activity:att_"), log.join(","));
    assert.equal(log.filter((x) => x.startsWith("activity")).length, 2, "no intervening pages: " + log.join(","));
    assert.equal(await b.eval(`document.activeElement?.dataset?.attempt?.startsWith("att_")`), true);
    assert.equal(await b.eval(`document.querySelectorAll("#panel-activity li.gap").length`), 1);
    const visible = await b.eval(`(() => { const r = document.activeElement.getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; })()`);
    assert.ok(visible, "focused result is in view");
    ok("Open full result focused an entry outside the loaded page with only the first and target pages", log.join(", "));
    await b.shot(`${shots}/07-open-full-result-target-1280.png`);
    while (await b.eval(`!!document.querySelector("#panel-activity li.gap button")`)) {
      await b.eval(`document.querySelector("#panel-activity li.gap button").click()`);
      await new Promise((r) => setTimeout(r, 400));
    }
    const keys = await b.eval(`[...document.querySelectorAll("#panel-activity li.entry")].map(li => li.dataset.key)`);
    assert.equal(new Set(keys).size, keys.length);
    ok("filling the gap closed it with every entry once", `${keys.length} entries`);

    // 8. Retry: a failing page keeps the loaded history and offers Retry.
    await b.eval(`window.__fail = body => !!body.cursor`);
    if (await b.eval(`!!document.querySelector("#panel-activity .feed-more button")`)) {
      await b.eval(`document.querySelector("#panel-activity .feed-more button").click()`);
    } else {
      await open(ids.paths.rework);
      await b.eval(`window.__fail = body => !!body.cursor; document.querySelector("#tab-activity").click()`);
      await b.waitFor(`document.querySelectorAll("#panel-activity li.entry").length === 20`);
      await b.eval(`document.querySelector("#panel-activity .feed-more button").click()`);
    }
    await b.waitFor(`document.querySelector("#panel-activity .feed-error")`);
    const kept = await entries();
    assert.match(await text("#panel-activity .feed-error"), /Couldn't load more entries: Simulated failure/);
    await b.eval(`document.querySelector("#panel-activity .feed-error").scrollIntoView({block: "center"})`);
    await b.shot(`${shots}/08-retry-1280.png`);
    await b.eval(`window.__fail = null; document.querySelector("#panel-activity .feed-error button").click()`);
    await b.waitFor(`document.querySelectorAll("#panel-activity li.entry").length > ${kept}`);
    assert.equal(await b.eval(`!!document.querySelector("#panel-activity .feed-error")`), false);
    ok("failed page kept loaded entries and Retry loaded it", `${kept} kept`);

    // 9. Empty state (simulated: every real task has a creation entry).
    await open(ids.paths.done);
    assert.match(await text(".result-card"), /Accepted at sign-off/);
    await b.eval(`window.__empty = ${JSON.stringify(ids.done)}; document.querySelector("#tab-activity").click()`);
    await b.waitFor(`document.querySelector("#panel-activity .feed-empty")`);
    assert.equal(await b.eval(`!!document.querySelector("#panel-activity .feed-more")`), false);
    ok("completed task shows the accepted result card; empty Activity shows the empty state");
    await b.shot(`${shots}/09-done-empty-1280.png`);

    // 10. Group: Spec keeps members and progress; Activity is the group's own feed.
    await open(ids.paths.group);
    assert.equal(await b.eval(`document.querySelector("#detail-tabs").getAttribute("aria-label")`), "Group details");
    assert.match(await text("#panel-spec"), /Members/i);
    assert.ok(await b.eval(`!!document.querySelector(".detail-head .progress")`));
    await b.shot(`${shots}/10-group-spec-1280.png`);
    await b.eval(`document.querySelector("#tab-activity").click()`);
    await b.waitFor(`document.querySelectorAll("#panel-activity li.entry").length === 20`);
    const groupLog = await b.eval(`window.__log.filter(r => r.url === "activity").map(r => r.task_id)`);
    assert.ok(groupLog.length && groupLog.every((id) => id === ids.group));
    const kinds = await b.eval(`[...document.querySelectorAll("#panel-activity li.entry .what")].map(n => n.textContent)`);
    ok("group opens on Spec with members/progress; Activity reads only the group's feed", [...new Set(kinds)].join(", "));
    await b.shot(`${shots}/11-group-activity-1280.png`);

    // 11. Imported task and a busy real task.
    await open(ids.paths.imported);
    assert.match(await text(".detail-head"), new RegExp(ids.imported));
    await b.eval(`document.querySelector("#tab-activity").click()`);
    await b.waitFor(`document.querySelector("#panel-activity li[data-attempt]")`);
    assert.match(await text("#panel-activity li[data-attempt]"), /Imported/);
    await b.eval(`document.querySelector("#panel-activity li[data-attempt] .entry-actions button").click()`);
    await b.waitFor(`document.querySelector("#panel-activity .entry-proof .attempt")`);
    ok("imported task: imported result entry under the import, opens with its proof");
    await b.shot(`${shots}/12-imported-activity-1280.png`);
    await open(ids.paths.busy);
    assert.match(await text(".detail-head"), new RegExp(ids.busy));
    const busyCard = await text(".result-card");
    assert.ok(busyCard.trim(), "the busy task has a current result card");
    await b.eval(`[...document.querySelectorAll(".result-card button")].find(x => x.textContent === "Open full result").click()`);
    await b.waitFor(`document.querySelector("#panel-activity .entry-proof .attempt")`);
    const busyResults = await b.eval(`document.querySelectorAll("#panel-activity li[data-attempt]").length`);
    ok("busy real task (6 results): card shows the accepted result; Open full result focuses it in Activity", `${busyResults} result entries loaded`);
    await b.shot(`${shots}/13-busy-open-full-result-1280.png`);

    // 12. Phone width.
    await b.viewport(375, 812, true);
    await open(ids.paths.signoff);
    const overflow = await b.eval(`document.documentElement.scrollWidth`);
    assert.ok(overflow <= 375, "no horizontal scroll: " + overflow);
    await b.shot(`${shots}/14-phone-spec-375.png`);
    await b.eval(`document.querySelector("#tab-activity").click()`);
    await b.waitFor(`document.querySelectorAll("#panel-activity li.entry").length >= 20`);
    await b.eval(`document.querySelector("#panel-activity li[data-attempt] .entry-actions button").click()`);
    await b.waitFor(`document.querySelector("#panel-activity .entry-proof .attempt")`);
    const overflow2 = await b.eval(`document.documentElement.scrollWidth`);
    assert.ok(overflow2 <= 375, "no horizontal scroll in Activity: " + overflow2);
    await b.eval(`document.querySelector("#detail").scrollTop = 400`);
    await b.shot(`${shots}/15-phone-activity-375.png`);
    ok("375px: no horizontal scroll on Spec or Activity (with an opened result)", `${overflow}/${overflow2}`);

    assert.deepEqual(b.consoleErrors.filter((e) => !/Simulated failure|500/.test(e)), []);
    ok("no console errors");
  } finally {
    await b.close();
  }
})().catch((e) => {
  console.error("FAIL", e);
  process.exitCode = 1;
});
