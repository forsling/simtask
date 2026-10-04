# Task MCP workstream export

Format: task-mcp/v5

Snapshot only. Editing this document does not update Task MCP.

- Project: webclient (`prj_sample`)
- Project path: /example/webclient
- Workstream: main (`wst_main`)
- Checkout: /example/webclient
- Branch: main
- Scope revision: 3
- Closed tasks: included
- Exported tasks: 2 of 2 in local scope

## Overview

Workflow views match this workstream's queue. Counts cover exported tasks only.
Deferred tasks remain visible when closed tasks are omitted.

- Blocked: unresolved questions: 1
- Awaiting sign-off: 1

| Workflow | Task | ID |
| --- | --- | --- |
| Awaiting sign-off | Show connection health | `tsk_client` |
| Blocked: unresolved questions | Choose the reconnect message | `tsk_protocol` |

## Referenced groups

Progress covers all repositories, including members outside this local scope
or hidden by the closed-task filter. An empty local queue does not mean the
whole group is complete. Only local scoped task details appear below.

### Live viewing

- ID: `grp_rollout`; revision: 3
- Reference: explicit scope, task membership
- Global progress: 0/2 done; incomplete
- Members in this project: 1
- Members in local scope: 1
- Members in this export: 1

#### Group context

> Coordinate client and service work without merging their task lifecycles.

#### Group acceptance criteria

> Both repositories deliver their independently reviewed changes.

## Tasks

### Show connection health

- ID: `tsk_client`
- Workflow: Awaiting sign-off (signoff)
- Stored disposition: open
- Revision: 2; specification: 1
- Workstream memberships: wst_main
- Request origin: user
- Updated: 2026-09-26T00:00:00.000000Z
- Group: Live viewing (`grp_rollout`)

#### Specification

> Show connected, reconnecting and offline states.
>
> - Preserve the last received frame.
> - Explain when the connection is stale.

#### Acceptance criteria

> Transitions are covered by tests and the indicator is keyboard accessible.

#### Original request

> Synthetic example request.

#### Implementation and review history

##### Attempt `att_client`

- State: passed; revision: 2
- Specification: 1; workstream: `wst_main`
- Context: current specification and workstream
- Implementer: example\-implementer
- Updated: 2026-09-26T00:00:00.000000Z

###### Result

> Added the connection-health indicator and transition handling.

###### Evidence

> Synthetic verification: 12 unit tests passed; keyboard navigation checked.

Reviewer: example\-reviewer

###### Review note

> Synthetic independent review passed. Human sign-off is still outstanding.

### Choose the reconnect message

- ID: `tsk_protocol`
- Workflow: Blocked: unresolved questions (unresolved_items)
- Stored disposition: open
- Revision: 2; specification: 1
- Workstream memberships: wst_main
- Request origin: user
- Updated: 2026-09-26T00:00:00.000000Z

#### Specification

> Settle how the UI should describe a retry.

#### Acceptance criteria

> The wording is agreed before implementation.

#### Original request

> Synthetic example request.

#### Questions and prerequisites

##### Unresolved question `unr_protocol`

> Should the message include the next retry time?

#### Implementation and review history

No implementation results recorded.
