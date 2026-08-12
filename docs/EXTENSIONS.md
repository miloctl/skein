# Skein extension authoring

Skein supports trusted workplace extensions without a source-tree fork. A
workplace can keep its Python package, frontend package, content, policies,
data, and deployment files in a private repository.

The extension APIs are narrow by design. Skein does not scan installed
packages. It does not run arbitrary browser code at runtime. The deployment
must explicitly select each trusted module.

The fictional [Atlas example](../examples/workplace-extension/README.md)
uses every supported contract.

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
and components only from `@skein/extension-api`.

| Concern | Contract | Composition time |
|---|---|---|
| Routes | `RouteContribution` | Application creation |
| Scheduled jobs | `JobContribution` | Application creation |
| Startup and shutdown | `LifecycleContribution` | Application lifespan |
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
    version="1.0.0",
    extension_api="1.0",
    minimum_core="0.2.0",
    maximum_core_exclusive="0.3.0",
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

A lifecycle handler receives the public `LifecycleContext`. It contains the
core version only. Skein stops handlers in reverse order. If one shutdown
handler fails, Skein logs the failure and continues the remaining cleanup.

Module IDs and contribution names must use the module namespace. Extension
routers must start with `/api/extensions/{module_id}`. Startup rejects these
conditions:

- Duplicate module or contribution names
- Unsupported extension API or core versions
- Missing module dependencies
- Dependency cycles
- Route or tool name collisions
- A route without an exact operation policy contract
- Invalid tool, event, migration, or workflow metadata

The module list is an allowlist. Import and instantiate modules in the private
composition root. Do not load all Python entry points automatically.

A lifecycle handler receives only the installed core version. It does not
receive the FastAPI application, raw settings, or secrets. Use a route, job,
event subscriber, or external service for work that needs those boundaries.

## Protect routes and apply policy

The backend is the enforcement point. Frontend capability checks only control
presentation.

Use `ExtensionRouteServicesDep` on an extension route. It supplies the mapped
subject, the composed policy engine, and the public work facade. Do not read
private values from `request.app.state`.

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
`review`. A deny returns `POLICY_DENIED`. Direct routes cannot resume a review
safely. A review on a direct route returns `POLICY_REVIEW_UNSUPPORTED` and does
not run the operation. Use a governed tool or workflow when the operation
needs durable human review.

The existing strong-identity, administrator, visibility, authority, and review
checks still run. A workplace permit cannot remove a core denial. Deny is
stronger than review, and review is stronger than permit.

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
Scoped engagement packs, briefs, and health reports remove legacy tasks whose
direct engagement and milestone parent disagree.

Skein resolves linked project context before it applies policy. Hidden,
missing, or conflicting legacy parents fail closed without exposing the
parent ID or project class.

Local domain writes keep context resolution, policy, and mutation in one
SQLite write transaction. This rule covers REST, stock tools, MCP, public
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

Exactly one identity contribution can own group refresh. Its resolver must
return a `groups` key. An empty tuple is a successful group refresh. Set
`resolves_groups=False` on profile-only resolvers. A profile resolver cannot
return groups or replace an unavailable group resolver. These rules prevent
stale approval groups when a workplace uses more than one identity module.
Extension API 1.0 packages that omit `resolves_groups` keep the original
resolver inference. All legacy resolvers must be available. Their group
results must be identical. New packages must declare one group owner with
`resolves_groups=True`.

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
trusted-header reads do not create roster rows and do not receive strong or
private-data authority.
Established OIDC users use a read-only ownership check. A first ownership
claim can require the SQLite writer lock. Skein returns a retryable `503` if
that lock is busy.
Restart Skein after you change persona or flock overlays so startup can
validate the complete identity roster.

An older database can contain two names that differ only by case or Unicode
form. Skein reports this conflict on `/health`. It refuses both identities
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

`WorkItems` is the first public command and query facade. It validates policy
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

Extensions receive typed views. They do not receive SQLite rows or a core
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
- Receipt and provenance behavior

Unknown effects fail closed. A review decision creates a durable proposal.
Skein stores the executable arguments outside the review queue. A qualified
human can approve the proposal and run the exact saved call. Before each
verdict, Skein runs the registered resource resolver again. Current target
classification controls both approval and rejection.
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

Reviewed tools store their exact input in the core review database. Do not put
credentials or unneeded sensitive content in tool arguments. Apply the
workplace backup and retention policy to this database.

A timed-out synchronous write has the status `completion_unknown`. A worker
thread can finish after the deadline. Make write handlers idempotent. Use the
supplied stable identifiers.

A specialist contribution contains its prompt, context sources, tools, and
required capabilities. The Chief of Staff reads the registry. A private
package does not import or patch the Chief implementation.

Use a registered tool for side effects. A context contribution declares a
version, read policy action, risk, capabilities, deadline, and output limit.
Skein records a content-free receipt for each retrieval. The provider must be
synchronous and must not write. Its single string argument is the authenticated
requester name. It is not the current question. Use a governed read tool when
retrieval needs structured input, review, or a custom resource resolver.

## Subscribe to events

All shared task writes create version 1 domain events in the SQLite outbox.
Each event has an ID, type, schema version, time, actor, origin, resource
reference, safe change summary, visibility, and correlation data.

Subscribers select event types, schema versions, and visibility tiers. The
dispatcher retries failures. It records one delivery receipt for each event
and subscriber. A subscriber must also use the event ID as the idempotency key
for its external side effect.

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

Use `ExtensionStore` for a small extension-owned SQLite database. The store
refuses both core database paths. Its migration stream is namespaced and
independent from core migration numbers.

Use namespaced metadata only for sparse annotations with simple validation.
Use extension-owned tables or an external store for indexed, relational, or
invariant-rich data. Do not create a general entity-attribute-value store.

Store stable Skein identifiers as external references. Do not create a foreign
key into the core SQLite database. The public API, events, and commands define
the consistency boundary.

Keep each migration version and name append-only. Add a new version for every
change. Test a fresh database and an upgrade from the previous extension
release.

## Add workflow behavior

Version 1 playbooks support four workflow step types:

- `condition` compares one context value and selects one branch.
- `approval` asks the policy engine for permit, deny, or review.
- `action` calls one registered typed workflow action.
- `checkpoint` records a named completed point.

Workflow actions declare schemas, effect, risk, policy action, timeout, and
safe error codes. A playbook cannot call arbitrary Python or an arbitrary URL.

Extension API 1.0 still exports `WorkflowEngine` and `WorkflowContext` for
source compatibility with early packages. Do not use them as an execution
entry point. The composed application issues the workflow authority. A
caller-created context cannot run an action and returns
`WORKFLOW_CONTEXT_REQUIRED`. Start a workflow-backed playbook through the REST
endpoint. Resume it through the review endpoint. This rule binds the requester,
policy, run ID, and action registry to one trusted application boundary.

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
Digest values include an algorithm prefix. The version 2 reader also accepts
the untagged digest from the previous compatible release when the content is
unchanged. An agent-origin proposal with no saved policy binding cannot be
approved. A qualified reviewer can reject it.

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
} from "@skein/extension-api";

const extension: FrontendExtension = {
  id: "atlas.workplace",
  version: "1.0.0",
  extensionApi: FRONTEND_EXTENSION_API,
  minimumCore: "0.2.0",
  maximumCoreExclusive: "0.3.0",
  navigation: [],
  dashboardCards: [],
};

export default extension;
```

Set the package allowlist during the frontend build:

```sh
SKEIN_FRONTEND_EXTENSIONS=@atlas/skein-extension npm run build
```

Create a versioned host artifact when the private repository cannot use the
core source tree:

```sh
scripts/package-frontend-host.sh 0.2.0 dist/frontend-host
```

The archive contains the trusted build host and a manifest with the core and
frontend API versions. The `host` stage in `frontend/Dockerfile` provides the
same boundary for derivative container builds. Install the private packed
package in that stage. Then compose the manifest and run `next build`.

The generator creates static imports. Registry validation rejects duplicate
IDs, invalid namespaces, unsupported API versions, and incompatible core
versions. A card or navigation item can declare a policy action. Skein hides
it unless `/api/capabilities` returns `permit`. An identity or credential
change clears the old decision and loads current capabilities.

Only import the components that `@skein/extension-api` exports. A private
package that imports `frontend/components` or `frontend/lib` uses an internal
contract and can break on any release.

## Package and deploy

The Skein wheel is a PEP 561 typed package. It includes `app/py.typed`.
Type-check the private backend against the installed Skein wheel, not against a
core source checkout. This check detects removed names and incompatible type
changes at the same boundary that deployment uses.

Use separate versioned artifacts:

- A Skein Python wheel or backend image
- A private workplace Python wheel
- A packed private frontend package
- A versioned frontend host archive or compatible `skein-frontend-host` image
- A deployment overlay with secret references

Container layering is suitable for the backend. A derivative backend image
can install the private wheel on top of a released Skein image. Frontend code
must compose before `next build`. Use the versioned host archive or container
stage as the build input. Copying edited core frontend files into an image is
a hidden fork.

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

Set a pod file-system group for persistent volumes that the non-root process
writes. The reference deployment uses `fsGroup: 1000` and tests both core and
extension data paths in the derivative backend image.

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
- An artifact-level test against the lowest and highest compatible core release

Run Skein's local reference rehearsal with:

```sh
scripts/reference-extension-contract.sh
scripts/reference-frontend-contract.sh
scripts/reference-deployment-contract.sh
scripts/reference-images-contract.sh
```

The backend script builds and installs separate wheels in a normal virtual
environment. It starts the installed application. It then moves the unchanged
private package from core `0.2.0` to a compatible `0.2.1` artifact. That
pair uses different backend source trees. It applies migration 018 and runs
the documented legacy identity-owner claims before startup.
`scripts/upgrade-path.sh` separately verifies all additive core migrations
from the historical base, schema equality, and activity-chain integrity. The
frontend script creates two host artifacts from distinct source
trees. It installs the same packed private package into both hosts. Then it
runs a production build in each host. A runtime test renders the packed Atlas
card through the generated registry.
The image script builds the backend
and frontend derivative images from staged release artifacts. The main CI
workflow runs all four extension contracts.

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
