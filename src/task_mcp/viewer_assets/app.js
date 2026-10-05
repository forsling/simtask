"use strict";
// Every piece of task text is inserted with textContent or DOM nodes, never parsed as HTML.
const $ = (id) => document.getElementById(id);
// The private launch link carries the token as a bare fragment (#<token>). Location
// URLs are paths (/w/1c4684b6/t/a5dfba02) and never contain it, so drop any fragment
// at once; one that is not a token (such as an old #/project/... address) is ignored.
const launchHash = location.hash.slice(1);
const launchToken = /^[A-Za-z0-9_-]+$/.test(launchHash) ? launchHash : "";
const token = launchToken || sessionStorage.getItem("task-token") || "";
if (token) sessionStorage.setItem("task-token", token);
history.replaceState(null, "", location.pathname || "/");

// Where a task stands for the user. Cards show only this, never the agents' internal
// stage; whether work is a first attempt or a rework round belongs in the task history.
const STANDINGS = {
  signoff: { label: "Sign-off", tone: "go", badge: true },
  decision: { label: "Design", tone: "warn" },
  progress: { label: "In progress", tone: "info" },
  open: { label: "Open", tone: "ready" },
  deferred: { label: "Deferred", tone: "muted" },
  done: { label: "Done", tone: "done" },
  dropped: { label: "Dropped", tone: "muted" },
};
// Result states, shown in the task's detail and history only.
const VIEWS = {
  review: { label: "In review", tone: "info" },
  rework: { label: "Sent back", tone: "warn" },
  passed: { label: "Review passed", tone: "go" },
  human_review: { label: "Human reviewed", tone: "go" },
  done: { label: "Done", tone: "done" },
  open: { label: "Open", tone: "ready" },
  ...STANDINGS,
};
const SECTIONS = [
  { key: "signoff", title: "Signoff", standings: ["signoff"], attention: true },
  { key: "progress", title: "In progress", standings: ["progress"] },
  { key: "open", title: "Open", standings: ["open"] },
  { key: "design", title: "Design", standings: ["decision"], attention: true },
  { key: "later", title: "Later", standings: ["deferred"], collapsible: true },
  { key: "done", title: "Done", standings: ["done", "dropped"], collapsible: true },
];
const collapsed = new Set(["done"]);
const CLOSED = ["done", "dropped", "deferred"];
// A task's standing from its status, unresolved items and the current-spec
// result states in view (this workstream's, or every workstream's on project boards).
//   decision  held by any unresolved item (briefs, revised at sign-off)
//   signoff   a result passed review (or was human-reviewed) and awaits the verdict
//   progress  a result is recorded and still with the agents (in review or being fixed),
//             or an agent picked the task up recently (r.picked)
//   open      no result yet and no recent pick, ready or blocked
function standingOf(r, counts = r.attempt_counts || r.aggregate_attempt_counts || {}) {
  if (r.object_type === "group") return "group";
  const closed = [r.status, r.view].find((s) => CLOSED.includes(s));
  if (closed) return closed;
  if (r.unresolved_count || r.view === "unresolved_items") return "decision";
  if (counts.passed || counts.human_review || r.view === "signoff") return "signoff";
  // The server reports a pick only while it counts: a few hours, until a newer result.
  if (counts.review || counts.rework || r.view === "review" || r.picked) return "progress";
  return "open";
}
// The same standing for a fetched task, from its current-spec results in view.
function taskStanding(t) {
  const counts = {};
  for (const a of t.attempts || [])
    if (a.spec_revision === t.spec_revision && (!state.stream || a.workstream_id === state.stream))
      counts[a.state] = (counts[a.state] || 0) + 1;
  return standingOf({ status: t.status, unresolved_count: t.unresolved_items?.length || 0, picked: currentPick(t) }, counts);
}
// The most recent live pick of a fetched task in view (this workstream's, or any on
// project boards); the server lists only picks that still count.
function currentPick(t) {
  return (t.picks || []).find((p) => !state.stream || p.workstream_id === state.stream) || null;
}
// A quick idea saved from the browser: held by the Store's "Idea to process" item until
// an agent goes through it with the user.
const IDEA_PREFIX = "Idea to process:";
function isIdea(t) {
  return !CLOSED.includes(t.status) && (t.unresolved_items || []).some((i) => i.text.startsWith(IDEA_PREFIX));
}
// The latest rejection stays in view only while it is current: no newer result exists.
function currentRejection(t) {
  const r = t.latest_rejection;
  if (!r) return null;
  return (t.attempts || []).some((a) => a.id !== r.attempt_id && (a.created_at || "") > (r.timestamp || "")) ? null : r;
}

const state = {
  projects: [],
  streams: [],
  rows: [],
  project: null,
  stream: null,
  groups: false,
  linkedGroup: null,
  task: null,
  selected: null,
  generation: 0,
  // Board loads are cancelled only by a newer board load or a scope change, so a
  // row click (which bumps generation for the detail pane) cannot leave the list stale.
  listGeneration: 0,
  loadedAt: 0,
  // Drag-and-drop edits only this loaded workstream order, guarded by its revision.
  orderStream: null,
  orderRevision: null,
  orderSaving: false,
  // Archived workstreams stay out of navigation unless shown (remembered per tab).
  showArchived: (() => { try { return sessionStorage.getItem("task-viewer-show-archived") === "1"; } catch { return false; } })(),
  // The workstream whose board state.rows holds (null for project and group boards).
  boardStream: null,
};
let submitAction = null;
let submissionPending = false;

/* ---------- small DOM helpers ---------- */

function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function el(tag, cls, ...children) {
  const e = node(tag, undefined, cls);
  e.append(...children.filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}
function button(text, fn, cls = "btn") {
  const b = node("button", text, cls);
  b.type = "button";
  b.onclick = fn;
  return b;
}
const ICONS = {
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM21 21l-4.3-4.3",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
  close: "M18 6 6 18M6 6l12 12",
  back: "M15 18l-6-6 6-6",
  chevron: "M9 6l6 6-6 6",
  menu: "M4 6h16M4 12h16M4 18h16",
  layers: "M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  branch: "M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9",
  check: "M20 6 9 17l-5-5",
  link: "M7 17 17 7M8 7h9v9",
};
function icon(name, size = 16) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "i");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}
function iconButton(name, label, fn, cls = "icon-btn") {
  const b = button("", fn, cls);
  b.append(icon(name));
  b.setAttribute("aria-label", label);
  b.title = label;
  return b;
}
function pill(view, text) {
  const v = VIEWS[view] || { label: view, tone: "muted" };
  return node("span", text || v.label, "pill tone-" + v.tone);
}
function toast(text, error = false) {
  const t = node("div", text, "toast" + (error ? " error" : ""));
  $("toasts").append(t);
  setTimeout(() => t.classList.add("out"), error ? 6000 : 2600);
  setTimeout(() => t.remove(), error ? 6400 : 3000);
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 86400 * 14) return Math.floor(s / 86400) + "d ago";
  return new Date(iso).toLocaleDateString();
}
// "12 minutes ago" in plain words, for when an agent picked a task up.
function pickedAgo(iso) {
  const m = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.floor(m / 60);
  return `${h} hour${h === 1 ? "" : "s"} ago`;
}
// Long branch names read better with the prefix dimmed: codex/ + session-resilience.
function branchLabel(name) {
  const cut = name.lastIndexOf("/");
  const wrap = node("span", undefined, "branch");
  if (cut > 0) wrap.append(node("span", name.slice(0, cut + 1), "prefix"));
  wrap.append(node("span", name.slice(cut + 1)));
  wrap.title = name;
  return wrap;
}

const EVENTS = {
  "task.created": "Created",
  "task.idea_captured": "Idea saved",
  "task.updated": "Edited",
  "task.workstream_added": "Added to workstream",
  "task.workstream_removed": "Removed from workstream",
  "task.disposition_changed": "Status changed",
  "task.signoff": "Sign-off decision",
  "task.decomposed": "Split into a group",
  "tasks.reordered": "Reordered",
  "attempt.recorded": "Result recorded",
  "attempt.reviewed": "Reviewed",
  "attempt.human_reviewed": "Human review",
  "gate.unresolved_added": "Unresolved item added",
  "gate.unresolved_resolved": "Unresolved item resolved",
  "gate.prerequisite_added": "Prerequisite added",
  "gate.prerequisite_proposed": "Gate proposed",
  "gate.proposal_accepted": "Proposal accepted",
  "gate.proposal_dismissed": "Proposal dismissed",
  "group.member_added": "Added to group",
  "scope.changed": "Scope changed",
};
const isRead = (action) => /(\.read|\.listed|_read)$/.test(action);

/* ---------- safe Markdown-ish rendering ---------- */

function inline(text) {
  const frag = document.createDocumentFragment();
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0,
    m;
  while ((m = re.exec(text))) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    const t = m[0];
    if (m[1]) frag.append(node("code", t.slice(1, -1)));
    else if (m[2]) frag.append(node("strong", t.slice(2, -2)));
    else if (m[3]) frag.append(node("em", t.slice(1, -1)));
    else {
      // Links are shown, never followed: no remote content from task text.
      const [, label, url] = t.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      frag.append(label, node("span", " (" + url + ")", "url"));
    }
    last = m.index + t.length;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}
function markdown(text, { checklist = false } = {}) {
  const root = node("div", undefined, "md");
  text = (text || "").replace(/\r\n?/g, "\n").trim();
  if (!text) {
    root.append(node("p", "Nothing written yet.", "empty-text"));
    return root;
  }
  // A single run-on line of criteria joined by semicolons reads far better as a list.
  if (checklist && !text.includes("\n") && text.split(/;\s+/).length >= 3) {
    const ul = node("ul", undefined, "checks");
    text
      .replace(/\.$/, "")
      .split(/;\s+/)
      .forEach((part) => {
        const li = node("li");
        li.append(inline(part.charAt(0).toUpperCase() + part.slice(1)));
        ul.append(li);
      });
    root.append(ul);
    return root;
  }
  const lines = text.split("\n");
  let para = [],
    lists = [];
  const flushPara = () => {
    if (para.length) {
      const p = node("p");
      p.append(inline(para.join(" ")));
      root.append(p);
      para = [];
    }
  };
  const closeLists = () => {
    lists = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flushPara();
      closeLists();
      const body = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i]);
      root.append(el("pre", "", node("code", body.join("\n"))));
      continue;
    }
    if (!line.trim()) {
      flushPara();
      closeLists();
      continue;
    }
    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      closeLists();
      const h = node("h4", undefined, "md-h" + Math.min(heading[1].length, 3));
      h.append(inline(heading[2]));
      root.append(h);
      continue;
    }
    const item = line.match(/^(\s*)([-*+•]|\d+[.)])\s+(.*)$/);
    if (item) {
      flushPara();
      const depth = Math.floor(item[1].replace(/\t/g, "  ").length / 2);
      const ordered = /\d/.test(item[2]);
      while (lists.length > depth + 1) lists.pop();
      if (lists.length < depth + 1) {
        const list = node(ordered ? "ol" : "ul");
        const parent = lists.length ? lists[lists.length - 1].lastElementChild : null;
        (parent || root).append(list);
        lists.push(list);
      }
      const li = node("li");
      li.append(inline(item[3]));
      lists[lists.length - 1].append(li);
      continue;
    }
    if (lists.length && /^\s{2,}\S/.test(line)) {
      lists[lists.length - 1].lastElementChild.append(" ", inline(line.trim()));
      continue;
    }
    if (/^\s*>/.test(line)) {
      flushPara();
      closeLists();
      const q = node("blockquote");
      q.append(inline(line.replace(/^\s*>\s?/, "")));
      root.append(q);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushPara();
      closeLists();
      const rows = [line];
      while (i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i + 1])) rows.push(lines[++i]);
      root.append(el("pre", "", node("code", rows.join("\n"))));
      continue;
    }
    closeLists();
    para.push(line.trim());
  }
  flushPara();
  return root;
}

/* ---------- API ---------- */

async function api(action, data = {}) {
  const r = await fetch("/api/" + action, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Task-Token": token },
    body: JSON.stringify(data),
  });
  const result = await r.json();
  if (!r.ok) {
    const e = new Error(result.error || "Request failed");
    e.conflict = r.status === 409;
    throw e;
  }
  return result;
}
async function pages(action, data = {}) {
  let items = [],
    offset = 0;
  do {
    const p = await api(action, { ...data, limit: 100, offset });
    items.push(...p.items);
    offset = p.next_offset;
  } while (offset !== null);
  return items;
}
async function taskBoard(data) {
  let items = [], offset = 0, revision = null;
  do {
    const page = await api("tasks", { ...data, limit: 100, offset });
    if (revision !== null && page.workstream_order_revision !== revision)
      throw new Error("Workstream order changed while loading. Refresh to see the current order.");
    revision = page.workstream_order_revision;
    items.push(...page.items);
    offset = page.next_offset;
  } while (offset !== null);
  return { items, workstream_order_revision: revision };
}
function projectName(id) {
  return state.projects.find((p) => p.id === id)?.name || "Other project";
}
function streamName(id) {
  const s = state.streams.find((s) => s.id === id);
  return s?.branch || s?.name || "another workstream";
}
// Every workstream of a project (or all), archived ones included: names, links and
// memberships still resolve; navigation decides what to show.
function workstreams(project) {
  return pages("workstreams", { ...(project ? { project } : {}), include_archived: true });
}
function isArchived(stream) {
  return !!stream?.archive?.archived;
}
function currentArchived() {
  return isArchived(state.streams.find((s) => s.id === state.stream));
}
// The default workstream of a project: its first one that is not archived.
function defaultStream(streams) {
  return streams.find((s) => !isArchived(s))?.id || null;
}
// Tasks waiting for the user's input. Every workstream's count comes from its own
// cards through standingOf, exactly as its Signoff and Design sections would sort them: the
// open board counts its loaded rows, and each other workstream's count is read from
// its card list (no specifications) once per board refresh. The server's status
// counts rank review ahead of open questions, so they cannot stand in for it.
const INPUT = SECTIONS.filter((s) => s.attention).flatMap((s) => s.standings);
const needsInput = (rows) => rows.filter((r) => INPUT.includes(r.standing ?? standingOf(r))).length;
const needsCounts = new Map(); // workstream ID -> count from its latest card list
const needsTokens = new Map(); // workstream ID -> token of the latest read that may set it
let needsToken = 0;
function needsYou(stream) {
  if (!state.groups && state.boardStream === stream.id) return needsInput(state.rows);
  return needsCounts.get(stream.id) ?? null;
}
function noteNeeds(id, rows) {
  needsTokens.set(id, ++needsToken);
  needsCounts.set(id, needsInput(rows));
}
// Reads the given workstreams' cards in parallel and redraws the sidebar if a count
// changed. A newer read of a workstream (or its board loading) supersedes an older one.
async function refreshNeeds(project, streams) {
  const reads = streams.map((s) => {
    const token = ++needsToken;
    needsTokens.set(s.id, token);
    return pages("tasks", { project, workstream_id: s.id }).then((rows) => {
      if (needsTokens.get(s.id) !== token) return false;
      const n = needsInput(rows), changed = needsCounts.get(s.id) !== n;
      needsCounts.set(s.id, n);
      return changed;
    });
  });
  const changed = (await Promise.allSettled(reads)).some((r) => r.status === "fulfilled" && r.value);
  if (changed && project === state.project) renderNav();
}

/* ---------- location URLs ---------- */

// Short, token-free paths name the selected project, view and task or group:
//   /p/<project>      All tasks         /p/<project>/t/<task>
//   /p/<project>/g    Task groups       /p/<project>/g/<group>
//   /w/<workstream>   a workstream      /w/<workstream>/t/<task>
//   /g/<group>        a group in the view it implies (its project's groups, or shared)
//   /sg               Shared groups     /sg/<group>
// A workstream or group implies its project, so only project-wide views name one.
// Readable public task/group IDs appear in full. Legacy IDs and project/workstream
// Legacy IDs appear as their first 8 hex characters, or in full when that prefix is ambiguous
// where the address is resolved: tasks within the view's board, projects among all
// projects, workstreams and groups across the database (through "resolve-prefix").
// New public task/group IDs stay complete under /id/, including all-hex names.
const SHORT = 8;
const hexOf = (id) => String(id).replace(/^[a-z]+_/, "");
const longIds = new Set(); // IDs shown in full because their short prefix is ambiguous
const checkedIds = new Set(); // workstream and group IDs whose short prefix was checked
const groupHomes = new Map(); // group ID -> "shared", or the project whose groups it implies
function shortId(id, pool = []) {
  if (!/^[a-z]+_[0-9a-f]{1,32}$/.test(id)) return id;
  const hex = hexOf(id), short = hex.slice(0, SHORT);
  return longIds.has(id) || pool.some((o) => o !== id && /^[a-z]+_[0-9a-f]{32}$/.test(o) && hexOf(o).startsWith(short)) ? hex : short;
}
const taskRouteId = (id, pool) => /^tsk_[0-9a-f]{32}$/.test(id) ? shortId(id, pool) : "id/" + id;
function routePath() {
  if (!state.project) return "";
  const ids = (list) => list.map((x) => x.id);
  const project = "/p/" + shortId(state.project, ids(state.projects));
  if (state.groups) {
    const group = state.selected;
    if (!group) return state.groups === "shared" ? "/sg" : project + "/g";
    const id = taskRouteId(group, ids(state.rows));
    const home = groupHomes.get(group);
    if (home && home === (state.groups === "shared" ? "shared" : state.project)) return "/g/" + id;
    return (state.groups === "shared" ? "/sg/" : project + "/g/") + id;
  }
  const base = state.stream ? "/w/" + shortId(state.stream, ids(state.streams)) : project;
  return state.selected ? base + "/t/" + taskRouteId(state.selected, ids(state.rows)) : base;
}
// A parsed location ({view, project, stream, group, task} as public IDs/hex prefixes), {} for the
// default location, or null for an unrecognized address.
function parseRoute(path) {
  if (path === "/") return {};
  const [a, b, c, d, e, ...rest] = path.split("/").slice(1);
  const id = (s) => /^[0-9a-f]{1,32}$/.test(s || "");
  const publicId = (s) => typeof s === "string" && s.length <= 96 && /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(s);
  if (rest.length) return null;
  if ((a === "p" || a === "w") && id(b) && d === "id" && publicId(e)) {
    if (c === "t") return {view: a === "p" ? "all" : "workstream", [a === "p" ? "project" : "stream"]: b, task: e, publicId: true};
    if (a === "p" && c === "g") return {view: "groups", project: b, group: e, publicId: true};
  }
  if (e !== undefined) return null;
  if ((a === "g" || a === "sg") && b === "id" && publicId(c) && d === undefined) return {view: a === "g" ? "group" : "shared-groups", group: c, publicId: true};
  if (a === "p" && id(b)) {
    if (c === undefined) return { view: "all", project: b };
    if (c === "t" && id(d)) return { view: "all", project: b, task: d };
    if (c === "g" && d === undefined) return { view: "groups", project: b };
    if (c === "g" && id(d)) return { view: "groups", project: b, group: d };
  } else if (a === "w" && id(b)) {
    if (c === undefined) return { view: "workstream", stream: b };
    if (c === "t" && id(d)) return { view: "workstream", stream: b, task: d };
  } else if (a === "g" && id(b) && c === undefined) return { view: "group", group: b };
  else if (a === "sg" && c === undefined) {
    if (b === undefined) return { view: "shared-groups" };
    if (id(b)) return { view: "shared-groups", group: b };
  }
  return null;
}
// Resolve a legacy prefix or marked exact public group ID; report ambiguity explicitly.
async function resolvePrefix(kind, prefix, publicId = false) {
  const { items } = await api("resolve-prefix", { kind, prefix, ...(publicId ? {match: "public_id"} : {}) });
  if (items.length !== 1) return { ambiguous: items.length > 1 };
  const found = items[0];
  // A short prefix that matched once proves the 8-character one unique; keep a longer
  // spelling until checkPrefix confirms that the short one would do.
  if (publicId) return found;
  if (prefix.length <= SHORT) {
    checkedIds.add(found.id);
    longIds.delete(found.id);
  } else if (!checkedIds.has(found.id)) longIds.add(found.id);
  return found;
}
// Spell a written workstream or group ID in full only if its short prefix is ambiguous
// across the database. Checked once per ID, after the address is written; the
// current address is respelled in place if it still names that ID.
function checkPrefix(kind, id) {
  if (checkedIds.has(id) || !/^[a-z]+_[0-9a-f]{32}$/.test(id)) return;
  checkedIds.add(id);
  const hex = hexOf(id), short = hex.slice(0, SHORT);
  api("resolve-prefix", { kind, prefix: short }).then(({ items }) => {
    const ambiguous = items.length > 1;
    if (ambiguous === longIds.has(id)) return;
    if (ambiguous) longIds.add(id);
    else longIds.delete(id);
    const parts = (location.pathname || "/").split("/");
    // The workstream is /w/<id>...; the group ends /g/<id>, /sg/<id> or /p/<project>/g/<id>.
    const at = kind === "workstream" ? (parts[1] === "w" ? 2 : 0) : ["g", "sg"].includes(parts[1]) || parts[3] === "g" ? parts.length - 1 : 0;
    if (!at || parts[at] !== (ambiguous ? short : hex)) return;
    parts[at] = ambiguous ? hex : short;
    shownPath = parts.join("/");
    history.replaceState(null, "", shownPath);
  }, () => checkedIds.delete(id));
}
// Learn which view a group's /g/ address implies, from its details.
function learnGroupHome(t) {
  const projects = Object.keys(t.progress?.by_project || {});
  const home = projects.length > 1 ? "shared" : projects[0] || t.origin_project_id;
  if (home) groupHomes.set(t.id, home);
  return home;
}
// The location this tab last wrote or opened; back/forward to anything else opens it.
let shownPath = null;
// Record the current location: "push" for a deliberate navigation, "replace" for
// automatic selection, fallbacks and loads.
function syncRoute(mode = "replace") {
  const path = routePath();
  if (!path) return;
  if (path !== (location.pathname || "/")) history[mode === "push" ? "pushState" : "replaceState"](null, "", path);
  shownPath = path;
  if (state.groups && state.selected) checkPrefix("group", state.selected);
  else if (!state.groups && state.stream) checkPrefix("workstream", state.stream);
}
// Open a location URL (on load, back/forward or an edited address). Anything that no
// longer exists, or a prefix matching several items, falls back to the nearest valid
// view with a short notice.
async function openLocation(path, { initial = false } = {}) {
  const generation = ++state.generation;
  const stale = () => generation !== state.generation;
  const route = parseRoute(path);
  const notices = [];
  if (!route) notices.push("That address is not a viewer location.");
  let view = route?.view || null, projectId = null, stream = null, group = null, task = route?.task || null;
  if (route?.project) {
    const match = (list) => list.filter((p) => hexOf(p.id).startsWith(route.project));
    let found = match(state.projects);
    if (!found.length) {
      state.projects = await pages("projects");
      if (stale()) return;
      found = match(state.projects);
    }
    if (found.length === 1) projectId = found[0].id;
    else {
      notices.push(found.length ? "That address matches more than one project." : "That project no longer exists.");
      view = task = null;
    }
  }
  if (route?.stream) {
    const found = await resolvePrefix("workstream", route.stream);
    if (stale()) return;
    if (found.id) [stream, projectId] = [found.id, found.project_id];
    else {
      notices.push(found.ambiguous ? "That address matches more than one workstream." : "That workstream no longer exists.");
      view = "all";
    }
  }
  if (route?.group && view) {
    const found = await resolvePrefix("group", route.group, route.publicId);
    if (stale()) return;
    if (found.id) group = found.id;
    else notices.push(found.ambiguous ? "That address matches more than one task group." : "That task group no longer exists.");
  }
  let origin = null;
  if (view === "group") {
    view = "groups";
    if (group) {
      // A group implies its view: shared across projects, else its project's groups.
      const t = (await api("details", { ids: [group] })).items[0];
      if (stale()) return;
      const home = learnGroupHome(t);
      origin = t.origin_project_id;
      if (home === "shared") view = "shared-groups";
      else projectId = home;
    }
  }
  if (projectId && !state.projects.some((p) => p.id === projectId)) {
    state.projects = await pages("projects");
    if (stale()) return;
  }
  // Views that name no project keep the open one (or the group's), else the first.
  const project = [projectId, state.project, origin].map((id) => state.projects.find((p) => p.id === id)).find(Boolean) || state.projects[0];
  if (!project) return void notices.forEach((n) => toast(n));
  const streams = await workstreams(project.id);
  if (stale()) return;
  const groups = view === "groups" ? "project" : view === "shared-groups" ? "shared" : false;
  if (!view) stream = defaultStream(streams);
  if (!groups) group = null;
  closeDrawer();
  // On a narrow screen a reloaded task reopens its detail; back/forward shows the list.
  if (initial && (task || group)) $("shell").classList.add("detail-open");
  state.project = project.id;
  state.streams = streams;
  state.stream = stream;
  state.groups = groups;
  state.linkedGroup = group;
  state.selected = group;
  state.task = null;
  const name = groups === "shared" ? "Shared task groups" : groups ? `${project.name} task groups` : state.stream ? `${project.name} · ${streamName(state.stream)}` : `${project.name} · All tasks`;
  notices.forEach((n) => toast(`${n} Showing ${name}.`));
  // Keep the requested address until the board resolves its exact ID or legacy prefix.
  if (!task) syncRoute();
  renderNav();
  await reload({ requested: groups ? null : task, publicId: !!route?.publicId });
}
function onLocationChange() {
  // A launch link pasted into this tab: reload so startup adopts and strips its token.
  if (location.hash.length > 1) return location.reload();
  const path = location.pathname || "/";
  if (path === shownPath) return;
  shownPath = path;
  if (!state.projects.length || $("shell").classList.contains("stopped")) return;
  closeMenu();
  openLocation(path).catch((e) => toast(e.message, true));
}
window.addEventListener("popstate", onLocationChange);
window.addEventListener("hashchange", onLocationChange);

/* ---------- navigation ---------- */

async function boot() {
  $("menu").append(icon("menu"));
  $("refresh").append(icon("refresh"));
  $("close").append(icon("close"));
  $("search-icon").append(icon("search", 15));
  try {
    state.projects = await pages("projects");
    if (!state.projects.length) {
      renderNav();
      $("list").replaceChildren(
        emptyState("No projects yet", "Initialize a project through Task MCP to get started."),
      );
      $("detail").replaceChildren();
      return;
    }
    shownPath = location.pathname || "/";
    await openLocation(shownPath, { initial: true });
  } catch (e) {
    toast(e.message, true);
  }
}
async function chooseProject(id, { keepSelection = false } = {}) {
  const generation = ++state.generation;
  state.project = id;
  state.groups = false;
  state.linkedGroup = null;
  if (!keepSelection) {
    state.selected = null;
    state.task = null;
  }
  const streams = await workstreams(id);
  if (generation !== state.generation) return;
  state.streams = streams;
  state.stream = keepSelection ? null : defaultStream(streams);
  syncRoute("push");
  renderNav();
  await reload();
}
function renderNav() {
  const nav = $("nav");
  nav.replaceChildren(node("div", "Projects", "nav-label"));
  state.projects.forEach((p) => {
    const active = p.id === state.project && !state.groups;
    const b = button("", () => {
      closeDrawer();
      if (p.id !== state.project || state.groups) chooseProject(p.id).catch((e) => toast(e.message, true));
    }, "nav-item project" + (p.id === state.project ? " current" : ""));
    b.append(icon("folder"), node("span", p.name, "grow"));
    b.title = p.canonical_path || p.name;
    nav.append(b);
    if (p.id !== state.project) return;
    const sub = el("div", "nav-sub");
    const all = button("", () => changeScope(null), "nav-item" + (active && !state.stream ? " active" : ""));
    all.append(node("span", "All tasks", "grow"));
    sub.append(all);
    const groups = button("", () => chooseGroups("project"), "nav-item" + (state.groups === "project" ? " active" : ""));
    groups.append(icon("layers", 14), node("span", "Task groups", "grow"));
    sub.append(groups);
    // Archived workstreams are hidden unless shown; an opened one stays visible.
    const archived = state.streams.filter(isArchived);
    state.streams.forEach((s) => {
      const old = isArchived(s);
      if (old && !state.showArchived && !(active && state.stream === s.id)) return;
      const b = button("", () => changeScope(s.id), "nav-item" + (active && state.stream === s.id ? " active" : "") + (old ? " archived" : ""));
      b.append(icon("branch", 14), branchLabel(s.branch || s.name));
      const n = old ? 0 : needsYou(s);
      if (old) b.append(node("span", "archived", "tag-archived"));
      else if (n) b.append(node("span", String(n), "count attention"));
      else b.append(node("span", String(s.status?.scoped_count ?? ""), "count"));
      b.title = `${s.branch || s.name} · ${s.status?.scoped_count || 0} tasks` + (n ? ` · ${n} need you` : "") + (old ? ` · archived: ${s.archive.reason}` : "");
      sub.append(b);
    });
    if (archived.length) {
      const toggle = button(state.showArchived ? "Hide archived" : `Show ${archived.length} archived`, () => {
        state.showArchived = !state.showArchived;
        try { sessionStorage.setItem("task-viewer-show-archived", state.showArchived ? "1" : "0"); } catch {}
        renderNav();
      }, "nav-item nav-toggle");
      toggle.setAttribute("aria-pressed", String(state.showArchived));
      sub.append(toggle);
    }
    nav.append(sub);
  });
  nav.append(node("div", "Across projects", "nav-label"));
  const g = button("", () => chooseGroups("shared"), "nav-item" + (state.groups === "shared" ? " active" : ""));
  g.append(icon("layers"), node("span", "Shared task groups", "grow"));
  nav.append(g);
}
async function chooseGroups(mode) {
  closeDrawer();
  state.groups = mode;
  state.linkedGroup = null;
  state.stream = null;
  state.selected = null;
  state.task = null;
  syncRoute("push");
  renderNav();
  await reload();
}
function groupProjectCount(group) {
  return group.project_count ?? Object.keys(group.progress?.by_project || {}).length;
}
function groupKind(group) {
  const projects = groupProjectCount(group);
  return projects > 1 ? "Shared group" : projects === 1 ? "Project group" : "Empty group";
}
async function changeScope(id) {
  closeDrawer();
  state.groups = false;
  state.linkedGroup = null;
  state.stream = id;
  state.selected = null;
  state.task = null;
  syncRoute("push");
  renderNav();
  await reload();
}
async function reload({ quiet = false, requested = null, publicId = false } = {}) {
  const generation = ++state.listGeneration;
  // A row click or navigation that starts while this board loads owns the detail pane
  // (and bumps state.generation); auto-selecting here would cancel it.
  const detailGeneration = state.generation;
  const project = state.project;
  const groups = state.groups;
  const stream = state.stream;
  const stale = () => generation !== state.listGeneration || project !== state.project || groups !== state.groups || stream !== state.stream;
  if (!quiet) $("list").replaceChildren(skeleton());
  $("idea").hidden = !!groups || !project;
  $("heading").replaceChildren(
    groups === "shared" ? "Shared task groups" : groups ? "Task groups" : state.stream ? branchLabel(streamName(state.stream)) : "All tasks",
  );
  // Notes are context, not the board: a failed notes read hides them instead of the list.
  const notesLoad = groups ? Promise.resolve(null) : api("notes", { project, workstream_id: stream }).catch(() => null);
  try {
    const board = groups ? null : await taskBoard({ project, workstream_id: state.stream });
    const loaded = groups
      ? await pages("groups", groups === "project" ? { project } : {})
      : board.items;
    const rows = groups === "shared" ? loaded.filter((g) => groupProjectCount(g) > 1) : loaded;
    const streams = state.groups ? state.streams : await workstreams(project);
    const notes = (await notesLoad)?.notes || null;
    if (stale()) return;
    renderNotes(notes);
    state.rows = rows.map((r) => ({ ...r, standing: standingOf(r) }));
    state.boardStream = groups ? null : stream;
    state.streams = streams;
    // The open board's count comes from its rows; every other workstream's is read
    // again (group boards read only those not counted yet), without holding the board.
    if (state.boardStream) noteNeeds(state.boardStream, state.rows);
    const recount = streams.filter((s) => s.id !== state.boardStream && !isArchived(s) && (!groups || !needsTokens.has(s.id)));
    if (recount.length) refreshNeeds(project, recount).catch(() => {});
    state.loadedAt = Date.now();
    state.orderStream = !groups && state.stream ? state.stream : null;
    state.orderRevision = state.orderStream ? board.workstream_order_revision : null;
    $("subheading").textContent = groups
      ? `${groups === "project" ? projectName(project) : "Across projects"} · ${rows.length} group${rows.length === 1 ? "" : "s"}`
      : `${projectName(state.project)} · ${rows.length} task${rows.length === 1 ? "" : "s"}`;
    const shownStream = !groups && state.stream ? streams.find((s) => s.id === state.stream) : null;
    if (isArchived(shownStream)) {
      // A short tag beside the name keeps the subheading's actions visible; the reason
      // is its tooltip.
      const tag = node("span", "Archived", "tag-archived");
      tag.title = "Archived: " + shownStream.archive.reason;
      $("heading").append(tag);
    }
    renderNav();
    renderList();
    if (detailGeneration !== state.generation) return;
    if (requested) {
      // Exact public IDs and legacy prefixes resolve separately within this board.
      // A task that left the view (or an ambiguous prefix) falls back to the first task.
      const matches = publicId ? rows.filter((r) => r.id === requested) : rows.filter((r) => /^tsk_[0-9a-f]{32}$/.test(r.id) && hexOf(r.id).startsWith(requested));
      if (matches.length === 1) state.selected = matches[0].id;
      else {
        const where = stream ? streamName(stream) : "this project";
        toast((matches.length ? `That address matches more than one task in ${where}.` : `That task is not in ${where}.`) + (rows.length ? " Showing the first task." : ""));
      }
    }
    if (state.selected && (rows.some((r) => r.id === state.selected) || (groups && state.linkedGroup === state.selected))) await selectTask(state.selected, { quiet });
    else if (rows.length) {
      const first = orderedRows()[0] || rows[0];
      await selectTask(first.id);
    } else {
      state.selected = null;
      syncRoute();
      $("detail").classList.remove("loading");
      $("detail").replaceChildren(
        state.groups
          ? emptyState(groups === "shared" ? "No shared task groups" : "No task groups", groups === "shared" ? "Task groups with members in more than one project appear here." : "Task groups belonging to or included in this project appear here.")
          : emptyState("Nothing here yet", "Tasks appear here when an agent adds them. Use + Idea to save a quick idea, or pick another workstream."),
      );
    }
  } catch (e) {
    if (stale()) return;
    renderNotes(null);
    if (detailGeneration === state.generation) $("detail").classList.remove("loading");
    toast(e.message, true);
    $("list").replaceChildren(emptyState("Couldn't load tasks", "Refresh to try again."));
  }
}

/* ---------- notes ---------- */

// Personal project/workstream notes, shown read-only; agents keep them with set_note.
const NOTE_LABELS = { project: "Project note", workstream: "Workstream note" };
const closedNotes = new Set();
function renderNotes(notes) {
  const box = $("notes");
  const kinds = ["project", "workstream"].filter((k) => notes?.[k]?.text);
  box.replaceChildren(
    ...kinds.map((kind) => {
      const n = notes[kind];
      const d = el("details", "note");
      d.dataset.kind = kind;
      d.open = !closedNotes.has(kind);
      d.addEventListener("toggle", () => (d.open ? closedNotes.delete(kind) : closedNotes.add(kind)));
      const summary = el("summary", "note-head", icon("chevron", 12), node("span", NOTE_LABELS[kind], "note-label"),
        node("span", `${ago(n.updated_at)} · ${n.updated_by}`, "note-meta"));
      summary.title = `Updated ${new Date(n.updated_at).toLocaleString()} by ${n.updated_by} · revision ${n.revision}`;
      d.append(summary, node("div", n.text, "note-text"));
      return d;
    }),
  );
  box.hidden = !kinds.length;
}

/* ---------- list ---------- */

function filteredRows() {
  const query = $("search").value.trim().toLowerCase();
  return state.rows.filter((r) => `${r.id} ${r.title} ${r.summary || ""}`.toLowerCase().includes(query));
}
function orderedRows() {
  if (state.groups) return filteredRows();
  const rows = filteredRows();
  return SECTIONS.flatMap((s) =>
    collapsed.has(s.key) && !$("search").value ? [] : rows.filter((r) => s.standings.includes(r.standing)),
  );
}
function taskIdLabel(id) {
  const label = node("code", id, "task-id");
  label.setAttribute("aria-label", "Task ID: " + id);
  // Selecting/copying the identifier should not open the card or start a drag.
  label.onclick = (event) => event.stopPropagation();
  label.onmousedown = (event) => {
    event.stopPropagation();
    const card = label.closest?.(".row");
    if (card?.draggable) {
      card.draggable = false;
      window.addEventListener("mouseup", () => { card.draggable = true; }, { once: true });
    }
  };
  label.draggable = false;
  return label;
}
function taskIdHeader(id) {
  const label = taskIdLabel(id);
  const copy = button("Copy ID", async () => {
    try {
      await navigator.clipboard.writeText(id);
      toast("Task ID copied.");
    } catch {
      const range = document.createRange();
      range.selectNodeContents(label);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      toast("Task ID selected. Press Ctrl+C (⌘C on a Mac) to copy it.");
    }
  }, "small");
  return el("div", "task-id-header", label, copy);
}
function row(r) {
  const b = button("", () => selectTask(r.id, { open: true, entry: "push" }), "row" + (r.id === state.selected ? " selected" : ""));
  b.dataset.id = r.id;
  b.setAttribute("role", "listitem");
  b.setAttribute("aria-current", r.id === state.selected ? "true" : "false");
  if (state.groups) {
    const total = r.progress?.total || 0,
      done = r.progress?.done || 0;
    b.append(
      node("span", "", "dot tone-" + (r.complete ? "done" : "info")),
      el("span", "row-main", node("span", r.title, "row-title"), taskIdLabel(r.id), r.summary ? node("small", r.summary + (r.summary_stale ? " · Summary predates current spec" : ""), "row-summary muted") : null, node("span", `${groupKind(r)} · ${groupProjectCount(r)} project${groupProjectCount(r) === 1 ? "" : "s"}`, "muted"), progressBar(done, total)),
      node("span", `${done}/${total}`, "row-meta"),
    );
  } else {
    // Only Sign-off has a badge on cards; the orange dot identifies Design. The badge sits
    // inline after the title, so it never narrows or truncates it.
    const v = STANDINGS[r.standing] || { label: r.standing, tone: "muted" };
    b.classList.toggle("closed", r.standing === "done" || r.standing === "dropped");
    if (orderEditable()) {
      b.draggable = true;
      b.classList.add("draggable");
      // Positions are workstream-wide, so a section shows no numbering of its own.
      b.title = `${v.label} · position ${r.workstream_order_key} in ${streamName(state.stream)}. Drag to reorder.`;
    } else b.title = v.label;
    const title = node("span", r.title, "row-title");
    if (v.badge) title.append(" ", node("span", v.label, "badge tone-" + v.tone));
    const main = el("span", "row-main", title, taskIdLabel(r.id));
    if (r.summary) main.append(node("small", r.summary + (r.summary_stale ? " · Summary predates current spec" : ""), "row-summary muted"));
    b.append(node("span", "", "dot tone-" + v.tone), main);
  }
  return b;
}
function renderList() {
  const list = $("list");
  const rows = filteredRows();
  const searching = !!$("search").value.trim();
  list.replaceChildren();
  if (state.groups) {
    list.append(...rows.map(row));
  } else {
    SECTIONS.forEach((s) => {
      const items = rows.filter((r) => s.standings.includes(r.standing));
      if (!items.length) return;
      const isCollapsed = collapsed.has(s.key) && !searching;
      const head = button("", () => {
        if (!s.collapsible) return;
        collapsed.has(s.key) ? collapsed.delete(s.key) : collapsed.add(s.key);
        renderList();
      }, "section-head" + (s.collapsible ? " toggle" : "") + (isCollapsed ? " collapsed" : ""));
      if (s.collapsible) head.append(icon("chevron", 12));
      head.append(node("span", s.title), node("span", String(items.length), "count" + (s.attention ? " attention" : "")));
      head.setAttribute("aria-expanded", String(!isCollapsed));
      list.append(head);
      if (!isCollapsed) list.append(...items.map(row));
    });
    // Standings the list does not know about still deserve a place.
    const known = SECTIONS.flatMap((s) => s.standings);
    const other = rows.filter((r) => !known.includes(r.standing));
    if (other.length) list.append(node("div", "Other", "section-head"), ...other.map(row));
  }
  if (!rows.length && state.rows.length)
    list.append(emptyState("No matches", "Try a different search."));
}
function progressBar(done, total) {
  const bar = node("span", undefined, "progress");
  bar.setAttribute("role", "progressbar");
  bar.setAttribute("aria-valuemin", "0");
  bar.setAttribute("aria-valuemax", String(total));
  bar.setAttribute("aria-valuenow", String(done));
  const fill = node("span");
  fill.style.width = total ? `${(100 * done) / total}%` : "0";
  bar.append(fill);
  return bar;
}
function skeleton() {
  const wrap = node("div", undefined, "skeleton");
  for (let i = 0; i < 6; i++) wrap.append(node("div"));
  return wrap;
}
function emptyState(title, text) {
  return el("div", "empty", node("h3", title), node("p", text));
}

/* ---------- drag-and-drop ordering ---------- */

// Only a named workstream has an order to edit; project-wide and group views do not.
function orderEditable() {
  return !state.groups && !!state.stream && state.orderStream === state.stream && state.orderRevision !== null;
}
// The complete new order, or null for an invalid or unchanged drop. Rows hidden by
// search or collapsed sections stay in state.rows, so they keep their relative order.
function droppedOrder(ids, movedId, targetId, placement) {
  if (movedId === targetId || !ids.includes(movedId) || !ids.includes(targetId)) return null;
  if (placement !== "before" && placement !== "after") return null;
  const next = ids.filter((id) => id !== movedId);
  next.splice(next.indexOf(targetId) + (placement === "after" ? 1 : 0), 0, movedId);
  return next.every((id, i) => id === ids[i]) ? null : next;
}
function sectionTitle(r) {
  return SECTIONS.find((s) => s.standings.includes(r.standing))?.title || "Other";
}
const drag = { id: null, stream: null, source: null, marked: null };
function dropHint(text) {
  $("drop-hint").textContent = text || "";
  $("drop-hint").hidden = !text;
}
function unmarkDrop() {
  drag.marked?.classList.remove("drop-before", "drop-after");
  drag.marked = null;
}
function endDrag() {
  unmarkDrop();
  drag.source?.classList.remove("dragging");
  drag.id = drag.stream = drag.source = null;
  if (!state.orderSaving) dropHint("");
}
// The row under the pointer and which half of it: upper half places before, lower after.
function dropTarget(e) {
  const target = e.target?.closest?.(".row");
  if (!target?.dataset.id || target.dataset.id === drag.id) return null;
  const box = target.getBoundingClientRect();
  return { el: target, id: target.dataset.id, placement: e.clientY < box.top + box.height / 2 ? "before" : "after" };
}
function describeDrop(movedId, targetId, placement, next) {
  const moved = state.rows.find((r) => r.id === movedId);
  const target = state.rows.find((r) => r.id === targetId);
  if (!next) return `“${moved.title}” is already ${placement} “${target.title}”. Dropping here changes nothing.`;
  let text = `Place ${placement} “${target.title}” · position ${next.indexOf(movedId) + 1} of ${next.length} in ${streamName(drag.stream || state.stream)}`;
  if (sectionTitle(moved) !== sectionTitle(target))
    text += ` · it stays under ${sectionTitle(moved)}, because status sets the section`;
  return text;
}
async function commitDrop(movedId, targetId, placement) {
  if (!orderEditable() || state.orderSaving) return false;
  const next = droppedOrder(state.rows.map((r) => r.id), movedId, targetId, placement);
  if (!next) return false;
  const stream = state.stream, revision = state.orderRevision, before = state.rows;
  const byId = new Map(before.map((r) => [r.id, r]));
  const optimistic = next.map((id, i) => ({ ...byId.get(id), workstream_order_key: i + 1 }));
  state.orderSaving = true;
  state.listGeneration++; // a board load already in flight predates this save; discard it
  state.rows = optimistic;
  renderList();
  dropHint("Saving the new order…");
  let saved = false;
  try {
    // One prefix through the moved task; the store keeps every later member's order.
    const ack = await api("reorder", {
      workstream_id: stream,
      task_ids: next.slice(0, next.indexOf(movedId) + 1),
      expected_order_revision: revision,
    });
    saved = true;
    // The shown order is now the recorded one; adopt its revision so the board stays
    // consistent even if the reconciling reload below never completes.
    if (state.rows === optimistic && state.orderStream === stream && state.orderRevision === revision)
      state.orderRevision = Number.isInteger(ack?.workstream_order_revision) ? ack.workstream_order_revision : null;
    toast("Order saved");
  } catch (e) {
    // Never keep an unconfirmed order: put back the loaded rows and disable dragging
    // until a reconciling reload supplies the recorded order and its revision.
    if (state.orderStream === stream) state.orderRevision = null;
    if (state.rows === optimistic) state.rows = before;
    toast(e.conflict
      ? `${streamName(stream)} changed elsewhere, so this move was not saved. Showing the current order; drag again if you still want it.`
      : `Couldn't confirm the new order (${e.message}). Showing the saved order.`, true);
  } finally {
    state.orderSaving = false;
    dropHint("");
    if (!state.groups && state.stream === stream) renderList();
  }
  // Reconcile with recorded state either way.
  if (state.stream === stream && !state.groups) await reload({ quiet: true });
  return saved;
}
$("list").ondragstart = (e) => {
  const source = e.target?.closest?.(".row");
  if (!source || !orderEditable() || state.orderSaving || !state.rows.some((r) => r.id === source.dataset.id)) {
    e.preventDefault();
    return;
  }
  endDrag();
  drag.id = source.dataset.id;
  drag.stream = state.stream;
  drag.source = source;
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("application/x-task-mcp-task", drag.id);
  source.classList.add("dragging");
  dropHint("Drop on the upper or lower half of another task to place it before or after that task.");
};
$("list").ondragover = (e) => {
  if (!drag.id || drag.stream !== state.stream) return;
  const target = dropTarget(e);
  unmarkDrop();
  if (!target) return dropHint("Drop on another task to place it before or after that task.");
  const next = droppedOrder(state.rows.map((r) => r.id), drag.id, target.id, target.placement);
  dropHint(describeDrop(drag.id, target.id, target.placement, next));
  if (!next) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  target.el.classList.add("drop-" + target.placement);
  drag.marked = target.el;
};
$("list").ondragleave = (e) => {
  if (drag.id && !$("list").contains(e.relatedTarget)) {
    unmarkDrop();
    dropHint("Drop on another task to place it before or after that task.");
  }
};
$("list").ondrop = (e) => {
  if (!drag.id) return;
  e.preventDefault();
  const moved = drag.id, stream = drag.stream, target = dropTarget(e);
  endDrag();
  if (target && stream === state.stream) commitDrop(moved, target.id, target.placement).catch((error) => toast(error.message, true));
};
$("list").ondragend = endDrag;

/* ---------- detail ---------- */

async function includedWorkstreams(groupId) {
  const included = [];
  for (const w of await workstreams()) {
    let found = w.groups.includes(groupId);
    if (!found && w.groups_has_more) {
      let offset = 0;
      do {
        const p = await api("workstream-status", {
          workstream_id: w.id, include_scope: true, include_archived: true, limit: 100, offset,
        });
        found = p.scope.groups.ids.includes(groupId);
        offset = p.scope.groups.next_offset;
      } while (!found && offset !== null);
    }
    if (found) included.push(w);
  }
  return included;
}

async function selectTask(id, { open = false, quiet = false, entry = "replace" } = {}) {
  const generation = ++state.generation;
  state.selected = id;
  syncRoute(entry);
  document.querySelectorAll(".row").forEach((r) => {
    const on = r.dataset.id === id;
    r.classList.toggle("selected", on);
    r.setAttribute("aria-current", String(on));
    if (on) r.scrollIntoView({ block: "nearest" });
  });
  if (open) $("shell").classList.add("detail-open");
  if (!quiet) $("detail").classList.add("loading");
  try {
    const t = (await api("details", { ids: [id] })).items[0];
    if (state.selected !== id || generation !== state.generation) return;
    if (t.object_type === "group") {
      // Now that the group's projects are known, its address may shorten to /g/<group>.
      if (state.groups && !groupHomes.has(id)) {
        learnGroupHome(t);
        syncRoute();
      }
      t.included_workstreams = await includedWorkstreams(t.id);
      if (state.selected !== id || generation !== state.generation) return;
    }
    state.task = t;
    const scroll = $("detail").scrollTop;
    renderDetail(t);
    $("detail").scrollTop = quiet ? scroll : 0;
  } catch (e) {
    if (generation === state.generation) toast(e.message, true);
  } finally {
    if (generation === state.generation) $("detail").classList.remove("loading");
  }
}
function section(title, content, extra) {
  return el("section", "block", el("h3", "block-title", node("span", title), extra), content);
}
function topBar(crumbs, actions) {
  const back = iconButton("back", "Back to list", () => $("shell").classList.remove("detail-open"), "icon-btn only-mobile");
  return el("div", "topbar", back, el("div", "crumbs", ...crumbs), el("div", "top-actions", ...actions));
}
function renderDetail(t) {
  const d = $("detail");
  if (t.object_type === "group") return renderGroup(t);
  const standing = taskStanding(t);
  // A completed task cannot change, so it offers no actions.
  const actions = [];
  if (t.status !== "done") {
    const menu = button("Actions", (e) => actionsMenu(e.currentTarget, t));
    menu.setAttribute("aria-haspopup", "menu");
    menu.title = "Ask a question, change workstreams, or defer, resume or drop this task";
    actions.push(menu);
  }
  const crumbs = [node("span", projectName(t.project_id))];
  if (state.stream) crumbs.push(node("span", "/", "sep"), branchLabel(streamName(state.stream)));

  const head = el(
    "header",
    "detail-head",
    node("h2", t.title, "title"),
    taskIdHeader(t.id),
    el(
      "div",
      "meta",
      pill(standing),
      t.workstream_ids?.length
        ? el("span", "meta-item", icon("branch", 13), "In " + t.workstream_ids.map(streamName).join(", "))
        : node("span", "Inbox", "meta-item warn"),
      node("span", "Updated " + ago(t.updated_at), "meta-item"),
      t.parent_group
        ? button(t.parent_group.title, () => openGroup(t.parent_group.id), "chip")
        : null,
    ),
  );
  if (t.summary) head.append(node("p", t.summary, "muted"), node("p", t.summary_stale ? "Descriptive summary predates the current specification." : "Descriptive summary; read the specification below for requirements.", "muted"));
  d.replaceChildren(topBar(crumbs, actions), el("div", "content", head, nextStep(t, standing), ...body(t)));
}
function currentAttempt(t, requiredStates = null) {
  if (!requiredStates && t.status === "done" && t.selected_attempt_id)
    return t.attempts.find((a) => a.id === t.selected_attempt_id) || null;
  return [...t.attempts].sort((a, b) =>
    (b.created_at || "").localeCompare(a.created_at || "") || a.id.localeCompare(b.id)
  ).find((a) =>
    a.spec_revision === t.spec_revision &&
    (!state.stream || a.workstream_id === state.stream) &&
    (!requiredStates || requiredStates.includes(a.state))
  ) || null;
}
// What, if anything, the user can do next, stated plainly. The standing is where the
// task is for the user (see standingOf); prose names no attempt IDs, agent labels or
// internal workflow stages. The browser shows and steers: agents write and decide with
// the user, so sign-off hands a ready prompt to an agent instead of recording a verdict.
function nextStep(t, standing) {
  const member = t.workstream_ids?.length && !(state.stream && !t.workstream_ids.includes(state.stream));
  const blockers = (t.prerequisites || []).filter((p) => p.blocking);
  const blocking = blockers.length > 0;
  // A result that passed review stays with the user for sign-off even while a
  // prerequisite is open; the walkthrough weighs the blocker.
  const passed = standing === "signoff" ? currentAttempt(t, ["passed", "human_review"]) : null;
  let title, text, buttons = [], extra = null, signoff = false;
  if (t.status === "done") {
    title = "Signed off";
    text = "This task is complete. A new requirement becomes a new task.";
  } else if (standing === "deferred" || standing === "dropped") {
    title = standing === "deferred" ? "Deferred" : "Dropped";
    text = standing === "deferred"
      ? "Its workstreams, details and results are kept for when it resumes."
      : "Its workstreams, details and results are kept. Resume brings it back.";
    buttons.push(button("Resume", () => resumeTask(t), "btn primary"));
  } else if (isIdea(t)) {
    title = "An idea waiting to be processed";
    text = "Nothing is built from an idea as it stands. Go through it with an agent: together you turn it into a proper brief or task, split it up, or drop it.";
    extra = node("p", "Copy the prompt, paste it into your agent, and decide together.", "muted");
    const prompt = ideaPrompt(t);
    buttons.push(button("Go through it with an agent", (e) => copyPrompt(prompt, e.currentTarget, "walkthrough"), "btn primary"));
  } else if (!member) {
    title = "Add this task to a workstream";
    text = "Choose the branch where this task should be built. Its questions and prerequisites still apply.";
    buttons.push(button(membershipLabel(t), () => addToWorkstreamTask(t).catch((e) => toast(e.message, true)), "btn primary"));
  } else if (t.unresolved_items.length) {
    const n = t.unresolved_items.length;
    title = n === 1 ? "An unresolved item needs your answer" : `${n} unresolved items need your answers`;
    text = "Work on this task waits until each question below is settled. Talk it through with an agent, who records the answer.";
  } else if (passed) {
    signoff = true;
    title = "Ready for your sign-off";
    text = passed.state === "human_review" ? "You reviewed the delivered work below yourself." : "The delivered work below passed independent review.";
    if (blocking) {
      const names = blockers.map((p) => `“${p.title}”`).join(", ");
      text += blockers.length === 1
        ? ` A prerequisite is still open: ${names}. Weigh it in the walkthrough.`
        : ` ${blockers.length} prerequisites are still open: ${names}. Weigh them in the walkthrough.`;
    }
    extra = node("p", "Sign-off is a walkthrough with an agent: copy the prompt, paste it into your agent, and decide together.", "muted");
    const prompt = signoffPrompt(t);
    buttons.push(button("Sign off with an agent", (e) => copyPrompt(prompt, e.currentTarget), "btn primary"));
  } else if (blocking) {
    title = "Waiting on prerequisites";
    text = "Work on this task waits for the prerequisites below: reviewed by default, or signed off where marked.";
  } else if (standing === "signoff") {
    return null;
  } else if (standing === "progress") {
    title = "In progress";
    const pick = currentPick(t), when = pick && pickedAgo(pick.picked_at);
    if (currentAttempt(t, ["review"]))
      text = "A result is recorded and is with the agents for independent review." + (pick?.action === "review" ? ` A reviewer picked it up ${when}.` : "");
    else if (currentAttempt(t, ["rework"]))
      text = "A result was sent back for changes and the agents are fixing it." + (pick ? ` An agent picked it up ${when}.` : "");
    else text = `An agent picked this up ${when || "recently"}. No result is recorded yet.`;
  } else {
    title = "Open";
    text = "No result is recorded yet. An agent can pick this up.";
  }
  if (!signoff && !isIdea(t) && (!member || t.unresolved_items.length || blocking || ["deferred", "dropped"].includes(t.status))) {
    if (t.attempts.some((x) => x.spec_revision === t.spec_revision && (!state.stream || x.workstream_id === state.stream)))
      text += " A recorded result is kept below; it does not change this.";
  }
  const tone = (STANDINGS[standing] || STANDINGS.open).tone;
  return el(
    "div",
    "next tone-" + (t.status === "done" ? "done" : tone),
    el("div", "next-text", node("strong", title), node("p", text), extra),
    buttons.length ? el("div", "next-actions", ...buttons) : null,
  );
}
// Plain text an agent understands: the full task ID and its title.
function signoffPrompt(t) {
  return `Sign off ${t.id} — ${t.title}`;
}
function ideaPrompt(t) {
  return `Go through my ideas, starting with ${t.id} — ${t.title}`;
}
// Copy a prompt for the user to paste into an agent. The browser launches no agent.
// The Clipboard API needs a secure context (127.0.0.1 is one); when it is missing or
// refused, the prompt is shown selected beside the button so the user can copy it.
async function copyPrompt(text, anchor, purpose = "sign-off") {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(text);
    toast(`Copied. Paste it into your agent to start the ${purpose}.`);
  } catch {
    const host = anchor?.closest?.(".next");
    let box = host?.querySelector(".prompt-copy");
    if (host && !box) {
      box = node("input", undefined, "prompt-copy");
      box.readOnly = true;
      box.setAttribute("aria-label", "Prompt to copy");
      host.append(box);
    }
    if (box) {
      box.value = text;
      box.focus();
      box.select();
    }
    toast("Couldn't copy automatically. The prompt is selected: press Ctrl+C (⌘C on a Mac) to copy it.", true);
  }
}
function body(t) {
  const out = [];
  if (t.unresolved_items.length) {
    const list = el("div", "questions");
    t.unresolved_items.forEach((q) => {
      list.append(el("div", "question", markdown(q.text)));
    });
    out.push(section("Unresolved items", list));
  }
  if (t.blocked_by.length) {
    const list = el("div", "links");
    t.prerequisites.forEach((p) => {
      const b = button("", async () => {
        if (p.object_type === "group") await openGroup(p.id);
        else await navigateMember(p);
      }, "link-row");
      const project = p.object_type === "group" ? "Global group" : `${p.project_name} · ${p.project_id}`;
      b.append(
        el("span", "grow prerequisite-ref", node("span", p.title), node("small", `${p.id} · ${project} · ${p.milestone === "signoff" ? "Sign-off required" : "Review required"}`, "muted")),
        pill(p.satisfied ? "done" : "open", p.satisfied ? (p.complete ? "Satisfied · complete" : "Satisfied · reviewed") : `Blocking · ${p.state}`),
        icon("link", 14),
      );
      list.append(b);
    });
    out.push(section("Prerequisites", list));
  }
  const rejection = currentRejection(t);
  if (rejection) {
    const r = rejection;
    out.push(section("Latest rejection", el("div", "",
      node("p", `${r.source} · ${r.verdict} · ${r.timestamp}`),
      node("p", `Attempt ${r.attempt_id} · workstream ${r.workstream_id} · spec ${r.spec_revision}.`, "muted"),
      markdown(r.reasons || ""))));
  }
  out.push(section("Specification", markdown(t.body)));
  const criteria = markdown(t.acceptance_criteria, { checklist: true });
  criteria.classList.add("criteria");
  out.push(section("Acceptance criteria", criteria));
  if (t.user_request) out.push(section(`Request (${t.source || "unknown"} origin)`, markdown(t.user_request)));
  if (t.signoff_decisions?.length) {
    const history = el("details", "fold", node("summary", `Sign-off decisions (${t.signoff_decisions.length})`));
    for (const d of t.signoff_decisions) {
      history.append(el("div", "sub",
        node("h4", `${d.decision} · ${d.disposition}`),
        node("p", `Task revision ${d.task_revision}, spec ${d.spec_revision}; attempt ${d.attempt_id}, revision ${d.attempt_revision}. Decision ${d.decision_ref}.`),
        d.purpose_judgment ? node("p", `Historical purpose: ${d.purpose_judgment} (${(d.purpose_source || "unknown").replaceAll("_", " ")}).`) : null,
        d.result_judgment ? node("p", `Historical human result quality: ${d.result_judgment.replaceAll("_", " ")}.`) : null,
        markdown(d.reasons ?? d.user_note ?? ""), d.result_note ? markdown(d.result_note) : null));
    }
    out.push(history);
  }
  const standing = taskStanding(t);
  const requiredStates = standing === "signoff" ? ["passed", "human_review"] : standing === "progress" ? ["review"] : null;
  const a = (t.status !== "done" && requiredStates && currentAttempt(t, requiredStates)) || currentAttempt(t);
  const earlier = t.attempts.filter((x) => x !== a).reverse();
  if (a) out.push(section("Result", attemptCard(a, t)));
  if (earlier.length) {
    const fold = el("details", "fold", node("summary", `Other results (${earlier.length})`));
    earlier.forEach((x) => fold.append(attemptCard(x, t)));
    out.push(fold);
  }
  if (t.gate_proposals.length) {
    const list = el("div", "proposals");
    t.gate_proposals.forEach((p) =>
      list.append(el("div", "proposal", pill("open", p.state || p.status || "proposed"), markdown(p.gate_type === "prerequisite" ? `${p.detail} · ${p.milestone === "signoff" ? "Sign-off required" : "Review required"} (nonblocking proposal)` : (p.text || p.kind || "Gate proposal")))),
    );
    out.push(el("details", "fold", node("summary", `Gate proposals (${t.gate_proposals.length})`), list));
  }
  out.push(activity(t));
  return out;
}
function attemptCard(a, t) {
  const other = state.stream && a.workstream_id !== state.stream;
  const superseded = a.spec_revision !== t.spec_revision;
  const card = el(
    "article",
    "attempt",
    el(
      "div",
      "attempt-head",
      pill(a.state),
      node("span", a.implementer, "who"),
      node("span", "on " + streamName(a.workstream_id) + (other ? " (other workstream)" : ""), "muted"),
      node("span", ago(a.created_at), "muted push"),
    ),
    markdown(a.summary),
  );
  if (superseded) card.append(node("p", `Built against spec v${a.spec_revision}; the spec has changed since.`, "warn-text"));
  card.append(el("div", "sub", node("h4", "Evidence"), markdown(a.evidence)));
  if (a.artifacts) card.append(el("div", "sub", node("h4", "Durable artifacts"),
    ...a.artifacts.map((ref) => node("p", `${ref.kind}: ${ref.reference}`))));
  if (a.verification) card.append(el("div", "sub", node("h4", "Actual verification"), markdown(a.verification)));
  if (a.review_note) card.append(el("div", "sub", node("h4", "Review by " + (a.reviewer || "reviewer")), markdown(a.review_note)));
  if (a.human_review_note) card.append(el("div", "sub", node("h4", "Your review"), markdown(a.human_review_note)));
  const concerns = concernPanel(a);
  if (concerns) card.append(concerns);
  return card;
}
// Concerns on a result, with their provenance.
function concernPanel(a) {
  if (!a.concerns?.length) return null;
  const panel = el("div", "sub", node("h4", "Worth-doing and approach concerns"),
    node("p", `Result ${a.id} · ${streamName(a.workstream_id)} (${a.workstream_id}) · spec ${a.spec_revision}. Concerns do not block review or sign-off.`, "muted"));
  for (const concern of a.concerns) {
    const kind = concern.kind === "value" ? "Worth-doing concern" : "Approach concern";
    const source = concern.source === "implementer" ? "Implementer" : "Reviewer";
    panel.append(node("h4", `${kind} · ${source} ${concern.author}`), markdown(concern.text));
  }
  return panel;
}
function renderGroup(t) {
  const d = $("detail");
  const p = t.progress;
  const projects = Object.keys(p.by_project).length;
  const members = el("div", "links");
  t.member_details.forEach((m) => {
    const b = button("", () => navigateMember(m), "link-row");
    b.append(
      node("span", "", "dot tone-" + (m.status === "done" ? "done" : m.status === "open" ? "ready" : "muted")),
      node("span", m.title, "grow"),
      node("span", projectName(m.project_id), "muted"),
      icon("link", 14),
    );
    members.append(b);
  });
  if (!t.member_details.length) members.append(node("p", "No members yet. An empty group is not complete.", "empty-text"));
  const workstreams = el("div", "links");
  t.included_workstreams.forEach((w) => {
    const b = button("", () => navigateWorkstream(w), "link-row");
    b.append(
      icon("branch", 14),
      node("span", w.project_name, "muted"),
      branchLabel(w.branch || w.name),
      ...(isArchived(w) ? [node("span", "archived", "tag-archived")] : []),
      icon("link", 14),
    );
    b.title = `${w.project_name} · ${w.branch || w.name} · ${w.checkout_path}` + (isArchived(w) ? ` · archived: ${w.archive.reason}` : "");
    workstreams.append(b);
  });
  if (!t.included_workstreams.length)
    workstreams.append(node("p", "Not included as a group in any workstream. Member tasks may be scoped individually.", "empty-text"));
  d.replaceChildren(
    topBar([node("span", groupKind(t)), state.groups === "project" && !state.rows.some((r) => r.id === t.id) ? node("span", "Opened from a task link", "muted") : null], []),
    el(
      "div",
      "content",
      el(
        "header",
        "detail-head",
        node("h2", t.title, "title"),
        taskIdHeader(t.id),
        el("div", "meta", pill(t.complete ? "done" : "review", t.complete ? "Complete" : "In progress"), node("span", `${p.done} of ${p.total} signed off · ${projects} project${projects === 1 ? "" : "s"}`, "meta-item")),
        progressBar(p.done, p.total),
      ),
      t.summary ? section(t.summary_stale ? "Descriptive summary (predates current specification)" : "Descriptive summary", node("p", t.summary)) : null,
      section("Context", markdown(t.body)),
      section("Done when", markdown(t.acceptance_criteria, { checklist: true })),
      section("Included in workstreams", workstreams),
      section("Members", members),
      node("p", "Group progress counts members in every project. It doesn't mean one workstream delivered them all.", "footnote"),
      activity(t),
    ),
  );
}
async function navigateWorkstream(w) {
  closeDrawer();
  const generation = ++state.generation;
  const streams = await workstreams(w.project_id);
  if (generation !== state.generation) return;
  state.project = w.project_id;
  state.streams = streams;
  state.stream = w.id;
  state.groups = false;
  state.linkedGroup = null;
  state.selected = null;
  state.task = null;
  syncRoute("push");
  renderNav();
  await reload();
}
async function navigateMember(m) {
  state.selected = m.id;
  await chooseProject(m.project_id, { keepSelection: true });
}
async function openGroup(id) {
  const generation = ++state.generation;
  const t = (await api("details", { ids: [id] })).items[0];
  if (generation !== state.generation) return;
  learnGroupHome(t);
  const projects = Object.keys(t.progress.by_project);
  if (projects.length === 1 && projects[0] !== state.project) {
    const streams = await workstreams(projects[0]);
    if (generation !== state.generation) return;
    state.project = projects[0];
    state.streams = streams;
  }
  state.groups = projects.length > 1 ? "shared" : "project";
  state.linkedGroup = id;
  state.stream = null;
  state.selected = id;
  syncRoute("push");
  renderNav();
  await reload();
}
function activity(t) {
  const wrap = el("details", "fold", node("summary", "Activity"));
  const content = el("ol", "timeline");
  wrap.append(content);
  let loaded = false,
    after = 0,
    through = null;
  async function more() {
    try {
      const r = await api("events", {
        task_id: t.id,
        after_sequence: after,
        through_sequence: through,
        limit: 50,
        include_details: true,
      });
      through = r.through_sequence;
      let reads = 0;
      r.items.forEach((e) => {
        if (isRead(e.action) && e.outcome === "ok") return void reads++;
        const failed = e.outcome !== "ok";
        const li = el(
          "li",
          failed ? "failed" : "",
          el(
            "div",
            "event",
            node("span", EVENTS[e.action] || e.action.replace(/[._]/g, " "), "what"),
            node("span", e.actor, "muted"),
            node("time", ago(e.timestamp), "muted push"),
          ),
        );
        li.lastChild.lastChild.title = new Date(e.timestamp).toLocaleString();
        if (failed) li.append(node("div", "Failed", "warn-text"));
        const note = e.request?.reasons || e.request?.user_note || e.request?.note || e.request?.text || e.error;
        if (note) li.append(node("p", note, "event-note"));
        content.append(li);
      });
      if (reads) content.append(node("li", `${reads} read${reads === 1 ? "" : "s"} not shown`, "reads"));
      after = r.next_after_sequence;
      if (after !== null) {
        const b = button("Load more", () => {
          b.remove();
          more();
        }, "btn small");
        content.append(b);
      }
    } catch (e) {
      content.append(node("p", e.message, "warn-text"));
    }
  }
  wrap.ontoggle = () => {
    if (wrap.open && !loaded) {
      loaded = true;
      more();
    }
  };
  return wrap;
}

/* ---------- menus ---------- */

function actionsMenu(anchor, t) {
  const pop = $("popover");
  if (!pop.hidden && pop.dataset.for === t.id) return closeMenu();
  const item = (label, fn, cls = "") =>
    button(label, () => {
      closeMenu();
      fn();
    }, "menu-item " + cls);
  const items = [];
  // Questions belong to live work, including tasks sent back for rework; done and
  // dropped tasks get none, and deferred tasks need Resume first.
  if (!["done", "dropped", "deferred"].includes(t.status)) items.push(item("Ask a question", () => askQuestion(t)));
  items.push(item(membershipLabel(t), () => addToWorkstreamTask(t).catch((e) => toast(e.message, true))));
  if (t.workstream_ids?.length) items.push(item("Remove from workstream", () => removeFromWorkstreamTask(t).catch(e => toast(e.message, true))));
  if (t.status === "deferred" || t.status === "dropped") items.push(item("Resume", () => resumeTask(t)));
  else items.push(item("Defer", () => changeStatus(t, "deferred")), item("Drop", () => dropTask(t), "danger"));
  items.forEach((b) => b.setAttribute("role", "menuitem"));
  pop.replaceChildren(...items);
  pop.dataset.for = t.id;
  pop.hidden = false;
  const r = anchor.getBoundingClientRect();
  pop.style.top = r.bottom + 6 + "px";
  pop.style.left = Math.max(8, r.right - pop.offsetWidth) + "px";
  items[0].focus();
}
function closeMenu() {
  $("popover").hidden = true;
  $("popover").dataset.for = "";
}
document.addEventListener("click", (e) => {
  if (!$("popover").hidden && !$("popover").contains(e.target) && !e.target.closest(".top-actions")) closeMenu();
});
function closeDrawer() {
  $("shell").classList.remove("drawer-open", "detail-open");
}

/* ---------- dialogs ---------- */

// A labelled short input, or a multiline details area.
function field(name, label, { required = true, maxLength = 500, multiline = false } = {}) {
  const wrap = el("div", "field");
  const l = node("label", label);
  l.htmlFor = "field-" + name;
  const i = node(multiline ? "textarea" : "input");
  if (multiline) i.rows = 4;
  i.id = "field-" + name;
  i.name = name;
  i.required = required;
  i.maxLength = maxLength;
  i.autocomplete = "off";
  wrap.append(l, i);
  $("fields").append(wrap);
  return i;
}
function openDialog(title, description, saveLabel, danger = false) {
  if (submissionPending) return;
  closeMenu();
  $("submit").disabled = $("cancel").disabled = $("close").disabled = false;
  $("dialog-title").textContent = title;
  $("dialog-description").textContent = description;
  $("dialog-description").hidden = !description;
  $("fields").replaceChildren();
  $("form-error").textContent = "";
  $("conflict").replaceChildren();
  $("submit").textContent = saveLabel;
  $("submit").className = "btn " + (danger ? "danger" : "primary");
  $("dialog").showModal();
  setTimeout(() => $("fields").querySelector("input,select")?.focus(), 0);
}
function confirmation(text) {
  const l = node("label", undefined, "check");
  const i = node("input");
  i.type = "checkbox";
  i.required = true;
  l.append(i, node("span", text));
  $("fields").append(l);
}
// Submit a change to one task with the revision this dialog read. If the task changed
// meanwhile, the Store refuses it; the dialog keeps the user's input and shows the
// current task before they choose to submit against it.
function taskAction(t, action, extra) {
  let revision = t.revision;
  submitAction = async values => {
    try {
      return await api(action, { task_id: t.id, expected_revision: revision, ...extra(values) });
    } catch (error) {
      if (error.conflict) $("conflict").replaceChildren(button("Show the current task", async () => {
        const latest = (await api("details", { ids: [t.id] })).items[0];
        $("conflict").replaceChildren(el("div", "conflict-box", node("strong", latest.title),
          node("p", "Status: " + (STANDINGS[taskStanding(latest)]?.label || latest.status)),
          markdown(latest.body), markdown(latest.acceptance_criteria),
          node("p", latest.workstream_ids?.length ? "Currently in " + latest.workstream_ids.map(streamName).join(", ") : "Currently in the inbox"),
          button("I've checked it — use this version", () => {
            revision = latest.revision;
            $("form-error").textContent = "Check your choice, then submit again.";
            $("conflict").replaceChildren();
          }, "btn small")));
      }, "btn small"));
      throw error;
    }
  };
}
// Names the open workstream only when the task is not in it yet.
function membershipLabel(t) {
  const target = state.streams.find((w) => w.id === state.stream && w.project_id === t.project_id);
  return target && !t.workstream_ids?.includes(target.id) ? `Add to ${target.branch || target.name}` : "Add to workstream";
}
async function membershipDialog(t, adding) {
  if (submissionPending) return;
  // Archived workstreams take no new tasks here; their memberships can still be removed.
  const streams = (await workstreams(t.project_id))
    .filter(w => adding ? !isArchived(w) || t.workstream_ids?.includes(w.id) : t.workstream_ids?.includes(w.id));
  if (submissionPending) return;
  const title = adding ? "Add to workstream" : "Remove from workstream";
  openDialog(title, adding
    ? `Add “${t.title}” to the chosen workstream. Existing memberships, results and reviews are kept.`
    : `Remove “${t.title}” only from the chosen workstream. Other memberships, results and reviews are kept. The inbox contains tasks with no memberships.`, title);
  const picker = node("select"); picker.id = "field-workstream_id"; picker.name = "workstream_id"; picker.required = true;
  for (const w of streams) {
    const option = node("option", (w.branch || w.name) + (adding && t.workstream_ids?.includes(w.id) ? " (already included)" : "") + (isArchived(w) ? " (archived)" : ""));
    option.value = w.id; picker.append(option);
  }
  picker.value = streams.some(w => w.id === state.stream) ? state.stream : streams[0]?.id || "";
  const label = node("label", "Workstream"); label.htmlFor = picker.id;
  $("fields").append(el("div", "field", label, picker));
  $("submit").disabled = !streams.length;
  if (!streams.length) $("fields").append(node("p", adding ? "Initialize a branch workstream before adding this task." : "This task has no workstream memberships.", "warn-text"));
  taskAction(t, adding ? "add-to-workstream" : "remove-from-workstream", values => ({ workstream_id: values.get("workstream_id") }));
}
async function addToWorkstreamTask(t) { return membershipDialog(t, true); }
async function removeFromWorkstreamTask(t) { return membershipDialog(t, false); }
function askQuestion(t) {
  openDialog("Ask a question", "Your question holds this task until it's settled. An agent talks it through with you and records the answer.", "Add question");
  field("text", "Question");
  taskAction(t, "question", values => ({ text: values.get("text") }));
}
// The Store keeps a reason with every status change; these stand in when the user
// gives none.
const STATUS_NOTES = {
  deferred: "Deferred in the browser.",
  open: "Resumed in the browser.",
  dropped: "Dropped in the browser.",
};
// Defer and resume (from Later) take effect at once: nothing is lost and either can be
// undone from the same menu.
async function changeStatus(t, value) {
  if (submissionPending) return;
  submissionPending = true;
  try {
    await api("disposition", { task_id: t.id, expected_revision: t.revision, disposition: value, note: STATUS_NOTES[value] });
    toast(value === "deferred" ? "Deferred. It waits under Later until you resume it." : "Resumed.");
  } catch (e) {
    toast(e.conflict ? "This task changed elsewhere, so nothing was saved. Showing its latest version; try again if you still want to." : e.message, true);
  } finally {
    submissionPending = false;
  }
  await reload({ quiet: true });
}
function dropTask(t) {
  openDialog("Drop this task?", `Are you sure you want to drop “${t.title}”? It moves to Done as dropped. Its workstreams, details and results are kept, and Resume can bring it back.`, "Drop task", true);
  field("note", "Reason (optional)", { required: false, maxLength: 200 });
  taskAction(t, "disposition", values => ({ disposition: "dropped", note: values.get("note")?.trim() || STATUS_NOTES.dropped }));
}
// Bringing back a dropped task needs a reason, which the Store keeps as the instruction
// that revived it.
function resumeTask(t) {
  if (t.status !== "dropped") return changeStatus(t, "open");
  openDialog("Bring back this dropped task?", `“${t.title}” returns with its workstreams, details and results. Say why it's coming back; this is kept in its history.`, "Resume");
  field("note", "Why bring it back?", { maxLength: 200 });
  taskAction(t, "disposition", values => {
    const reason = (values.get("note") || "").trim();
    if (!reason) throw Object.assign(new Error("Say why it's coming back."), { local: true });
    return { disposition: "open", note: reason, authorization: reason };
  });
}
// A quick idea goes to the open project's inbox, never a workstream, held by an "Idea
// to process" item so nothing is built from it until an agent goes through it with the
// user. Saving shows it in All tasks, under Design.
function captureIdea() {
  const project = state.project;
  if (!project || state.groups) return;
  const name = projectName(project);
  openDialog("Save an idea",
    `Jot it down before it's lost. It goes to the inbox of ${name}${state.stream ? ", not this workstream" : ""}, and waits under Design. Nothing is built from it until you go through it with an agent.`,
    "Save idea");
  field("text", "Idea title (required)", { maxLength: 200 });
  field("note", "Details (optional)", { required: false, maxLength: 500, multiline: true });
  submitAction = async (values) => {
    const text = values.get("text") || "", note = values.get("note") || "";
    if (!text.trim()) throw Object.assign(new Error("Write a short idea title (up to 200 characters)."), { local: true });
    const saved = await api("idea", { project, text, note });
    return { afterSave: () => showIdea(saved) };
  };
}
async function showIdea(saved) {
  toast("Idea saved to the inbox. It waits under Design.");
  if (state.project !== saved.project_id) return reload({ quiet: true });
  state.groups = false;
  state.linkedGroup = null;
  state.stream = null;
  state.selected = saved.id;
  state.task = null;
  $("search").value = "";
  syncRoute("push");
  renderNav();
  $("shell").classList.add("detail-open");
  await reload({ quiet: true });
}
$("form").onsubmit = async (e) => {
  e.preventDefault();
  if (submissionPending) return;
  submissionPending = true;
  $("submit").disabled = $("cancel").disabled = $("close").disabled = true;
  $("form-error").textContent = "";
  try {
    const result = await submitAction(new FormData($("form")));
    $("dialog").close();
    if (result?.afterSave) await result.afterSave();
    else if (!result?.stopped) {
      toast("Saved");
      await reload({ quiet: true });
    }
  } catch (e) {
    $("form-error").textContent =
      e.message +
      (e.local ? "" : e.conflict
        ? " This task changed elsewhere. What you entered is kept; check the current task before trying again."
        : " If you're unsure whether it saved, check the task before retrying.");
  } finally {
    submissionPending = false;
    $("submit").disabled = $("cancel").disabled = $("close").disabled = false;
  }
};
$("cancel").onclick = $("close").onclick = () => {
  if (!submissionPending) $("dialog").close();
};
$("dialog").oncancel = (e) => {
  // Escape must not abandon a write whose eventual outcome still owns this dialog.
  if (submissionPending) e.preventDefault();
};

/* ---------- global controls ---------- */

$("search").oninput = renderList;
$("idea").onclick = captureIdea;
$("refresh").onclick = () => reload({ quiet: true }).then(() => toast("Up to date"));
$("menu").onclick = () => $("shell").classList.add("drawer-open");
$("scrim").onclick = closeDrawer;
$("stop").onclick = () => {
  openDialog(
    "Stop the viewer?",
    "Closes this local page. Task MCP keeps working; relaunch with task-mcp ui.",
    "Stop viewer",
    true,
  );
  confirmation("Stop the local viewer.");
  submitAction = async () => {
    const result = await api("stop");
    $("dialog").close();
    $("shell").classList.add("stopped");
    $("detail").replaceChildren(emptyState("Viewer stopped", "You can close this tab."));
    $("list").replaceChildren();
    submitAction = null;
    return result;
  };
};
function moveSelection(step) {
  const rows = orderedRows();
  if (!rows.length) return;
  const i = rows.findIndex((r) => r.id === state.selected);
  const next = rows[Math.min(rows.length - 1, Math.max(0, i + step))];
  if (next && next.id !== state.selected) selectTask(next.id);
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("popover").hidden) return closeMenu();
  if ($("dialog").open || e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if (typing) {
    if (e.key === "Escape" && e.target.id === "search") {
      e.target.value = "";
      renderList();
      e.target.blur();
    } else if (e.key === "ArrowDown" && e.target.id === "search") {
      e.preventDefault();
      moveSelection(1);
    }
    return;
  }
  if (e.key === "/") {
    e.preventDefault();
    $("search").focus();
  } else if (e.key === "j" || e.key === "ArrowDown") {
    e.preventDefault();
    moveSelection(1);
  } else if (e.key === "k" || e.key === "ArrowUp") {
    e.preventDefault();
    moveSelection(-1);
  } else if (e.key === "r") {
    reload({ quiet: true });
  }
});
// Agents change tasks while this tab sits in the background; catch up on return.
window.addEventListener("focus", () => {
  if (state.project && !$("dialog").open && !$("shell").classList.contains("stopped") && Date.now() - state.loadedAt > 15000)
    reload({ quiet: true });
});
boot();
