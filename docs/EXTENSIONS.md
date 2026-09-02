# Skein extension authoring

Skein supports trusted workplace extensions without a source-tree fork. A
workplace can keep its Python package, frontend package, content, policies,
data, and deployment files in a private repository.

The extension APIs are narrow by design. Skein does not scan installed
packages. It does not run arbitrary browser code at runtime. The deployment
must explicitly select each trusted module.

Start with [Workplace setup](SETUP.md) to create and deploy a private
consumer repository. The fictional
[Atlas example](../examples/workplace-extension/README.md) uses every
supported contract.

## How it works

Skein does not find your code. Your code starts Skein.

Most applications are extended by editing them. Skein works the other way
around. Skein is a library that your private code calls. Your code creates
the application and hands Skein a list of your additions. Skein never scans
installed packages, and it never loads a module the deployment did not name.
Your whole connection to core is one small file in your own repository, shown
under "Compose a backend module" below.

That list is one `SkeinModule`. Each entry adds one thing and declares what
that thing does. The declaration names the identity it runs as, the
permission it needs, and whether it reads or writes. It also gives a risk
level and a time limit. Skein reads these declarations when the application
starts. A duplicate name, a missing permission, or an unsupported core
version stops the application there, in front of the person who deployed it.

One permission system covers core screens, contributed routes, agent tools,
scheduled jobs, and workflow steps. Your rules join that system. A rule
answers `permit`, `deny`, or `review`. A rule can make a decision stricter.
It can never make one looser, so a workplace permit never removes a core
denial.

This is one write, from an integration through to the database:

```mermaid
flowchart TD
    START[Your route or scheduled job starts] --> GATE1{Is this operation<br/>allowed at all?}
    GATE1 -->|no| STOP1[Refused. Nothing runs.]
    GATE1 -->|yes| CALL[Your code calls a public<br/>command such as create_task]
    CALL --> GATE2{What do the rules say<br/>about this write?}
    GATE2 -->|deny| STOP2[Refused. Nothing is written.]
    GATE2 -->|review| HOLD[Skein saves the exact command<br/>and answers with a review number]
    GATE2 -->|permit| WRITE[Skein writes the row]
    HOLD --> HUMAN{A qualified person answers}
    HUMAN -->|approve| WRITE
    HUMAN -->|reject| STOP3[Nothing is written.]
    WRITE --> RECORD[Skein records the author,<br/>writes the activity row,<br/>and emits a domain event]
```

The `review` branch is what lets an unattended integration ask a person.
Skein stores the command itself, so approval runs that exact command later.
Your integration stays the recorded author, and the reviewer is recorded
beside it.

Your extension owns its PostgreSQL schema, routes under its namespace,
rules, and content files. Core owns its schemas, pages, and every module
outside the three named below.

## Supported boundaries

The backend extension API version is `1.0`. Import backend contracts only from
these modules:

- `app.extensions` contains composition and contribution contracts.
- `app.public` contains commands, queries, events, workflows, and public errors.
- `app.main.create_app` creates a composed FastAPI application.

Do not import `app.services`, `app.db`, `app.routes`, or `app.agents`. These
modules are internal. Their functions and dictionary shapes can change in a
compatible core release.

The frontend extension API version is also `1.0`. Import frontend contracts
and components only from `@miloctl/skein-extension-api`.

| Concern | Contract | Composition time |
|---|---|---|
| Routes | `RouteContribution` | Application creation |
| Scheduled jobs | `JobContribution` | Application creation |
| Policy | `PolicyContribution` | Application creation |
| Identity attributes | `IdentityContribution` | Each resolved identity |
| Service identities | `ServiceIdentityContribution` | Application creation |
| Agent context | `ContextContribution` | Agent construction |
| Governed tools | `ToolContribution` | Agent construction |
| Specialists | `SpecialistContribution` | Agent construction |
| Events | `EventContribution` | Outbox delivery |
| Data migrations | `MigrationContribution` | Application startup |
| Workflow actions | `WorkflowActionContribution` | Workflow preparation |
| Navigation and dashboard cards | `FrontendExtension` | Frontend build |

Frontend routes, task detail panels, general actions, forms, notifications,
theme packages, and terminology packages are not version 1.0 extension slots.
Add a core slot only when a real workplace extension needs it.

## Compose a backend module

Create one immutable `SkeinModule`. Pass it to the application factory from a
private composition root.

```python
from app.extensions import (
    PolicyResource,
    RouteContribution,
    RouteOperationContribution,
    SkeinModule,
)
from app.main import create_app

from atlas.router import router

atlas = SkeinModule(
    module_id="atlas.workplace",
    version="2.0.0",
    extension_api="1.0",
    minimum_core="0.3.0",
    maximum_core_exclusive="0.6.0",
    routes=(
        RouteContribution(
            "atlas.workplace.routes",
            router,
            operations=(
                RouteOperationContribution(
                    "POST",
                    "/api/extensions/atlas.workplace/sync",
                    "atlas.integration.sync",
                    PolicyResource("atlas"),
                    "write",
                    "high",
                ),
            ),
        ),
    ),
)

app = create_app(modules=(atlas,))
```

The default `app.main:app` remains unchanged. An extension deployment starts
its private composition root instead.

Module IDs and contribution names must use the module namespace. Extension
routers must start with `/api/extensions/{module_id}`. Startup rejects these
conditions:

- Duplicate module or contribution names
- Unsupported extension API or core versions
- Route or tool name collisions
- A route without an exact operation policy contract
- Invalid tool, event, migration, or workflow metadata

The module list is an allowlist. Import and instantiate modules in the private
composition root. Do not load all Python entry points automatically.

Version 1.0 has no startup or shutdown hook. Initialize private resources
lazily from a route, job, event subscriber, or migration. Each of these runs
with a declared identity, policy action, and timeout. A bare startup hook
would run trusted code with none of those bounds. If a failed extension
migration raises, the application does not start.

## Protect routes and apply policy

The backend is the enforcement point. Frontend capability checks only control
presentation.

Use `ExtensionRouteServicesDep` on an extension route. It supplies the mapped
subject, the composed policy engine, and the public work facade. Do not read
private values from `request.app.state`.

The grant ends with the response. A route declares no deadline, so a thread
the handler starts would otherwise keep writing core rows under the route's
provenance after the response and after shutdown. A later call returns
`EXECUTION_CONTEXT_CLOSED`. Use a `JobContribution` for background work.

One case escapes that close: a `BackgroundTasks` task added by the handler
runs before the dependency teardown, so it still writes under the route's
grant. Do not write core rows from a background task in a contributed route.
Use a `JobContribution`, which declares its own identity, policy action, and
timeout.

```python
from fastapi import APIRouter

from app.extensions import (
    ExtensionRouteServicesDep,
)

router = APIRouter(prefix="/api/extensions/atlas.workplace")

@router.post("/sync")
def sync(services: ExtensionRouteServicesDep):
    return run_sync(
        services.work_items,
        services.command_context(project_type="standard"),
    )
```

Skein applies the composed policy to all namespaced extension operations. This
first check uses the stable REST action for the route. Each private operation
also declares its domain action, resource, effect, and risk. Skein checks this
contract before it calls the route. Startup rejects a missing or extra
operation contract.

The version 1 route contract has a static resource plus an optional path ID.
It does not resolve project or classification data for an arbitrary private
entity. Use the public command facade for core work. If a private route needs
dynamic domain policy, perform a typed check in its own adapter. Add a core
resource resolver only when a shared use case exists.

Skein also applies the same composed policy to core REST operations, agent
tools, classified MCP tools, workflow steps, jobs, and capability responses.
Core REST actions use this stable form:

```text
skein.rest.<method>.<literal-path-segments>
```

For example, `PATCH /api/tasks/{task_id}` is
`skein.rest.patch.tasks`. A policy rule can return `permit`, `deny`, or
`review`. A deny returns `POLICY_DENIED`.

Where a review goes next depends on what the rule named. A rule on a **public
work command** holds the command: Skein stores it, answers
`REVIEW_REQUIRED` with the `review_id` that a human can approve, and runs the
exact saved command on approval under a new grant. This works from a route, a
scheduled job, a governed tool, an event subscriber, and a workflow action, so
an unattended integration can hold a regulated write for a manager instead of
failing it. Record the `review_id`: the write has not happened yet, so the
command's idempotency key protects nothing, and the next run proposes the
same write again. A batch keeps its own record and skips what it already
asked about:

```python
for item in client.open_items():
    if store.query_one("SELECT 1 FROM held WHERE external_id = ?", (item.id,)):
        continue
    try:
        view = context.work_items.create_task(command_for(item), command_context)
    except PublicError as exc:
        if exc.code != "REVIEW_REQUIRED":
            raise
        store.execute(
            "INSERT INTO held (external_id, review_id) VALUES (?, ?)",
            (item.id, exc.review_id),
        )
        continue
    remember(item, view)
```

One held item does not stop the batch. The rest of the run lands, and a
reviewer answers the held ones once.

A rule on the **operation action itself** — the REST action, a contributed
route operation, or a scheduled job — has no request to resume, so it returns
`POLICY_REVIEW_UNSUPPORTED` and does not run the operation. Name the command
action when the intent is to hold one write for approval.

The existing strong-identity, administrator, visibility, authority, and review
checks still run. A workplace permit cannot remove a core denial. Deny is
stronger than review, and review is stronger than permit.

Declare the stable actions that a policy rule can inspect. Put an `actions`
tuple on the callable rule object. Keep the `PolicyContribution(name, rule)`
constructor compatible with extension API 1.0 cores. The reference Atlas
package uses this form:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class AtlasPolicy:
    skein_policy_actions: tuple[str, ...] = (
        "atlas.dashboard.view",
        "atlas.integration.sync",
    )

    def __call__(self, request: PolicyInput) -> PolicyDecision | None:
        if request.action not in self.skein_policy_actions:
            return None
        return decide_atlas(request)


PolicyContribution("atlas.workplace.policy", AtlasPolicy())
```

A callable with no `skein_policy_actions` tuple keeps the extension API 1.0
behavior. It can inspect every action. The namespaced attribute does not
reinterpret an existing callable's unrelated `actions` member. Skein treats
unclassified free-form text as unsafe when an applicable workplace rule
exists. A scoped rule does not disable unrelated core readouts.

Auth bootstrap endpoints keep their specialized gates. Signed Slack and forge
requests first pass signature checks. Skein then evaluates
`skein.integration.slack` or `skein.integration.forge`. CI requests evaluate
`skein.integration.ci`. A direct integration route cannot resume a review.

Collection and composite reads use the same policy engine. Row-shaped results
apply policy to each visible domain resource in one database snapshot.

Some aggregates cannot remove one denied resource without changing their
meaning. Skein checks every project domain that can affect these aggregates.
This check includes hidden inputs whose names are masked in the response.
The aggregate fails closed if policy denies one of these project domains.

Migration 019 gives each new notification a source entity and source ID.
Migration 020 also saves its creation-time policy context. Policy-aware
readers check both the saved context and the current source before they return
the static body. They omit an old notification that has no safe context when
workplace rules are active. Saved reviews use their stored policy input and
their current target in the same way.

Some older free-form derivatives have no reliable source. Activity summaries
and findings are examples. Skein omits or refuses unclassified text when
workplace rules are active. It does not scan all historical rows on each
request. Exact policy checks run beside the rows that contribute to a result.

Delegated agent inboxes and crew context packs can remove individual tasks.
Skein applies policy to each task before it returns or stores the content.

An engagement composite applies one action to the engagement and each nested
resource. This rule covers milestones, tasks, blockers, promises, lessons,
decisions, notes, questions, allocations, and artifacts. A permitted task
cannot name a denied relationship. Skein removes each denied relationship ID
and title before it returns the task.

A filtered first context-pack read does not store content that policy removed.
If policy permits all content, Skein stores the exact approved body. It does
not rebuild the body between the policy decision and the database write.

The unattended runner checks a delegated task and sends its quiet-work notice
in one database transaction. A concurrent project change waits until the
notice transaction is complete.

The runner also checks the current due tasks and claims the daily model turn
in one transaction. A project change that finishes before this claim changes
the policy result. A concurrent change waits until the claim is complete.
Scoped engagement packs, briefs, and health reports remove legacy tasks whose
direct engagement and milestone parent disagree.

Skein resolves linked project context before it applies policy. Hidden,
missing, or conflicting legacy parents fail closed without exposing the
parent ID or project class.

Local domain writes keep context resolution, policy, and mutation in one
write transaction, with the deciding row held. This rule covers REST, stock tools, MCP, public
commands, and verdict-time execution. External I/O uses its documented
idempotency and completion contract instead of a database transaction.

## Map enterprise identity

OIDC validation remains in the core. An identity contribution maps verified
directory groups into workplace roles, capabilities, and attributes.

```python
def map_identity(name: str, groups: tuple[str, ...], strong: bool):
    is_manager = "delivery-managers" in groups
    return {
        "roles": ("delivery-manager",) if is_manager else (),
        "capabilities": ("atlas.approve",) if is_manager else (),
        "identity_proved": strong,
    }
```

Do not validate tokens in an extension rule. Do not trust browser-supplied
roles. Use the groups that the configured OIDC claim supplies.

Configure a directory resolver for each OIDC identity contribution. Skein
uses the resolver to refresh group membership before a verdict. This rule also
applies to an OIDC user who has no groups. A missing record or unavailable
resolver fails closed. Skein stores the original authentication strength and
never increases it during refresh. Skein also rejects an inactive local user.

Exactly one identity contribution can own group refresh. Set
`resolves_groups=True` on that directory resolver. Its resolver must
return a `groups` key. An empty tuple is a successful group refresh. A
profile resolver keeps the default `resolves_groups=False`. It cannot
return groups or replace an unavailable group resolver. These rules prevent
stale approval groups when a workplace uses more than one identity module.

Register every job and event subject with `ServiceIdentityContribution`.
Service identities do not pass through the human identity mapper. Startup
reserves their names and rejects a collision with a human account, a core
actor, an MCP actor, a specialist, a persona, or a flock. This check includes
stock content and deployment overlays. The API and standalone MCP process use
the same check. The API logs an invalid MCP actor and keeps REST available.
The standalone MCP process stops because it cannot operate without its actor.
Neither path reuses a human-owned roster name for a machine identity.
Skein reserves each human or machine name in one database transaction. The
transaction covers exact names and Unicode case-folded variants. A concurrent
human sign-in cannot claim a machine name during that reservation. Skein
refuses the human request if the machine reservation wins.
Validated OIDC reads reserve human ownership before a handler runs. Weak
trusted-header reads do not supply enterprise groups. They do not create roster
rows or receive strong or private-data authority. Thus, group-gated extension
contributions stay unavailable in trusted-header mode.
Established OIDC users use a read-only ownership check. A first ownership
claim can wait on a row lock. Skein returns a retryable `503` if
that lock is busy.
Restart Skein after you change persona or flock overlays so startup can
validate the complete identity roster.

An older database can contain two names that differ only by case or Unicode
form. Skein reports this conflict on `/api/health`. It refuses both identities
until an operator repairs the roster. Run this command on the server:

```sh
python -m app.identity_audit
```

The command lists each conflicting group. Rename one row at a time:

```sh
python -m app.identity_audit rename RACE-OWNER person-owner
```

The command acts only on a current identity conflict. It refuses an ordinary
account, an invalid or reserved target, a target name that already exists, and
a target with unrelated private ownership. It validates every target rule
before private data moves. It records `system` as the core activity actor. It
also writes a private administrative audit before it moves private ownership.
The audit contains no note content.

The core and private databases cannot share one transaction. The private move
commits first. If the core rename fails, correct the reported cause and repeat
the same command. The private operation is idempotent.

Skein does not merge conflicting rows automatically. An automatic merge could
combine human and agent authority, provenance, or private ownership. The same
audit also detects an old human row that now conflicts with a persona, flock,
or reserved core actor. The installed-artifact upgrade rehearsal includes the
legacy folded-roster check.

One canonical core-machine set protects module registration, runtime
composition, human authentication, health checks, and repair. `anonymous` is
the one documented compatibility exception. It is a synthetic unnamed subject,
not an authenticated person or an extension-owned machine identity. OIDC,
personal API keys, bootstrap keys, explicit trusted headers, delegation, and
authority changes cannot claim it. Signed Slack identities cannot claim it
either. Browser OIDC token exchange refuses it before the UI reports a
successful sign-in. Only an absent weak identity and old unnamed records use
it.

Persona and flock overlays cannot use any composed machine identity. This
includes core actors, private services, specialists, and the configured MCP
actor. Startup rejects a conflict before it reserves an actor. The set of
persona and flock slugs is fixed for one application lifetime. You can edit an
existing file live. Restart Skein after you add or rename an identity-bearing
file. A new file stays unavailable until restart. Its valid slug is reserved
immediately, so a human, delegated agent, service, or tool cannot claim it
during the restart window.

Core migration 018 records durable ownership for each roster identity. New
rows identify a human, generic agent, content item, service, specialist, MCP
actor, or core actor. This prevents a later concern from silently using the
same provenance and authority.

Startup creates an agent-owned row for every accepted persona and flock slug.
The row remains if a mounted file is temporarily removed. Thus, a human or
generic agent cannot claim the slug before the file returns.

Migration 018 assigns stock content and the core Chief automatically. It
cannot infer private ownership from an old generic agent row. Before the first
restart on the new core, list ownership conflicts:

```sh
python -m app.identity_audit
```

If an old row belongs to private persona or flock content, assign it:

```sh
python -m app.identity_audit claim-content atlas-auditor
```

If an old row belongs to a private service, specialist, or MCP actor, assign
its exact owner:

```sh
python -m app.identity_audit claim-machine atlas-sync service:atlas.workplace.sync-identity
python -m app.identity_audit claim-machine atlas.workplace.delivery-specialist specialist:atlas.workplace.delivery-specialist
python -m app.identity_audit claim-machine mcp-agent mcp
```

The command accepts only an existing generic agent row. It never creates a
row or takes a human identity. It records `system` and the assigned owner in
the activity chain. Keep these commands in the deployment upgrade procedure.

## Use public work commands

`WorkItems` is the public command and query facade for tasks and blockers. It validates policy
and uses the existing service write path. That shared path preserves
provenance and writes a versioned outbox event in the same transaction for
human, agent, and integration callers.

```python
from app.extensions import JobExecutionContext
from app.public import CreateTaskCommand

def import_work(context: JobExecutionContext):
    task = context.work_items.create_task(
        CreateTaskCommand(
            title="Map an external work item",
            idempotency_key="atlas-item:ATLAS-7",
        ),
        context.command_context(project_type="standard"),
    )
    return {"created": 1, "task_id": task.id}
```

A blocker is Skein's word for an impediment. Use `CreateBlockerCommand` and
`UpdateBlockerCommand` rather than filing one as a task: the entity carries
its own impact, escalation clock, and resolution. A blocker update can only
resolve it or correct its wording. Escalation belongs to the scheduled sweep,
so a command that set it would move a clock the sweep owns. The sweep emits
`skein.blocker.updated` when it escalates, so a subscriber sees that
transition even though no command caused it.

A promise is a commitment with a direction, an audience, and a settlement
status. `CreatePromiseCommand` records one and `UpdatePromiseCommand` settles
it as `kept`, `missed`, or `withdrawn`. A promise settles once.

Blocker and promise commands need `minimum_core = "0.2.2"`.

Extensions receive typed views. They do not receive core rows or a core
connection. Propose a new public command when an extension needs a stable core
operation that is not available. Do not make every internal service public.

Machine execution contexts read workspace tasks only. This rule applies to
jobs, event handlers, workflows, services, MCP, and agent tools. A machine
actor name is write attribution. It is not proof of human identity and does
not grant crew or private reads. A route contribution can read scoped work
only through the strongly authenticated human subject that the core supplies.

A task cannot have a wider audience than a linked engagement, milestone, task,
blocker, or promise. A crew link must use the same crew. A private link
requires a private task. Skein checks the milestone and its engagement parent
in the same transaction as policy and the write. Task responses redact a
relationship identifier when the reader cannot read the linked row. This
redaction also protects rows that an older Skein release created before the
containment rule existed.

## Register tools and specialists

A tool contribution declares its full security contract:

- Stable names and versions
- Pydantic input and output schemas
- Read or write effect
- Risk level and policy action
- Agent allowlist and required capabilities
- Timeout and safe error codes

Every tool write records a receipt with service provenance. This behavior is
fixed. A contribution cannot opt out of it.

Raise `PublicError` from `app.public` when a tool returns a safe failure. Skein
preserves the code only when the contribution declares it in `error_codes`.
Skein returns `tool_error` for an undeclared code. Keep `detail` safe for the
requester because it is part of the tool result.
This tool behavior requires core `0.2.1` or later. Set `minimum_core` to
`0.2.1` when a package depends on a declared tool error. A package that keeps a
`0.2.0` floor receives the older generic tool error on that core. Workflow
actions already support public declared errors on core `0.2.0`.

A retryable `PublicError` with status 503 adds `Retry-After: 60`. The
scheduled job wrapper records the machine code and retryable value. It does
not log the detail or the chained adapter error. Read the Contracts section
of CHANGELOG.md for the minimum core version that includes this behavior.

Unknown effects fail closed. A review decision creates a durable proposal.
Skein stores the executable arguments outside the review queue. A qualified
human can approve the proposal and run the exact saved call. Before each
verdict, Skein runs the registered resource resolver again. Current target
classification controls both approval and rejection.

The review queue is a REST surface. `GET /api/review` lists pending
proposals. `POST /api/review/{change_id}/approve` and
`POST /api/review/{change_id}/reject` record the verdict. Both accept a
JSON body with a `note` string.

Approval requires a human reviewer. It does not require a second person by
default: the person who asked an agent to act can approve the result. If a
workplace needs separated duties, return `approver_groups` or
`approver_capabilities` on the review decision. Skein then refuses every
approver outside that set.

`SKEIN_REVIEW_SEPARATION=1` applies the same rule to every proposal without a
policy rule: the person a proposal came from cannot approve it. The two
checks compose, and both must pass. Rejection stays open to every qualified
reviewer, because a rule that traps a proposal in the queue is worse than one
person declining it.
The review service supplies the exact current decision to the executor. The
executor checks the bound request and contract. It does not ask a mutable
policy source for a second decision after the reviewer qualifies.

`ToolHandlerContext.subject` is the human or service requester for policy.
`ToolHandlerContext.agent` is the specialist that executes the tool.
`ToolHandlerContext.correlation_id` links the tool receipt to outbox events.
The same correlation ID follows a reviewed call from its queue entry to its
final execution receipt.
If a tool writes through `WorkItems`, use `ToolHandlerContext.command_context()`.
The composed execution boundary binds the agent, origin, contribution
namespace, and correlation ID. A caller-created `CommandContext` cannot write.
Caller-created route, job, tool, event, or workflow execution contexts also
cannot mint command authority. Only the core adapter binds that authority.
`WorkItems` does not expose a binding method. The core keeps an internal
identity registry for the exact execution object. It also saves the granted
subject, actor, namespace, receipt namespace, and correlation ID. Changing a
context field does not change that grant. Changing an issued command makes
the facade reject it.
Receipt namespaces include the contribution kind, so a job and tool can use
the same stable name without sharing idempotency receipts. This split records
the agent as the writer without giving it the requester's roles.

Stock model-facing reads use actions in the form `skein.tool.<tool-name>`.
Stock writes use their domain action through the shared write gate. The four
stock writers with sponsor or artifact rules also use the tool action. A
review of one of these four writers stores the exact input and can resume.
Stock playbook proposals also store the expected content digest. Changed
playbook content cannot use an earlier agent-tool verdict.

A write-capable MCP tool needs the same metadata. Skein omits an unclassified
MCP tool from the agent. A review decision creates a durable proposal. Skein
rechecks the identity, policy, server ID, tool version, and exact input at
approval time. Each MCP server needs a stable, unique name. Skein omits
same-named tools from different servers because a model could not select them
safely.
Approval and rejection use current MCP metadata. If the tool is removed,
approval fails. A qualified reviewer can still reject the stale proposal.

MCP metadata must use a non-empty policy action and arrays for allowlists,
capabilities, and error codes. A remote tool cannot use the same model-facing
name as a stock or contributed tool. MCP policy has no generic way to resolve
an arbitrary remote identifier to a Skein project. Use a governed
`ToolContribution` when a remote write needs target project classification.

Use `auth_token_env` to name an environment variable that contains an MCP
token. An inline `auth_token` remains compatible, but Skein logs a warning.
Keep tokens in the deployment secret manager.

A personal MCP server (`services/mcp_servers.py`, Settings → Connections)
carries no governance block, and its owner writes none. Skein derives the
effect and risk of each tool from the server's MCP annotations, and it
stamps the version from the input schema. Every write from a personal
server opens a review, also when the policy engine permits it, because an
`mcp-tool` authority grant was made for operator-classified servers. A read
opens one review the first time that tool runs, per server, tool, and
version, because annotations are the server's own claim. A personal tool
joins only the chat turns its owner drives. Its token is
sealed under `SKEIN_CREDENTIAL_KEY` and never travels in an export.

A personal server that uses OAuth 2.1 signs in through the `mcp` package's
client provider (`agents/mcp_oauth.py`). The provider runs the grant inside
the first request of a connect. Skein parks the authorization URL for the
settings card and receives the code on `/api/mcp/oauth/callback`, which is
open on the perimeter and keyed on the provider's state nonce. A connect
from a chat turn refuses a fresh authorization demand at once. Pending
flows live in the process that started them, so the callback must reach
that replica.

Skein serves the same MCP tools two ways. The in-API endpoint
(`/api/mcp-server`, `mcp_server.remote_app`) runs inside the API process with
the API's composition, and resolves the caller per request: the policy subject
is the person, the acting identity is their `<name>-mcp` agent, and the
requester is the person. The standalone MCP server is a separate process and composes its own module
list. Set `SKEIN_MCP_MODULES` to the dotted path of a module whose `modules`
attribute is the same tuple the private composition root passes to
`create_app`. The reference package exports one at
`atlas_skein.composition`:

```sh
SKEIN_MCP_MODULES=atlas_skein.composition SKEIN_MCP_USER=you-mcp \
    python -m app.mcp_server
```

Without this value the MCP process composes core only. A workplace
deployment must set it, or MCP calls run without the workplace policy,
identity, and tool contributions that the API process enforces. A value
that does not resolve stops the server with the reason on stderr.

Reviewed tools store their exact input in the core review database. Do not put
credentials or unneeded sensitive content in tool arguments. A held command
is queued once per attempt, so an integration that retries a refused write
files one proposal per retry: record the `review_id` and skip what you
already asked about. Apply the
workplace backup and retention policy to this database.

Policy rules receive the resource attributes for a decision. For task
commands these attributes include the title and description. A durable
review stores the same policy input in the core database. Treat contributed
policy rules and the review store as readers of that content.

A reviewed tool that uses the supplied `WorkItems` service requires core
`0.2.1` or later. Set its module's `minimum_core` to `0.2.1`. A tool that does
not perform a reviewed local write can retain a `0.2.0` floor.

A timed-out synchronous write has the status `completion_unknown`. A worker
thread can finish after the deadline. Make write handlers idempotent. Use the
supplied stable identifiers.

A specialist contribution contains its prompt, context sources, tools, and
required capabilities. The Chief of Staff reads the registry. A private
package does not import or patch the Chief implementation.

Use a registered tool for side effects. A context contribution declares a
read policy action, risk, capabilities, deadline, and output limit.
Skein records a content-free receipt for each retrieval. The provider must be
synchronous and must not write. Its single string argument is the authenticated
requester name. It is not the current question. Use a governed read tool when
retrieval needs structured input, review, or a custom resource resolver.

## Subscribe to events

All shared task writes create version 1 domain events in the durable outbox.
Each event has an ID, type, schema version, time, actor, origin, resource
reference, safe change summary, visibility, and correlation data.

The version 1 catalog has these event types:

- `skein.task.created`
- `skein.task.updated`
- `skein.blocker.created`
- `skein.blocker.updated`
- `skein.promise.created`
- `skein.promise.updated`

An entity that has a public command has its events. The blocker and promise
pairs both need core `0.2.2`.

`app.public.events.EVENT_TYPES` carries the same list. Composition rejects a
subscription to an event type or schema version outside the catalog.

Subscribers select event types, schema versions, and visibility tiers. The
dispatcher retries failures. It records one delivery receipt for each event
and subscriber. A subscriber must also use the event ID as the idempotency key
for its external side effect.

If synchronization and event subscribers update the same remote entity, put
both updates in one extension-owned outbox. Claim one row. Commit the claim
before the remote call. After success, mark the row only when the lease token
still matches. Keep later rows for one entity blocked until the earlier row
settles.
Use retry delays for temporary errors and dead-letter rows for permanent errors.
Apply count and wall-clock limits to each drain.

Each subscriber declares a service identity, policy action, effect, risk, and
timeout. Skein checks policy before it calls the handler. A write timeout is
terminal because the side effect can finish after the deadline. Handlers must
be synchronous and return `None`. A policy review is unsupported for a
subscriber because there is no safe request to resume. Use a governed workflow
when the side effect needs human review.

A scheduled job declares the same identity and effect data. Skein claims each
time window before it calls an extension job. This claim prevents two workers
from running the same extension job in the same window. Job handlers are also
synchronous and bounded by their declared timeout. A timed-out write reports
`completion_unknown`. Make external writes idempotent. A policy review is
unsupported for a scheduled job. Use `JobExecutionContext.run_id` as the
idempotency key for external writes in that run.

The core unattended agent job receives the composed registry and policy
engine. The scheduler action is the outer gate. Each agent tool is a second
gate that uses the active agent subject, tool risk, and project context.

Event data does not contain task body text. Query authorized public data when
the integration needs more information.

Do not use an event subscriber for a synchronous invariant. Core services own
their transaction invariants.

## Own extension data and migrations

Use `ExtensionStore` for a small extension-owned set of tables. Construct it
with a NAME, and core gives it a schema of its own (`ext_<name>`). Its
migration stream is namespaced and independent from core migration numbers.

Before deployment, the database administrator creates this schema and makes
the Skein application role its owner. The application role does not need
and must not get database-wide `CREATE`. Dots and dashes normalize to
underscores. Composition refuses collisions and names longer than PostgreSQL
can store.

Unqualified names in the store's SQL resolve inside that schema and nowhere
else. That prevents accidents. It is not an isolation boundary: an in-process
module runs as the Skein process on one connection role, so SQL that NAMES
`public.tasks` still reaches core. Keep untrusted code out of the module list
and use a sidecar service for it.

The store supplies `execute`, `query`, `query_one`, `migrate`, and
`transaction`, which nests. `execute` returns the first column of a
`RETURNING` row, or 0 when the statement has none — an insert whose id you
need must say `RETURNING id`, because there is no last-inserted-id to hand
back. There is no `connect`: a raw connection would
escape the schema scoping and the placeholder translation that make the rest
of the contract hold.

Use namespaced metadata only for sparse annotations with simple validation.
Use extension-owned tables or an external store for indexed, relational, or
invariant-rich data. Do not create a general entity-attribute-value store.

Store stable Skein identifiers as external references. Do not create a
foreign key into a core table. The public API, events, and commands define
the consistency boundary.

Keep the mapping from your own identifier to the Skein one in that store.
`WorkItems` fetches a task by its Skein id alone, so an idempotency key
prevents a duplicate write but cannot find the row it protected.

Keep each migration version and name append-only. Add a new version for every
change. Test a fresh database and an upgrade from the previous extension
release.

The daily local database recovery unit includes each store that a composed
migration contribution declares. Set `include_in_backup=False` when the
contents are rebuildable from the source system. This setting requires core
`0.2.2` or later. The configured public platform mirror excludes every
extension schema. Core cannot classify data that a private package stores.
Retention inside the store stays extension-owned, so a store that grows
without bound is the private package's job to prune.

## Add workflow behavior

Version 1 playbooks support four workflow step types:

- `condition` compares one context value and selects one branch.
- `approval` asks the policy engine for permit, deny, or review.
- `action` calls one registered typed workflow action.
- `checkpoint` records a named completed point.

Workflow actions declare schemas, effect, risk, policy action, timeout, and
safe error codes. A playbook cannot call arbitrary Python or an arbitrary URL.

The workflow engine is core machinery, and `app.public` does not export it.
The composed application issues the workflow authority. A caller-created
context cannot run an action and returns `WORKFLOW_CONTEXT_REQUIRED`. Start a
workflow-backed playbook through the REST endpoint. Resume it through the
review endpoint. This rule binds the requester, policy, run ID, and action
registry to one trusted application boundary.

The REST, deterministic chat, and agent-tool paths use `playbook.create`.
They use one resolver to load the project class from the selected playbook.
The REST path
stores a workplace-policy review before it creates project work. A qualified
human can approve it. Skein then runs the exact saved playbook request and
records the result. A workflow approval step can create a second proposal.

Each durable playbook review stores a canonical content digest. The digest
keeps YAML types distinct, including dates, byte strings, sets, and mixed map
keys. Skein compares the digest before approval. A changed overlay needs a new
review. No changed task, action, or project class can use the old verdict.
Skein still lets a qualified reviewer reject a stale or pre-digest proposal.
This action removes old work from the pending queue without executing it.
Digest values include an algorithm prefix. A stored digest without that
prefix never approves content. An agent-origin proposal with no saved policy
binding cannot be approved. A qualified reviewer can reject it.

Core migration 017 marks the review-contract version. An unbound row from an
older core remains version 0. Approval fails closed for that row. New core
proposals use version 1 and keep their existing review behavior.

Skein evaluates all selected workflow steps before it creates core work. It
binds each successful decision to that immediate execution. The runner cannot
observe a second policy result after core work exists.

Each approval grant covers one structural step path. It also covers one input,
action version, policy result, and complete workflow definition. A changed
policy or playbook needs a new verdict.

Each workflow execution has a random run ID. Review resume and retry keep the
same ID. A separate run gets a different ID. Skein combines the run ID and
step path for action correlation and idempotency. The activity chain records
an attempt and its outcome, including `completion_unknown`.

Skein runs an action handler on a bounded worker. Calls through the supplied
`WorkItems` service run on the thread that owns the current core transaction.
Thus, a reviewed task command uses the same transaction as policy refresh and
the reviewer verdict. The worker never owns that database connection.
This reviewed local-write behavior requires core `0.2.1` or later. Set
`minimum_core` to `0.2.1` when a workflow action uses `WorkItems` after review.
An external-only action can retain a `0.2.0` floor.

At the deadline, Skein closes the action's public work service before it
returns `completion_unknown`. A late handler can finish external work, but a
late core work call returns `EXECUTION_CONTEXT_CLOSED`. A core command that
completed before the deadline stays part of the reviewer transaction. Make
all write actions idempotent because the final external result can be unknown.
Each owner-bound `WorkItems` command has its own rollback boundary. If a local
command writes and then fails, Skein removes that command's rows and deferred
callbacks. The reviewed action remains `completion_unknown` because an
external effect can already exist. Skein stores the human verdict as approved
and stores the invocation outcome separately as `completion_unknown`. The
Approvals history keeps the warning. Do not submit or run the action again.

Version 1 does not support timers, parallel branches, or a general workflow
state machine. Keep these processes in an extension service.

Start a workflow-backed playbook through the REST API. This path receives the
composed workflow action registry. The deterministic `/plan` command and the
stock agent playbook tool support static templates only in version 1. They
return an error if the selected playbook contains workflow actions.

## Validate content overlays

Playbooks, personas, and flocks accept `schema_version: 1`. A file that declares
this version uses the strict closed schema. Existing unversioned content keeps
the legacy open-field behavior and is normalized to version 1 when it loads.
Add `schema_version` after deployment validation removes unsupported fields.

Validate private overlays in deployment CI:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.content \
  --playbooks workplace/content/playbooks \
  --personas workplace/content/personas \
  --flocks workplace/content/flocks \
  --workflow-action workplace.notify-manager
```

Repeat `--workflow-action` for each action that the private module registers.
The same action check runs during application startup.

Configuration is sufficient for static templates, prompts, and flock groups.
Use a typed workflow action when a process needs an external call or custom
validation. Use policy for an approval decision. Use a frontend extension when
a person needs a new interactive surface.

## Compose the frontend

Publish JavaScript and TypeScript declarations from the private frontend
package. Do not publish raw TSX as the package entry point.

The package exports one `FrontendExtension`:

```typescript
import {
  FRONTEND_EXTENSION_API,
  type FrontendExtension,
} from "@miloctl/skein-extension-api";

const extension: FrontendExtension = {
  id: "atlas.workplace",
  version: "2.0.0",
  extensionApi: FRONTEND_EXTENSION_API,
  minimumCore: "0.3.0",
  maximumCoreExclusive: "0.6.0",
  navigation: [],
  dashboardCards: [],
};

export default extension;
```

Install the versioned host, extension API, and private extension in one
workplace npm project. The workplace project owns its lock.

Compile the private extension. Then run the host command with the explicit
package allowlist:

```sh
npm run build --workspace @atlas/skein-extension
skein-frontend-build @atlas/skein-extension
```

The command stages the installed host and runs `next build`. It writes a
standalone application to `dist/frontend`.

The command does not install packages. It does not change `package.json`,
`package-lock.json`, or `node_modules`.

The generator creates static imports and a Tailwind `@source` entry for each
allowlisted package, so the production CSS contains the utilities that only
the extension uses. It does not scan packages outside the allowlist.

The manifest fields are validated at build time. TypeScript declarations do
not validate an installed JavaScript package, so the registry checks each
field and names the extension when one is invalid:

| Field | Rule |
|---|---|
| `id` | Lowercase dotted identifier. Unique across composed extensions. |
| `version` | Three-part numeric version of the private package. |
| `extensionApi` | Must equal the host's frontend extension API (`1.0`). |
| `minimumCore` | Lowest compatible core version, inclusive. |
| `maximumCoreExclusive` | First incompatible core version. |
| `navigation[].id` | Identifier under the extension namespace. Unique. |
| `navigation[].label` | Non-empty string. |
| `navigation[].href` | Application-relative path (`/...`, never `//`). |
| `navigation[].activePaths` | Array of application-relative paths. |
| `navigation[].policyAction` | Optional string, at most 160 characters. |
| `dashboardCards[].id` | Identifier under the extension namespace. Unique. |
| `dashboardCards[].slot` | Exactly `manager-dashboard`. |
| `dashboardCards[].component` | A React component function. |
| `dashboardCards[].policyAction` | Optional string, at most 160 characters. |

A composed registry can declare at most 64 policy actions. This is the same
bound the backend applies to one capability request.

A card or navigation item that declares a policy action is hidden unless
`/api/capabilities` returns `permit`. The backend refuses an action that no
composed module registers, outside the reserved `skein.` core namespace —
and a private module cannot declare an operation under that namespace, and
a frontend `policyAction` cannot name it. A frontend package whose backend
module is not installed therefore stays hidden instead of rendering
against a missing API. An
identity or credential change clears the old decision and loads current
capabilities. A contribution with no policy action always renders.

Only import the components that `@miloctl/skein-extension-api` exports. A private
package that imports `frontend/components` or `frontend/lib` uses an internal
contract and can break on any release.

## Package and deploy

The Skein wheel is a PEP 561 typed package. It includes `app/py.typed`.
Type-check the private backend against the installed Skein wheel, not against a
core source checkout. This check detects removed names and incompatible type
changes at the same boundary that deployment uses.

Build the core wheel with:

```sh
uv build --wheel --out-dir dist backend
```

Point `--out-dir` outside `backend/`. A build that writes inside `backend/`
leaves a `build/` directory in the source tree.

Skein reads its configuration once, when `app.config` imports. Set every
variable before the process imports `app`. A value set later has no effect.
An installed deployment sets at least:

- Set `SKEIN_DATABASE_URL`, or set the database components:
  `SKEIN_DB_HOST`, `SKEIN_DB_NAME`, `SKEIN_DB_USER`, and
  `SKEIN_DB_PASSWORD`. `SKEIN_DB_PORT` is optional. Use component variables
  in a deployment manifest because a password can contain URI punctuation.
- `SKEIN_DATA_DIR` holds artifacts, backups, and exports — no database.
  Always set it for an installed package. The default resolves inside the
  installed package directory.
- `SKEIN_MODEL_PROVIDER` selects the model provider. The keyless `mock`
  provider is the default.
- `SKEIN_AUTH_MODE` selects `api-key` (the default), `trusted-header` (the
  `X-User` header supplies a weak name), or `oidc`. Trusted-header mode supplies
  no enterprise groups. Use signed OIDC groups to test group-gated contributions.
- `SKEIN_SCHEDULER=0` disables the background scheduler. Extension tests
  use this.
- `SKEIN_PLAYBOOKS_DIR`, `SKEIN_PERSONAS_DIR`, and `SKEIN_FLOCKS_DIR` mount
  deployment content overlays.

The protected GitHub `main` workflow publishes these packages when `.github/release-version` changes:

- `skein-agents` to public PyPI through Trusted Publishing.
- `@miloctl/skein-extension-api` to private GitHub Packages.
- `@miloctl/skein-frontend-host` to private GitHub Packages.

This revision declares package line `0.5.0`. Registry pull-back and annotated tag `v0.5.0` are the authority for completed publication.

The command that follows installs the contract for this revision. Run it only when the registry package and matching tag exist. It does not install later working-tree changes.

Install `skein-agents` from PyPI or a controlled mirror:

```sh
pip install skein-agents==0.5.0 \
  --index-url https://pypi.org/simple
```

A hash-verified wheelhouse with `--no-index` is also permitted. The production lock excludes `skein-agents` and the private workplace package.

The image installs both exact first-party wheels with `--no-deps` after the locked public closure.

Keep test tools in a separate hash-locked `requirements-test.lock`. Install this lock before the same first-party wheels in a test environment.

The `@miloctl` npm packages are public on npmjs.com and install with no token. A controlled npm mirror serves them like any other package.

Use Node 22. Pin the frontend host, its peers, and Next directly in the workplace root:

```json
{
  "engines": {
    "node": "22.x"
  },
  "dependencies": {
    "@miloctl/skein-extension-api": "1.0.0",
    "@miloctl/skein-frontend-host": "0.5.0",
    "next": "16.2.11",
    "react": "19.2.4",
    "react-dom": "19.2.4"
  },
  "overrides": {
    "postcss": "8.5.23",
    "sharp": "0.35.3"
  }
}
```

An installed package cannot apply its overrides to the workplace root. `skein-frontend-build` refuses missing or different pins.

The workplace repository owns these release inputs:

- The private Python package.
- The private frontend package.
- The combined Python production lock.
- The combined Python test lock.
- The npm lock.
- Content overlays.
- Final runtime images and deployment files.

The workplace backend image installs the locked dependency closure. It then
installs the Skein and workplace wheels with `--no-deps`.

The workplace frontend image runs `npm ci` and `skein-frontend-build`. It
copies only the completed standalone output into its runtime stage.

Kustomize or Helm overlays are suitable for environment values, image tags,
volumes, and Secret references. They are not suitable for changing application
logic. Sidecars and separate services are safer for untrusted or separately
operated integrations. In-process modules are trusted code with the same
operating-system permissions as Skein.

Keep credentials in the deployment secret manager. Pass only references in
Git. Use separate service identities and least-privilege credentials for jobs
and integrations. Apply network policy outside the Python plugin boundary.

Keep every file that a Kustomize generator reads below the overlay root. The
reference overlay renders with the standard command:

```sh
kubectl kustomize examples/workplace-extension
```

Do not set a fixed `runAsUser` or `fsGroup` in an OpenShift deployment.
The `restricted-v2` profile assigns values from the namespace range.

## Contract reference

The dataclasses that `app.extensions` and `app.public` export are the
authoritative field reference. The wheel is typed (PEP 561), so an editor
shows every field, type, and default on the installed package. Two shared
vocabularies apply across contributions:

- `effect` is one of `none`, `read`, `write`, or `unknown`. An unknown
  effect fails closed.
- `risk` is one of `low`, `medium`, `high`, or `critical`.

## Compatibility and deprecation

Every module and frontend manifest declares these values:

- Its own version
- The extension API version
- The minimum supported core version
- The exclusive maximum core version

Skein rejects an incompatible module during startup or build. The first
contract line is `1.0`. Additive fields can ship within this line. A breaking
contract requires a new extension API version and a compatibility adapter when
practical.

Keep a deprecated contract for at least one released compatibility range.
Document its replacement and removal release. Do not change event data or
error meanings within one schema version.
Rehearse upgrades with two distinct core revisions. Do not change only the
version string on one source tree. Keep the private extension artifact
unchanged during the rehearsal.

## Required tests

A private extension repository must run these checks:

- Import-boundary tests that reject core internal imports
- Module collision and compatibility tests
- Policy tests for permit, deny, and review
- Route authorization tests against the composed application
- Tool gating, schema, timeout, and receipt tests
- Event retry and idempotency tests
- Fresh and upgraded extension migration tests
- Content validation
- Frontend registry tests and a production build
- A signed browser test of denied, integration, manager, and core-write paths
- An artifact-level test against the lowest and highest compatible core release

The public packages export the surfaces these tests need:

- `app.extensions.assert_import_boundary` raises when a package imports a
  Skein module outside `app.extensions`, `app.public`, and
  `app.main.create_app`. Pass it
  the imported package, its dotted name, or a path. A submodule of a public
  package is internal too: the export list is the contract. The check reads
  source and is not a security boundary, because a dynamic import evades it.
  This surface requires core `0.2.2` or later.
- `app.extensions.registry_for(app)` returns the composed registry of one
  application, with its contributions and `policy_engine`.
- `app.extensions.execute_tool` runs one governed tool call. It is a
  coroutine, so a synchronous test awaits it with `asyncio.run`. Build its
  `ToolCallContext` from `registry_for(app).service_subject(...)` or a
  `PolicySubject`, and inspect the returned `ToolExecution`.
- `app.public.dispatch_events` delivers pending outbox events to the
  contributions you pass it, and returns the delivery counts.

Start the composed application with `TestClient` before a test touches the
database. The application lifespan applies core and extension migrations.
The reference tests in `examples/workplace-extension/backend/tests/` use
each of these surfaces and cover every required category.

Run Skein's local reference rehearsal with:

```sh
SKEIN_CONTRACT_RUN_ID=local \
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  scripts/reference-extension-contract.sh
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  scripts/reference-frontend-contract.sh
scripts/reference-deployment-contract.sh
scripts/reference-images-contract.sh
```

The two package scripts pin their configuration before they build. The caller
sets `SKEIN_DATABASE_URL`. The backend script also requires a safe
`SKEIN_CONTRACT_RUN_ID`.

The script builds and installs separate wheels in a normal virtual environment.
It starts Skein 0.2.3 with Atlas 1.x and writes upgrade data.

It removes both old distributions. Then it installs `skein-agents` 0.5.0 and
Atlas 2.0 against the same database. This current-source wheel is a local test
artifact and can differ from the registry wheel with the same version. Do not
publish or distribute it.

The script runs a real Atlas synchronization on both package generations. It
uses strict mypy against the installed current public contracts.

The script compares fresh and upgraded core and extension schemas. It also
keeps a data marker through the package transition.
`scripts/upgrade-path.sh` separately validates all additive core migrations,
schema equality, and activity-chain integrity.

The frontend script packs the host and API. It installs them through the Atlas
lock in a clean directory and starts the standalone server. It also starts the
installed Atlas backend and a signed test identity provider. A browser exercises
the denied, integration, manager, and core-write paths.

The image script builds both final Atlas images from the package artifacts.
The main CI workflow runs all four extension contracts.

## Upgrade a workplace deployment

1. Read the Skein release notes and deprecations.
2. Change the pinned core wheel, image, and frontend build input.
3. Run the backend and frontend compatibility checks.
4. Validate all private content.
5. Run core and extension migrations in a disposable copy of production data.
6. Run contract, policy, provenance, and activity-chain tests.
7. Build the derivative images.
8. Deploy to a test environment and run smoke tests.
9. Deploy the unchanged private package with the new compatible core release.

If a compatibility check fails, update the private package to the documented
public contract. Do not copy the new core source into the workplace repository.
