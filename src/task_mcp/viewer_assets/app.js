"use strict";
const $ = (id) => document.getElementById(id);
const token =
  location.hash.slice(1) || sessionStorage.getItem("task-token") || "";
if (token) sessionStorage.setItem("task-token", token);
history.replaceState(null, "", "/");
const labels = {
  ready: "Ready",
  pending_acceptance: "Needs acceptance",
  unresolved_items: "Open questions",
  prerequisites: "Prerequisites",
  review: "Needs review",
  signoff: "Awaiting sign-off",
  done: "Done",
  deferred: "Deferred",
  dropped: "Dropped",
  rework: "Rework",
  open: "Open",
};
const state = {
  projects: [],
  streams: [],
  rows: [],
  project: null,
  stream: null,
  groups: false,
  task: null,
  selected: null,
  generation: 0,
};
let submitAction = null;
let submissionPending = false;
function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function button(text, fn, cls = "button") {
  const b = node("button", text, cls);
  b.type = "button";
  b.onclick = fn;
  return b;
}
function badge(view) {
  return node("span", labels[view] || view, "badge " + view);
}
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").className = error ? "error" : "success";
}
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
function projectName(id) {
  return state.projects.find((p) => p.id === id)?.name || "Other project";
}
function streamName(id) {
  const s = state.streams.find((s) => s.id === id);
  return s?.branch || s?.name || "Other workstream";
}
function section(title, text, target = $("detail")) {
  target.append(
    node("h3", title),
    node("div", text || "No details recorded yet.", "prose"),
  );
}
async function boot() {
  try {
    state.projects = await pages("projects");
    $("project").replaceChildren(
      ...state.projects.map((p) => {
        const o = node("option", p.name);
        o.value = p.id;
        return o;
      }),
    );
    if (!state.projects.length) {
      $("queue").append(
        node(
          "div",
          "No projects yet. Initialize a project with Task MCP to begin.",
          "empty",
        ),
      );
      $("new").disabled = true;
      return;
    }
    await chooseProject(state.projects[0].id);
  } catch (e) {
    notice(e.message, true);
  }
}
async function chooseProject(id) {
  const generation = ++state.generation;
  state.project = id;
  state.groups = false;
  state.selected = null;
  state.task = null;
  $("project").value = id;
  $("project-path").textContent =
    state.projects.find((p) => p.id === id)?.canonical_path || "";
  const streams = await pages("workstreams", { project: id });
  if (generation !== state.generation) return;
  state.streams = streams;
  state.stream = state.streams[0]?.id || null;
  renderNav();
  await reload();
}
function renderNav() {
  const all = button(
    "All project tasks",
    () => changeScope(null),
    "nav" + (!state.stream && !state.groups ? " active" : ""),
  );
  $("workstreams").replaceChildren(
    all,
    ...state.streams.map((s) => {
      const b = button(
        s.branch || s.name,
        () => changeScope(s.id),
        "nav" + (state.stream === s.id && !state.groups ? " active" : ""),
      );
      b.append(
        node("small", `${s.status.scoped_count} tasks · registered workstream`),
      );
      return b;
    }),
  );
  $("groups").classList.toggle("active", state.groups);
}
async function changeScope(id) {
  state.groups = false;
  state.stream = id;
  state.selected = null;
  state.task = null;
  renderNav();
  await reload();
}
async function reload() {
  const generation = ++state.generation;
  const project = state.project;
  $("queue").replaceChildren(node("div", "Loading your workspace…", "empty"));
  $("heading").textContent = state.groups
    ? "Shared context, together."
    : state.stream
      ? streamName(state.stream)
      : "Every task, one place.";
  $("eyebrow").textContent = state.groups
    ? "GLOBAL GROUPS"
    : projectName(state.project).toUpperCase();
  $("subtitle").textContent = state.groups
    ? "Whole-group progress across all projects. This is separate from local workstream scope."
    : state.stream
      ? "Local scope · Tasks belonging to this project and workstream."
      : "Project-wide queue · Includes tasks outside the selected workstream.";
  $("new").hidden = state.groups;
  $("filter").hidden = state.groups;
  try {
    const rows = state.groups
      ? await pages("groups")
      : await pages("tasks", {
          project: state.project,
          workstream_id: state.stream,
        });
    const streams = await pages("workstreams", { project });
    if (generation !== state.generation) return;
    state.rows = rows;
    state.streams = streams;
    renderNav();
    renderQueue();
    if (state.selected && rows.some((r) => r.id === state.selected))
      await selectTask(state.selected);
    else if (rows.length) await selectTask(rows[0].id);
    else
      $("detail").replaceChildren(
        node(
          "div",
          state.groups
            ? "No shared groups have been created yet."
            : "A clear queue. Create a task or choose another workstream.",
          "empty",
        ),
      );
  } catch (e) {
    if (generation !== state.generation) return;
    notice(e.message, true);
    $("queue").replaceChildren(
      node("div", "Could not load tasks. Refresh to try again.", "empty"),
    );
  }
}
function renderQueue() {
  const query = $("search").value.toLowerCase(),
    filter = $("filter").value;
  const rows = state.rows.filter(
    (r) =>
      r.title.toLowerCase().includes(query) &&
      (state.groups || !filter || r.view === filter),
  );
  $("count").textContent =
    `${rows.length} ${state.groups ? "groups" : "tasks"}`;
  $("queue").replaceChildren(
    ...rows.map((r) => {
      const b = button(
        "",
        () => selectTask(r.id),
        "task-card" + (r.id === state.selected ? " selected" : ""),
      );
      b.setAttribute("aria-pressed", String(r.id === state.selected));
      b.append(node("h3", r.title));
      if (state.groups) {
        b.append(
          badge(r.complete ? "done" : "Shared group"),
          node(
            "small",
            `${r.progress?.done || 0} / ${r.progress?.total || 0} complete · all projects`,
          ),
        );
      } else
        b.append(
          badge(r.view),
          node(
            "small",
            `Specification ${r.accepted ? "accepted" : "pending"} · Revision ${r.revision}`,
          ),
        );
      return b;
    }),
  );
  if (!rows.length)
    $("queue").append(
      node("div", "No matching tasks. Try another search or filter.", "empty"),
    );
}
async function selectTask(id) {
  state.selected = id;
  renderQueue();
  $("detail").replaceChildren(node("div", "Opening task…", "empty"));
  try {
    const t = (await api("details", { ids: [id] })).items[0];
    if (state.selected !== id) return;
    state.task = t;
    renderDetail(t);
  } catch (e) {
    notice(e.message, true);
  }
}
function renderDetail(t) {
  const d = $("detail");
  d.replaceChildren();
  const row = state.rows.find((r) => r.id === t.id),
    isGroup = t.object_type === "group";
  d.append(
    badge(isGroup ? "Shared group" : row?.view || t.status),
    node("h2", t.title),
  );
  d.append(
    node(
      "div",
      isGroup
        ? "GLOBAL CONTEXT · ALL MEMBER PROJECTS"
        : `Stored disposition: ${t.status} · Specification v${t.spec_revision} · Revision ${t.revision}`,
      "meta",
    ),
  );
  if (isGroup) {
    d.append(
      node(
        "div",
        `${t.progress.done} of ${t.progress.total} members signed off across ${Object.keys(t.progress.by_project).length} projects. An empty group is incomplete.`,
        "note",
      ),
    );
    section("Group context", t.body);
    section("Completion criteria", t.acceptance_criteria);
    d.append(node("h3", "Members · Global progress"));
    t.member_details.forEach((m) => {
      const b = button(m.title, () => navigateMember(m), "task-card");
      b.append(
        node(
          "small",
          `${projectName(m.project_id)} · Disposition: ${m.status}`,
        ),
      );
      d.append(b);
    });
    addHistory(t);
    return;
  }
  if (t.parent_group) {
    const n = node(
      "div",
      `Part of ${t.parent_group.title}. Group completion includes members in other scopes and projects.`,
      "note",
    );
    n.append(
      button(
        "View global group",
        () => openGroup(t.parent_group.id),
        "text-button",
      ),
    );
    d.append(n);
  }
  const actions = node("div", undefined, "actions");
  if (t.status !== "done") {
    actions.append(button("Edit specification", () => editTask(t)));
    if (!t.accepted)
      actions.append(
        button(
          "Accept specification",
          () =>
            decision(
              "Accept this specification?",
              "Your acceptance applies to the exact specification shown. Editing it later requires acceptance again.",
              "accept",
              { task_id: t.id, expected_revision: t.revision },
              "user_note",
            ),
          "button primary",
        ),
      );
    actions.append(
      button("Add question", () =>
        decision(
          "Add an unresolved question",
          "This question blocks implementation until resolved.",
          "question",
          { task_id: t.id, expected_revision: t.revision },
          "text",
        ),
      ),
    );
    if (t.status === "deferred" || t.status === "dropped")
      actions.append(
        button("Resume task", () => disposition(t, "open", "Resume")),
      );
    else
      actions.append(
        button("Defer", () => disposition(t, "deferred", "Defer")),
        button("Drop", () => disposition(t, "dropped", "Drop")),
      );
  }
  d.append(actions);
  section("Specification", t.body);
  section("Acceptance criteria", t.acceptance_criteria);
  if (t.acceptance_note) {
    const n = node("details");
    n.append(
      node(
        "summary",
        t.accepted
          ? "Acceptance decision"
          : "Previous acceptance (specification changed)",
      ),
      node("p", t.acceptance_note, "prose"),
    );
    d.append(n);
  }
  if (t.unresolved_items.length) {
    d.append(node("h3", "Unresolved questions"));
    t.unresolved_items.forEach((q) => {
      const n = node("div", undefined, "question");
      n.append(node("div", q.text, "prose"));
      if (t.status !== "done")
        n.append(
          button("Resolve question", () =>
            decision(
              "Resolve this question",
              q.text,
              "resolve",
              { task_id: t.id, expected_revision: t.revision, item_id: q.id },
              "user_note",
            ),
          ),
        );
      d.append(n);
    });
  }
  if (t.blocked_by.length) {
    d.append(node("h3", "Prerequisites"));
    t.blocked_by.forEach((id) => {
      const b = button(
        "Loading prerequisite…",
        async () => {
          const p = (await api("details", { ids: [id] })).items[0];
          if (p.object_type === "group") await openGroup(id);
          else await navigateMember(p);
        },
        "text-button",
      );
      d.append(b);
      api("details", { ids: [id] })
        .then((r) => {
          b.textContent = r.items[0].title + " · " + r.items[0].status;
        })
        .catch(() => {
          b.textContent = "Prerequisite unavailable";
        });
    });
  }
  if (t.gate_proposals.length)
    section(
      "Proposed gates",
      t.gate_proposals
        .map(
          (p) =>
            `${p.state || p.status || "proposed"}: ${p.text || p.kind || "Gate proposal"}`,
        )
        .join("\n"),
    );
  d.append(node("h3", "Delivery & review"));
  if (!t.attempts.length)
    d.append(
      node("p", "No implementation result has been recorded yet.", "meta"),
    );
  [...t.attempts].reverse().forEach((a) => {
    const n = node("section", undefined, "attempt"),
      current = a.spec_revision === t.spec_revision,
      local = !state.stream || a.workstream_id === state.stream;
    n.append(
      badge(a.state),
      node(
        "p",
        `${a.implementer} · ${streamName(a.workstream_id)} · Specification v${a.spec_revision}${current ? "" : " · Superseded specification"}${local ? "" : " · Other workstream"}`,
        "meta",
      ),
    );
    section("Result", a.summary, n);
    section("Verification evidence", a.evidence, n);
    if (a.review_note) section("Review", `${a.reviewer}: ${a.review_note}`, n);
    if (a.human_review_note) section("Human review", a.human_review_note, n);
    if (t.status !== "done" && current) {
      const ac = node("div", undefined, "actions");
      if (a.state === "review")
        ac.append(
          button("Record my review", () =>
            decision(
              "Record your human review",
              "Confirm that you personally reviewed this result and its evidence, or explicitly decide that further independent review is unnecessary. This does not sign off the task.",
              "human-review",
              { attempt_id: a.id, expected_revision: a.revision },
              "user_note",
            ),
          ),
        );
      if (["passed", "human_review"].includes(a.state)) {
        ac.append(
          button(
            "Approve & sign off",
            () =>
              decision(
                "Sign off this result?",
                `Approve “${t.title}” and the result by ${a.implementer}. This completes the task permanently. Ensure the acceptance criteria and evidence satisfy your requirements.`,
                "signoff",
                {
                  task_id: t.id,
                  expected_revision: t.revision,
                  attempt_id: a.id,
                  verdict: "approve",
                },
                "user_note",
              ),
            "button primary",
          ),
        );
        ac.append(
          button("Request rework", () =>
            decision(
              "Request implementation rework",
              "The accepted specification remains in place; the selected result returns for rework.",
              "signoff",
              {
                task_id: t.id,
                expected_revision: t.revision,
                attempt_id: a.id,
                verdict: "reject",
                rejection: "rework",
              },
              "user_note",
            ),
          ),
        );
        ac.append(
          button("Request specification revision", () =>
            decision(
              "Revise the specification",
              "This invalidates specification acceptance and adds your note as an unresolved question.",
              "signoff",
              {
                task_id: t.id,
                expected_revision: t.revision,
                attempt_id: a.id,
                verdict: "reject",
                rejection: "revise",
              },
              "user_note",
            ),
          ),
        );
      }
      n.append(ac);
    }
    d.append(n);
  });
  addHistory(t);
}
async function navigateMember(m) {
  await chooseProject(m.project_id);
  state.stream = null;
  state.groups = false;
  state.selected = m.id;
  renderNav();
  await reload();
}
async function openGroup(id) {
  state.groups = true;
  state.selected = id;
  renderNav();
  await reload();
}
function addHistory(t) {
  const wrap = node("details");
  wrap.append(node("summary", "Audit history"));
  const content = node("div");
  wrap.append(content);
  $("detail").append(wrap);
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
      r.items.forEach((e) => {
        const entry = node(
          "div",
          `${new Date(e.timestamp).toLocaleString()} · ${e.actor}\n${e.action} · ${e.outcome}`,
          "history",
        );
        const note =
          e.request?.user_note || e.request?.note || e.request?.text || e.error;
        if (note) entry.append(node("p", note, "prose"));
        content.append(entry);
      });
      after = r.next_after_sequence;
      if (after !== null) {
        const b = button(
          "Load more history",
          () => {
            b.remove();
            more();
          },
          "text-button",
        );
        content.append(b);
      }
    } catch (e) {
      content.append(node("p", e.message, "error"));
    }
  }
  wrap.ontoggle = () => {
    if (wrap.open && !loaded) {
      loaded = true;
      more();
    }
  };
}
function field(name, label, value = "", kind = "textarea", required = true) {
  const l = node("label", label);
  l.htmlFor = "field-" + name;
  const i = node(kind === "input" ? "input" : "textarea");
  i.id = "field-" + name;
  i.name = name;
  i.value = value;
  i.required = required;
  $("fields").append(l, i);
  return i;
}
function openDialog(title, description, saveLabel) {
  if (submissionPending) return;
  $("dialog-title").textContent = title;
  $("dialog-description").textContent = description;
  $("fields").replaceChildren();
  $("form-error").textContent = "";
  $("conflict").replaceChildren();
  $("submit").textContent = saveLabel;
  $("dialog").showModal();
}
function confirmation() {
  const l = node("label", undefined, "check"),
    i = node("input");
  i.type = "checkbox";
  i.required = true;
  l.append(
    i,
    document.createTextNode(
      "I confirm this is my decision and I want it recorded in the task history.",
    ),
  );
  $("fields").append(l);
}
function decision(title, description, action, data, key) {
  openDialog(title, description, "Confirm decision");
  field(key, key === "text" ? "Question" : "Your decision note");
  confirmation();
  const taskId = state.task.id;
  submitAction = async (values) => {
    try {
      return await api(action, { ...data, [key]: values.get(key) });
    } catch (e) {
      if (e.conflict) {
        $("conflict").replaceChildren(
          button("Review the current task without losing my note", async () => {
            const latest = (await api("details", { ids: [taskId] })).items[0];
            const n = node("div", undefined, "note");
            n.append(
              node(
                "strong",
                `Current specification v${latest.spec_revision} · Revision ${latest.revision}`,
              ),
              node("p", latest.title, "prose"),
              node("p", latest.body, "prose"),
              node("p", latest.acceptance_criteria, "prose"),
            );
            latest.unresolved_items.forEach((q) =>
              n.append(node("p", q.text, "prose")),
            );
            const a = data.attempt_id
              ? latest.attempts.find((a) => a.id === data.attempt_id)
              : null;
            if (a) {
              n.append(
                node("p", `${a.state}: ${a.summary}\n${a.evidence}`, "prose"),
              );
            }
            n.append(
              button("I reviewed the changes; use this revision", () => {
                data.expected_revision =
                  action === "human-review" ? a.revision : latest.revision;
                $("fields").querySelector("[type=checkbox]").checked = false;
                $("form-error").textContent =
                  "Review your decision note, confirm again, then submit.";
                n.remove();
              }),
            );
            $("conflict").replaceChildren(n);
          }),
        );
      }
      throw e;
    }
  };
}
function disposition(t, value, label) {
  decision(
    `${label} this task?`,
    "The specification and history are preserved. Record the reason for this decision.",
    "disposition",
    { task_id: t.id, expected_revision: t.revision, disposition: value },
    "note",
  );
}
function editTask(t) {
  let revision = t.revision;
  openDialog(
    "Edit specification",
    "Saving a changed specification invalidates its previous acceptance. Your draft stays here if another client edits this task.",
    "Save specification",
  );
  field("title", "Title", t.title, "input");
  field("body", "Specification", t.body, "textarea", false);
  field(
    "acceptance_criteria",
    "Acceptance criteria",
    t.acceptance_criteria,
    "textarea",
    false,
  );
  submitAction = async (values) => {
    try {
      return await api("edit", {
        task_id: t.id,
        expected_revision: revision,
        changes: Object.fromEntries(values),
      });
    } catch (e) {
      if (e.conflict) {
        $("conflict").replaceChildren(
          button("Load current version alongside my draft", async () => {
            const latest = (await api("details", { ids: [t.id] })).items[0];
            const n = node("div", undefined, "note");
            n.append(
              node("strong", `Current revision ${latest.revision}`),
              node("p", latest.title, "prose"),
              node("p", latest.body, "prose"),
              node("p", latest.acceptance_criteria, "prose"),
              button("I reconciled my draft with this revision", () => {
                revision = latest.revision;
                $("form-error").textContent =
                  "Revision updated. Review your draft and save when ready.";
                n.remove();
              }),
            );
            $("conflict").replaceChildren(n);
          }),
        );
      }
      throw e;
    }
  };
}
function createTask() {
  openDialog(
    "Make the next step clear.",
    state.stream
      ? `Create a task in ${streamName(state.stream)}. Your request accepts this specification.`
      : "Create an accepted task in this project's inbox.",
    "Create task",
  );
  field("title", "Task title", "", "input");
  field("body", "What needs to happen?", "", "textarea", false);
  field(
    "acceptance_criteria",
    "How will you know it is done?",
    "",
    "textarea",
    false,
  );
  field("user_request", "Your request / authorization note");
  submitAction = async (values) => {
    const r = await api("create", {
      ...Object.fromEntries(values),
      project: state.project,
      workstream_id: state.stream,
      scope: state.stream ? "workstream" : "inbox",
    });
    state.selected = r.id;
    return r;
  };
}
$("form").onsubmit = async (e) => {
  e.preventDefault();
  if (submissionPending) return;
  submissionPending = true;
  $("submit").disabled = true;
  $("cancel").disabled = true;
  $("close").disabled = true;
  $("form-error").textContent = "";
  try {
    const result = await submitAction(new FormData($("form")));
    $("dialog").close();
    if (!result?.stopped) {
      notice("Saved to your local task history.");
      await reload();
    }
  } catch (e) {
    $("form-error").textContent =
      e.message +
      (e.conflict
        ? " Your draft is preserved. Reconcile the current version before retrying."
        : " If the response is uncertain, inspect the task before retrying.");
  } finally {
    submissionPending = false;
    $("submit").disabled = false;
    $("cancel").disabled = false;
    $("close").disabled = false;
  }
};
$("cancel").onclick = $("close").onclick = () => {
  if (!submissionPending) $("dialog").close();
};
$("dialog").oncancel = (e) => {
  // Escape must not abandon a write whose eventual outcome still owns this dialog.
  if (submissionPending) e.preventDefault();
};
$("project").onchange = () =>
  chooseProject($("project").value).catch((e) => notice(e.message, true));
$("groups").onclick = () => {
  state.groups = true;
  state.selected = null;
  renderNav();
  reload();
};
$("search").oninput = $("filter").onchange = renderQueue;
$("new").onclick = createTask;
$("refresh").onclick = () => reload();
$("stop").onclick = () => {
  openDialog(
    "Stop this local viewer?",
    "This closes the listener. Task MCP over stdio continues independently. Relaunch with task-mcp ui when needed.",
    "Stop viewer",
  );
  confirmation();
  submitAction = async () => {
    const result = await api("stop");
    $("dialog").close();
    $("detail").replaceChildren(
      node("div", "Viewer stopped. You can close this tab.", "empty"),
    );
    $("queue").replaceChildren();
    $("new").disabled = true;
    notice("Viewer stopped.");
    submitAction = null;
    return result;
  };
};
boot();
