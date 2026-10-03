"use strict";
// Every piece of task text is inserted with textContent or DOM nodes, never parsed as HTML.
const $ = (id) => document.getElementById(id);
const token =
  location.hash.slice(1) || sessionStorage.getItem("task-token") || "";
if (token) sessionStorage.setItem("task-token", token);
history.replaceState(null, "", "/");

const VIEWS = {
  signoff: { label: "Ready to sign off", short: "Sign off", tone: "go" },
  inbox: { label: "Inbox", short: "Queue", tone: "ask" },
  unresolved_items: { label: "Open question", short: "Question", tone: "warn" },
  review: { label: "In review", short: "Review", tone: "info" },
  ready: { label: "Ready", short: "Ready", tone: "ready" },
  prerequisites: { label: "Blocked", short: "Blocked", tone: "muted" },
  deferred: { label: "Deferred", short: "Deferred", tone: "muted" },
  done: { label: "Done", short: "Done", tone: "done" },
  dropped: { label: "Dropped", short: "Dropped", tone: "muted" },
  rework: { label: "Rework", short: "Rework", tone: "warn" },
  passed: { label: "Review passed", short: "Passed", tone: "go" },
  human_review: { label: "Human reviewed", short: "Reviewed", tone: "go" },
  rejected: { label: "Rejected", short: "Rejected", tone: "warn" },
  open: { label: "Open", short: "Open", tone: "ready" },
};
const SECTIONS = [
  { key: "you", title: "Needs you", views: ["signoff", "inbox", "unresolved_items"] },
  { key: "active", title: "In progress", views: ["review", "ready", "open", "prerequisites"] },
  { key: "later", title: "Later", views: ["deferred"], collapsible: true },
  { key: "closed", title: "Closed", views: ["done", "dropped"], collapsible: true },
];
const collapsed = new Set(["closed"]);

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
  loadedAt: 0,
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
  plus: "M12 5v14M5 12h14",
  search: "M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM21 21l-4.3-4.3",
  refresh: "M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  edit: "M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z",
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
  if (name === "more") svg.setAttribute("class", "i bold");
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
  "task.updated": "Edited",
  "task.queued": "Queued",
  "task.unqueued": "Moved to inbox",
  "task.disposition_changed": "Status changed",
  "task.signoff": "Sign-off decision",
  "task.decomposed": "Split into a group",
  "tasks.reordered": "Reordered",
  "attempt.recorded": "Result recorded",
  "attempt.reviewed": "Reviewed",
  "attempt.human_reviewed": "Human review",
  "gate.unresolved_added": "Question added",
  "gate.unresolved_resolved": "Question answered",
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
    if (revision !== null && page.project_order_revision !== revision)
      throw new Error("Project order changed while loading. Refresh to see the current order.");
    revision = page.project_order_revision;
    items.push(...page.items);
    offset = page.next_offset;
  } while (offset !== null);
  return { items, project_order_revision: revision };
}
function projectName(id) {
  return state.projects.find((p) => p.id === id)?.name || "Other project";
}
function streamName(id) {
  const s = state.streams.find((s) => s.id === id);
  return s?.branch || s?.name || "another workstream";
}
function needsYou(stream) {
  const c = stream.status?.counts || {};
  return (c.signoff || 0) + (c.inbox || 0) + (c.unresolved_items || 0);
}

/* ---------- navigation ---------- */

async function boot() {
  $("menu").append(icon("menu"));
  $("refresh").append(icon("refresh"));
  $("close").append(icon("close"));
  $("search-icon").append(icon("search", 15));
  $("new").prepend(icon("plus", 15));
  try {
    state.projects = await pages("projects");
    if (!state.projects.length) {
      renderNav();
      $("new").disabled = true;
      $("list").replaceChildren(
        emptyState("No projects yet", "Initialize a project through Task MCP to get started."),
      );
      $("detail").replaceChildren();
      return;
    }
    await chooseProject(state.projects[0].id);
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
  const streams = await pages("workstreams", { project: id });
  if (generation !== state.generation) return;
  state.streams = streams;
  state.stream = keepSelection ? null : streams[0]?.id || null;
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
    state.streams.forEach((s) => {
      const b = button("", () => changeScope(s.id), "nav-item" + (active && state.stream === s.id ? " active" : ""));
      b.append(icon("branch", 14), branchLabel(s.branch || s.name));
      const n = needsYou(s);
      if (n) b.append(node("span", String(n), "count attention"));
      else b.append(node("span", String(s.status?.scoped_count ?? ""), "count"));
      b.title = `${s.branch || s.name} · ${s.status?.scoped_count || 0} tasks` + (n ? ` · ${n} need you` : "");
      sub.append(b);
    });
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
  renderNav();
  await reload();
}
async function reload({ quiet = false } = {}) {
  const generation = ++state.generation;
  const project = state.project;
  const groups = state.groups;
  if (!quiet) $("list").replaceChildren(skeleton());
  $("heading").replaceChildren(
    groups === "shared" ? "Shared task groups" : groups ? "Task groups" : state.stream ? branchLabel(streamName(state.stream)) : "All tasks",
  );
  $("new").hidden = state.groups;
  try {
    const board = groups ? null : await taskBoard({ project, workstream_id: state.stream });
    const loaded = groups
      ? await pages("groups", groups === "project" ? { project } : {})
      : board.items;
    const rows = groups === "shared" ? loaded.filter((g) => groupProjectCount(g) > 1) : loaded;
    const streams = state.groups ? state.streams : await pages("workstreams", { project });
    if (generation !== state.generation) return;
    state.rows = rows.map(r => ({ ...r, view: r.view || (r.object_type === "group" ? "group" : ["done", "deferred", "dropped"].includes(r.status) ? r.status : r.gate_diagnostics?.[0] || "open") }));
    state.streams = streams;
    state.loadedAt = Date.now();
    $("subheading").textContent = groups
      ? `${groups === "project" ? projectName(project) : "Across projects"} · ${rows.length} group${rows.length === 1 ? "" : "s"}`
      : `${projectName(state.project)} · ${rows.length} task${rows.length === 1 ? "" : "s"}`;
    if (!groups && state.stream) $("subheading").append(" · ", button("Next agent action", async () => {
      try {
        const selected = await api("next-action", {workstream_id: state.stream});
        if (selected.action) {
          await selectTask(selected.task.id);
          toast(selected.action === "review" ? "Next: fresh independent reviewer. Verify the actual checkout and saved proof." : "Next: implementer. Use the current checkout and relevant existing work.");
        } else toast(`No autonomous action. ${selected.diagnostics.signoff || 0} task(s) await human sign-off; other gates remain on the board.`);
      } catch (e) { toast(e.message, true); }
    }, "btn small"));
    renderNav();
    renderList();
    if (state.selected && (rows.some((r) => r.id === state.selected) || (groups && state.linkedGroup === state.selected))) await selectTask(state.selected, { quiet });
    else if (rows.length) {
      const first = orderedRows()[0] || rows[0];
      await selectTask(first.id);
    } else {
      state.selected = null;
      $("detail").classList.remove("loading");
      $("detail").replaceChildren(
        state.groups
          ? emptyState(groups === "shared" ? "No shared task groups" : "No task groups", groups === "shared" ? "Task groups with members in more than one project appear here." : "Task groups belonging to or included in this project appear here.")
          : emptyState("Nothing here yet", "Create a task, or pick another workstream.", button("New task", createTask, "btn primary")),
      );
    }
  } catch (e) {
    if (generation !== state.generation) return;
    $("detail").classList.remove("loading");
    toast(e.message, true);
    $("list").replaceChildren(emptyState("Couldn't load tasks", "Refresh to try again."));
  }
}

/* ---------- list ---------- */

function filteredRows() {
  const query = $("search").value.trim().toLowerCase();
  return state.rows.filter((r) => `${r.title} ${r.summary || ""}`.toLowerCase().includes(query));
}
function orderedRows() {
  if (state.groups) return filteredRows();
  const rows = filteredRows();
  return SECTIONS.flatMap((s) =>
    collapsed.has(s.key) && !$("search").value ? [] : rows.filter((r) => s.views.includes(r.view)),
  );
}
function row(r) {
  const b = button("", () => selectTask(r.id, { open: true }), "row" + (r.id === state.selected ? " selected" : ""));
  b.dataset.id = r.id;
  b.setAttribute("role", "listitem");
  b.setAttribute("aria-current", r.id === state.selected ? "true" : "false");
  if (state.groups) {
    const total = r.progress?.total || 0,
      done = r.progress?.done || 0;
    b.append(
      node("span", "", "dot tone-" + (r.complete ? "done" : "info")),
      el("span", "row-main", node("span", r.title, "row-title"), node("span", `${groupKind(r)} · ${groupProjectCount(r)} project${groupProjectCount(r) === 1 ? "" : "s"}`, "muted"), progressBar(done, total)),
      node("span", `${done}/${total}`, "row-meta"),
    );
  } else {
    const v = VIEWS[r.view] || { short: r.view, tone: "muted" };
    b.classList.toggle("closed", r.view === "done" || r.view === "dropped");
    b.append(
      node("span", "", "dot tone-" + v.tone),
      node("span", r.title, "row-title"),
      node("span", v.short, "row-meta tone-" + v.tone),
    );
  }
  if (r.summary) {
    b.querySelector(".row-title").append(node("small", r.summary + (r.summary_stale ? " · Summary predates current spec" : ""), "row-summary muted"));
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
      const items = rows.filter((r) => s.views.includes(r.view));
      if (!items.length) return;
      const isCollapsed = collapsed.has(s.key) && !searching;
      const head = button("", () => {
        if (!s.collapsible) return;
        collapsed.has(s.key) ? collapsed.delete(s.key) : collapsed.add(s.key);
        renderList();
      }, "section-head" + (s.collapsible ? " toggle" : "") + (isCollapsed ? " collapsed" : ""));
      if (s.collapsible) head.append(icon("chevron", 12));
      head.append(node("span", s.title), node("span", String(items.length), "count" + (s.key === "you" ? " attention" : "")));
      head.setAttribute("aria-expanded", String(!isCollapsed));
      list.append(head);
      if (!isCollapsed) list.append(...items.map(row));
    });
    // Views the list does not know about still deserve a place.
    const known = SECTIONS.flatMap((s) => s.views);
    const other = rows.filter((r) => !known.includes(r.view));
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
function emptyState(title, text, action) {
  return el("div", "empty", node("h3", title), node("p", text), action);
}

/* ---------- detail ---------- */

async function includedWorkstreams(groupId) {
  const included = [];
  for (const w of await pages("workstreams")) {
    let found = w.groups.includes(groupId);
    if (!found && w.groups_has_more) {
      let offset = 0;
      do {
        const p = await api("workstream-status", {
          workstream_id: w.id, include_scope: true, limit: 100, offset,
        });
        found = p.scope.groups.ids.includes(groupId);
        offset = p.scope.groups.next_offset;
      } while (!found && offset !== null);
    }
    if (found) included.push(w);
  }
  return included;
}

async function selectTask(id, { open = false, quiet = false } = {}) {
  const generation = ++state.generation;
  state.selected = id;
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
  const row = state.rows.find((r) => r.id === t.id);
  const view = row?.view || t.status;
  const locked = t.status === "done";

  const actions = [button("Move in project order", () => moveTask(t).catch((e) => toast(e.message, true))), button("Edit summary", () => editSummary(t))];
  if (!locked) {
    actions.push(iconButton("edit", "Edit (e)", () => editTask(t)));
    actions.push(iconButton("more", "More actions", (e) => moreMenu(e.currentTarget, t)));
  }
  const crumbs = [node("span", projectName(t.project_id))];
  if (state.stream) crumbs.push(node("span", "/", "sep"), branchLabel(streamName(state.stream)));

  const head = el(
    "header",
    "detail-head",
    node("h2", t.title, "title"),
    el(
      "div",
      "meta",
      pill(view),
      t.queue_workstream_id
        ? el("span", "meta-item", icon("branch", 13), "Queued for " + streamName(t.queue_workstream_id))
        : node("span", "Inbox", "meta-item warn"),
      node("span", "Updated " + ago(t.updated_at), "meta-item"),
      t.parent_group
        ? button(t.parent_group.title, () => openGroup(t.parent_group.id), "chip")
        : null,
    ),
  );
  if (t.summary) head.append(node("p", t.summary, "muted"), node("p", t.summary_stale ? "Descriptive summary predates the current specification." : "Descriptive summary; read the specification below for requirements.", "muted"));
  d.replaceChildren(topBar(crumbs, actions), el("div", "content", head, nextStep(t, view), ...body(t)));
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
// The one thing the human can do next, stated plainly with its buttons.
function nextStep(t, view) {
  const a = currentAttempt(t, view === "signoff" ? ["passed", "human_review"] : ["review"]);
  let title, text, buttons = [];
  if (t.status === "done") {
    title = "Signed off";
    text = "This task is complete. A new requirement becomes a new task.";
  } else if (view === "deferred" || view === "dropped") {
    title = view === "deferred" ? "Deferred" : "Dropped";
    text = t.status === "dropped" ? "History, queue placement and proof are kept. Revival needs actual authorization." : "Queue placement, context and proof are kept for resumption.";
    buttons.push(button("Resume task", () => disposition(t, "open", "Resume"), "btn primary"));
  } else if (view === "inbox" || !t.queue_workstream_id) {
    title = "Queue this task on a branch";
    text = "Choose the branch where this task should be built. Open questions and prerequisites still apply.";
    buttons.push(button(queueLabel(t), () => queueTask(t).catch((e) => toast(e.message, true)), "btn primary"));
    buttons.push(button("Edit first", () => editTask(t)));
  } else if (t.unresolved_items.length) {
    const n = t.unresolved_items.length;
    title = n === 1 ? "One question needs an answer" : `${n} questions need answers`;
    text = "Autonomous implementation and review wait until each question is resolved.";
  } else if (t.prerequisites?.some((p) => p.blocking) || view === "prerequisites") {
    title = "Waiting on prerequisites";
    text = "Autonomous implementation and review wait for the required milestones below: review by default, or explicit sign-off.";
  } else if (view === "signoff" && a) {
    title = "Ready for your sign-off";
    text = `${a.implementer}'s result has been reviewed. Judge the task purpose and the result below; one informed decision can cover both.`;
    buttons.push(button("Approve & sign off", () => signoff(t, a), "btn primary"));
    buttons.push(button("Request changes", () => requestChanges(t, a)));
  } else if (view === "review" && a) {
    title = "Next agent action: review";
    text = `${a.implementer} recorded a durable result. Send it to a fresh independent reviewer, or review it yourself. Check the actual checkout and proof first.`;
    buttons.push(button("Record my review", () => humanReview(a)));
  } else if (view === "unresolved_items") {
    const n = t.unresolved_items.length;
    title = n === 1 ? "One question needs an answer" : `${n} questions need answers`;
    text = "Work is blocked until each is resolved.";
  } else if (view === "prerequisites") {
    title = "Waiting on prerequisites";
    text = "It becomes ready once the required prerequisite milestones below are satisfied.";
  } else if (view === "ready") {
    title = "Next agent action: implement";
    text = "Queued and unblocked. An implementer can use the current checkout and relevant existing work.";
  } else return null;
  const blocked = !t.queue_workstream_id || t.unresolved_items.length || t.prerequisites?.some((p) => p.blocking) || ["deferred", "dropped"].includes(t.status);
  if (blocked && t.attempts.some((a) => a.spec_revision === t.spec_revision && (!state.stream || a.workstream_id === state.stream))) {
    text += " A factual result is saved below; it changes no queue placement, gates or completion.";
    const pending = currentAttempt(t, ["review"]);
    if (pending) buttons.push(button("Record my review", () => humanReview(pending)));
  }
  const tone = (VIEWS[view] || VIEWS.open).tone;
  return el(
    "div",
    "next tone-" + (t.status === "done" ? "done" : tone),
    el("div", "next-text", node("strong", title), node("p", text)),
    buttons.length ? el("div", "next-actions", ...buttons) : null,
  );
}
function body(t) {
  const out = [];
  const locked = t.status === "done";
  if (t.unresolved_items.length) {
    const list = el("div", "questions");
    t.unresolved_items.forEach((q) => {
      const item = el("div", "question", markdown(q.text));
      if (!locked) item.append(el("div", "q-actions", button("Answer", () => resolve(t, q), "btn small")));
      list.append(item);
    });
    out.push(section("Open questions", list));
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
        node("p", `Purpose: ${d.purpose_judgment} (${d.purpose_source.replaceAll("_", " ")}).`),
        node("p", `Human result quality: ${d.result_judgment.replaceAll("_", " ")}. Independent review remains in the result evidence.`),
        markdown(d.user_note), d.result_note ? markdown(d.result_note) : null));
    }
    out.push(history);
  }
  const view = state.rows.find((r) => r.id === t.id)?.view;
  const requiredStates = view === "signoff" ? ["passed", "human_review"] : view === "review" ? ["review"] : null;
  const a = currentAttempt(t, t.status === "done" ? null : requiredStates);
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
  return card;
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
      icon("link", 14),
    );
    b.title = `${w.project_name} · ${w.branch || w.name} · ${w.checkout_path}`;
    workstreams.append(b);
  });
  if (!t.included_workstreams.length)
    workstreams.append(node("p", "Not included as a group in any workstream. Member tasks may be scoped individually.", "empty-text"));
  d.replaceChildren(
    topBar([node("span", groupKind(t)), state.groups === "project" && !state.rows.some((r) => r.id === t.id) ? node("span", "Opened from a task link", "muted") : null], [button("Edit summary", () => editSummary(t))]),
    el(
      "div",
      "content",
      el(
        "header",
        "detail-head",
        node("h2", t.title, "title"),
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
  const streams = await pages("workstreams", { project: w.project_id });
  if (generation !== state.generation) return;
  state.project = w.project_id;
  state.streams = streams;
  state.stream = w.id;
  state.groups = false;
  state.linkedGroup = null;
  state.selected = null;
  state.task = null;
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
  const projects = Object.keys(t.progress.by_project);
  if (projects.length === 1 && projects[0] !== state.project) {
    const streams = await pages("workstreams", { project: projects[0] });
    if (generation !== state.generation) return;
    state.project = projects[0];
    state.streams = streams;
  }
  state.groups = projects.length > 1 ? "shared" : "project";
  state.linkedGroup = id;
  state.stream = null;
  state.selected = id;
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
        const note = e.request?.user_note || e.request?.note || e.request?.text || e.error;
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

function moreMenu(anchor, t) {
  const pop = $("popover");
  if (!pop.hidden && pop.dataset.for === t.id) return closeMenu();
  const item = (label, fn, cls = "") =>
    button(label, () => {
      closeMenu();
      fn();
    }, "menu-item " + cls);
  const items = [item("Ask a question", () => askQuestion(t))];
  items.push(item(queueLabel(t), () => queueTask(t).catch((e) => toast(e.message, true))));
  if (t.queue_workstream_id) items.push(item("Move to inbox", () => moveToInbox(t)));
  if (t.status === "deferred" || t.status === "dropped") items.push(item("Resume", () => disposition(t, "open", "Resume")));
  else items.push(item("Defer", () => disposition(t, "deferred", "Defer")), item("Drop", () => disposition(t, "dropped", "Drop"), "danger"));
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

function field(name, label, value = "", kind = "textarea", required = true, hint) {
  const wrap = el("div", "field");
  const l = node("label", label);
  l.htmlFor = "field-" + name;
  const i = node(kind === "input" ? "input" : "textarea");
  i.id = "field-" + name;
  i.name = name;
  i.value = value;
  i.required = required;
  if (kind === "tall") i.rows = 9;
  else if (kind !== "input") i.rows = 4;
  wrap.append(l, i);
  if (hint) wrap.append(node("small", hint, "field-hint"));
  $("fields").append(wrap);
  return i;
}
function openDialog(title, description, saveLabel, danger = false) {
  if (submissionPending) return;
  closeMenu();
  $("dialog-title").textContent = title;
  $("dialog-description").textContent = description;
  $("dialog-description").hidden = !description;
  $("fields").replaceChildren();
  $("form-error").textContent = "";
  $("conflict").replaceChildren();
  $("submit").textContent = saveLabel;
  $("submit").className = "btn " + (danger ? "danger" : "primary");
  $("dialog").showModal();
  setTimeout(() => $("fields").querySelector("input,textarea")?.focus(), 0);
}
function confirmation(text = "This is my decision. Record it in the task history.") {
  const l = node("label", undefined, "check");
  const i = node("input");
  i.type = "checkbox";
  i.required = true;
  l.append(i, node("span", text));
  $("fields").append(l);
}
function decision({ title, description, action, data, key, label, submit, danger, before }) {
  openDialog(title, description, submit || "Confirm", danger);
  before?.();
  field(key, label || (key === "text" ? "Question" : "Note"));
  confirmation();
  const taskId = state.task.id;
  submitAction = async (values) => {
    const payload = { ...data, [key]: values.get(key) };
    if (action === "signoff") {
      payload.decision = values.get("decision") || data.decision;
      if (payload.decision === "revise") payload.specification_question = values.get("specification_question");
      if (["revise", "drop", "defer"].includes(payload.decision)) {
        payload.result_judgment = values.get("result_judgment") || "not_judged";
        if (payload.result_judgment !== "not_judged") payload.result_note = values.get("result_note");
      }
    }
    if (values.has("authorization")) payload.authorization = values.get("authorization");
    try {
      return await api(action, payload);
    } catch (e) {
      if (e.conflict) {
        $("conflict").replaceChildren(
          button("Show me what changed", async () => {
            const latest = (await api("details", { ids: [taskId] })).items[0];
            const n = el(
              "div",
              "conflict-box",
              node("strong", "Current version"),
              node("p", latest.title, "c-title"),
              markdown(latest.body),
              markdown(latest.acceptance_criteria),
            );
            latest.unresolved_items.forEach((q) => n.append(node("p", "Question: " + q.text)));
            const a = data.attempt_id ? latest.attempts.find((a) => a.id === data.attempt_id) : null;
            if (a) n.append(node("p", `${VIEWS[a.state]?.label || a.state}: ${a.summary}`), node("p", a.evidence, "muted"));
            n.append(
              button("I've reviewed it — use this version", () => {
                data.expected_revision = action === "human-review" ? a.revision : latest.revision;
                $("fields").querySelector("[type=checkbox]").checked = false;
                $("form-error").textContent = "Check your note, confirm again, then submit.";
                n.remove();
              }, "btn small"),
            );
            $("conflict").replaceChildren(n);
          }, "btn small"),
        );
      }
      throw e;
    }
  };
}
async function moveTask(t) {
  if (submissionPending) return;
  const board = await taskBoard({ project: t.project_id });
  if (submissionPending) return;
  let revision = board.project_order_revision;
  openDialog("Move in project order", `Move “${t.title}”. This shared order applies to every workstream. Each workstream keeps its own scope and eligibility. Completed task positions may shift.`, "Move task");
  const wrap = el("div", "field");
  const label = node("label", "Place this task");
  label.htmlFor = "field-position";
  const position = node("select");
  position.id = "field-position"; position.name = "position";
  for (const value of ["before", "after"]) {
    const option = node("option", value === "before" ? "Immediately before" : "Immediately after");
    option.value = value; position.append(option);
  }
  position.value = "before";
  const anchorLabel = node("label", "Project task");
  anchorLabel.htmlFor = "field-anchor_id";
  const anchor = node("select");
  anchor.id = "field-anchor_id"; anchor.name = "anchor_id"; anchor.required = true;
  const populate = (items) => {
    const selected = anchor.value;
    anchor.replaceChildren();
    for (const row of items.filter((row) => row.id !== t.id)) {
      const option = node("option", `${row.order_key}. ${row.title}${row.view === "done" ? " (done)" : ""}`);
      option.value = row.id; anchor.append(option);
    }
    if (items.some((row) => row.id === selected && row.id !== t.id)) anchor.value = selected;
  };
  populate(board.items);
  wrap.append(label, position, anchorLabel, anchor);
  $("fields").append(wrap);
  field("instruction", "Scheduling decision", "", "textarea", true, "Record the actual instruction or authority for this move.");
  confirmation("Apply this scheduling decision to the shared project order.");
  submitAction = async (values) => {
    try {
      return await api("reorder", {
        project: t.project_id, task_id: t.id, anchor_id: values.get("anchor_id"),
        position: values.get("position"), expected_order_revision: revision,
        instruction: values.get("instruction"),
      });
    } catch (error) {
      if (error.conflict) $("conflict").replaceChildren(button("Show the current project order", async () => {
        const latest = await taskBoard({ project: t.project_id });
        const list = el("ol", "");
        for (const row of latest.items) list.append(node("li", row.title));
        $("conflict").replaceChildren(el("div", "conflict-box", list, button("I've reviewed it — use this order", () => {
          revision = latest.project_order_revision;
          populate(latest.items);
          $("fields").querySelector("[type=checkbox]").checked = false;
          $("form-error").textContent = "Check the anchor and scheduling note, confirm again, then submit.";
          $("conflict").replaceChildren();
        }, "btn small")));
      }, "btn small"));
      throw error;
    }
  };
}
function queueLabel(t) {
  const target = state.streams.find((w) => w.id === state.stream && w.project_id === t.project_id);
  return target ? `Queue for ${target.branch || target.name}` : "Queue for branch";
}
async function queueTask(t) {
  if (submissionPending) return;
  const preferred = state.stream;
  const streams = await pages("workstreams", { project: t.project_id });
  if (submissionPending) return;
  openDialog("Queue for branch", `Choose where “${t.title}” should be built. Queueing moves it from its previous branch.`, "Queue task");
  const picker = node("select"); picker.id = "field-workstream_id"; picker.name = "workstream_id"; picker.required = true;
  for (const w of streams) {
    const option = node("option", w.branch || w.name); option.value = w.id; picker.append(option);
  }
  picker.value = streams.some(w => w.id === preferred) ? preferred :
    (streams.some(w => w.id === t.queue_workstream_id) ? t.queue_workstream_id : streams[0]?.id || "");
  const label = node("label", "Branch"); label.htmlFor = picker.id;
  $("fields").append(el("div", "field", label, picker));
  $("submit").disabled = !streams.length;
  if (!streams.length) $("fields").append(node("p", "Initialize a branch workstream before queueing this task.", "warn-text"));
  placementAction(t, "queue", values => ({ workstream_id: values.get("workstream_id") }));
}
function moveToInbox(t) {
  if (submissionPending) return;
  openDialog("Move to inbox", `Remove “${t.title}” from its branch queue. Requirements and all results and reviews are kept.`, "Move to inbox");
  placementAction(t, "unqueue", () => ({}));
}
function placementAction(t, action, extra) {
  let revision = t.revision;
  submitAction = async values => {
    try {
      return await api(action, { task_id: t.id, expected_revision: revision, ...extra(values) });
    } catch (error) {
      if (error.conflict) $("conflict").replaceChildren(button("Show the current task", async () => {
        const latest = (await api("details", { ids: [t.id] })).items[0];
        $("conflict").replaceChildren(el("div", "conflict-box", node("strong", latest.title),
          markdown(latest.body), markdown(latest.acceptance_criteria),
          node("p", latest.queue_workstream_id ? "Currently queued for " + streamName(latest.queue_workstream_id) : "Currently in the inbox"),
          button("I've reviewed it — use this version", () => {
            revision = latest.revision;
            $("form-error").textContent = "Check the chosen placement, then submit again.";
            $("conflict").replaceChildren();
          }, "btn small")));
      }, "btn small"));
      throw error;
    }
  };
}
function askQuestion(t) {
  decision({
    title: "Ask a question",
    description: "Open questions block implementation until they're answered.",
    action: "question",
    data: { task_id: t.id, expected_revision: t.revision },
    key: "text",
    label: "Question",
    submit: "Add question",
  });
}
function resolve(t, q) {
  decision({
    title: "Answer question",
    description: q.text,
    action: "resolve",
    data: { task_id: t.id, expected_revision: t.revision, item_id: q.id },
    key: "user_note",
    label: "Your answer",
    submit: "Resolve",
  });
}
function disposition(t, value, label) {
  decision({
    title: `${label} this task?`,
    description: value === "dropped" ? "Dropping preserves queue placement, history and proof. Revival will need actual authorization." : "Queue placement, context and proof are kept.",
    action: "disposition",
    data: { task_id: t.id, expected_revision: t.revision, disposition: value },
    key: "note",
    label: "Reason",
    submit: label,
    danger: value === "dropped",
    before: () => {
      if (t.status === "dropped" && value !== "dropped") field("authorization", "Actual instruction authorizing revival");
    },
  });
}
function humanReview(a) {
  decision({
    title: "Record your review",
    description: "Confirm you reviewed this result and its evidence yourself, or that more independent review isn't needed. This doesn't sign off the task.",
    action: "human-review",
    data: { attempt_id: a.id, expected_revision: a.revision },
    key: "user_note",
    label: "Review note",
    submit: "Record review",
  });
}
function signoff(t, a, initial = "approve") {
  decision({
    title: "Judge purpose and result",
    description: `“${t.title}” · spec ${t.spec_revision}. Result ${a.id} · revision ${a.revision}. Approve completes the task and cannot be undone.`,
    action: "signoff",
    data: { task_id: t.id, expected_revision: t.revision, attempt_id: a.id,
      expected_attempt_revision: a.revision, decision: initial },
    key: "user_note",
    label: "Your decision and reason",
    submit: "Record decision",
    before: () => {
      $("fields").append(section("Reviewed result", el("div", "", markdown(a.summary || ""),
          node("p", a.state === "human_review" ? "Actual human review recorded." : `Independent review by ${a.reviewer || "reviewer"}.`),
          markdown(a.review_note || a.human_review_note || ""))));
      const choice = node("select");
      choice.id = "field-decision"; choice.name = "decision";
      for (const [value, label] of [
        ["approve", "Approve purpose and result; complete"],
        ["rework", "Purpose approved; repair implementation and review again"],
        ["revise", "Revise requirements; ask a blocking question"],
        ["drop", "Drop task; keep queue placement and proof"],
        ["defer", "Defer task; retain queue placement and proof"],
      ]) {const option = node("option", label); option.value = value; choice.append(option);}
      choice.value = initial;
      $("fields").append(el("div", "field", node("label", "Decision"), choice));
      const question = field("specification_question", "Concrete specification question", "", "textarea", false);
      const quality = node("select"); quality.id = "field-result_judgment"; quality.name = "result_judgment";
      for (const [value, label] of [["not_judged", "Human technical quality not judged"], ["accepted", "User separately judged result sound"], ["rework", "User separately requested technical rework"]]) {
        const option = node("option", label); option.value = value; quality.append(option);
      }
      quality.value = "not_judged";
      const qualityField = el("div", "field", node("label", "Separate actual quality judgment (optional)"), quality);
      $("fields").append(qualityField);
      const qualityNote = field("result_note", "Actual separate quality judgment", "", "textarea", false);
      const sync = () => {
        const direction = ["revise", "drop", "defer"].includes(choice.value);
        question.parentElement.hidden = choice.value !== "revise";
        question.disabled = choice.value !== "revise"; question.required = choice.value === "revise";
        qualityField.hidden = !direction; quality.disabled = !direction;
        const judged = direction && quality.value !== "not_judged";
        qualityNote.parentElement.hidden = !judged; qualityNote.disabled = !judged; qualityNote.required = judged;
      };
      choice.onchange = sync; quality.onchange = sync; sync();
    },
  });
}
function requestChanges(t, a) { signoff(t, a, "rework"); }
function editSummary(t) {
  openDialog("Edit descriptive summary", "Intent or settled constraints, up to 240 characters on one line. Requirements and acceptance criteria stay in the specification.", "Save summary");
  const summary = field("summary", "Summary (optional)", t.summary || "", "input", false);
  summary.maxLength = 240;
  submitAction = async values => api("edit", {
    task_id: t.id, expected_revision: t.revision,
    changes: { summary: values.get("summary") || null },
  });
}
function editTask(t) {
  if (!t || t.object_type !== "task" || t.status === "done") return;
  let revision = t.revision;
  let etag = t.specification_etag;
  openDialog("Edit task", "Save the specification. Queue placement stays unchanged; other gates still apply.", "Save task");
  $("dialog").classList.add("wide");
  field("title", "Title", t.title, "input");
  const summary = field("summary", "Summary (optional descriptive intent)", t.summary || "", "input", false);
  summary.maxLength = 240;
  field("body", "Specification", t.body, "tall", false, "Markdown is supported.");
  field("acceptance_criteria", "Acceptance criteria", t.acceptance_criteria, "textarea", false);
  submitAction = async (values) => {
    const payload = {
      task_id: t.id, expected_revision: revision, specification_etag: etag,
      changes: { ...Object.fromEntries(["title", "body", "acceptance_criteria"].map(key => [key, values.get(key)])), summary: values.get("summary") || null },
    };
    try {
      return await api("edit", payload);
    } catch (e) {
      if (e.conflict) {
        $("conflict").replaceChildren(
          button("Show the current version next to my draft", async () => {
            const latest = (await api("details", { ids: [t.id] })).items[0];
            const n = el(
              "div",
              "conflict-box",
              node("strong", "Current version"),
              node("p", latest.title, "c-title"),
              markdown(latest.body),
              markdown(latest.acceptance_criteria),
              button("I've merged my draft — save over this version", () => {
                revision = latest.revision;
                etag = latest.specification_etag;
                $("form-error").textContent = "Check your merged draft, then save again.";
                n.remove();
              }, "btn small"),
            );
            $("conflict").replaceChildren(n);
          }, "btn small"),
        );
      }
      throw e;
    }
  };
}
function createTask() {
  if (state.groups || !state.project || $("new").disabled || submissionPending) return;
  const projectId = state.project, workstreamId = state.stream;
  openDialog(
    "New task",
    state.stream
      ? `Adds a task to ${streamName(state.stream)}. It will be queued on this branch.`
      : `Adds a task to the ${projectName(state.project)} inbox.`,
    "Create task",
  );
  $("dialog").classList.add("wide");
  field("title", "Title", "", "input");
  const summary = field("summary", "Summary (optional descriptive intent)", "", "input", false);
  summary.maxLength = 240;
  field("body", "What needs to happen?", "", "tall", false, "Markdown is supported.");
  field("acceptance_criteria", "How will you know it's done?", "", "textarea", false);
  field("user_request", "Your original request", "", "textarea", false, "Preserved as descriptive context.");
  submitAction = async (values) => {
    const payload = {
      title: values.get("title"), body: values.get("body"), summary: values.get("summary") || null,
      acceptance_criteria: values.get("acceptance_criteria"),
      user_request: values.get("user_request"), source: "user",
      project: projectId,
      workstream_id: workstreamId,
    };
    const r = await api("create", payload);
    state.selected = r.id;
    return r;
  };
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
    if (!result?.stopped) {
      toast("Saved");
      await reload({ quiet: true });
    }
  } catch (e) {
    $("form-error").textContent =
      e.message +
      (e.conflict
        ? " Recorded state changed. Your draft is kept; review the current version before retrying."
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
$("dialog").onclose = () => $("dialog").classList.remove("wide");

/* ---------- global controls ---------- */

$("search").oninput = renderList;
$("new").onclick = createTask;
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
    $("new").disabled = true;
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
  } else if (e.key === "n") {
    e.preventDefault();
    createTask();
  } else if (e.key === "e" && state.task) {
    e.preventDefault();
    editTask(state.task);
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
