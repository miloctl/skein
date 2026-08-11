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
from app.extensions import RouteContribution, SkeinModule
from app.main import create_app

from atlas.router import router

atlas = SkeinModule(
    module_id="atlas.workplace",
    version="1.0.0",
    extension_api="1.0",
    minimum_core="0.2.0",
    maximum_core_exclusive="0.3.0",
    routes=(RouteContribution("atlas.workplace.routes", router),),
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
    PolicyInput,
    PolicyResource,
    enforce_decision,
)

router = APIRouter(prefix="/api/extensions/atlas.workplace")

@router.post("/sync")
def sync(services: ExtensionRouteServicesDep):
    decision = services.policy.decide(
        PolicyInput(
            services.subject,
            "atlas.integration.sync",
            PolicyResource("atlas"),
            "human",
        )
    )
    enforce_decision(decision)
    return run_sync(services.work_items, services.subject)
```

Skein applies the composed policy to all namespaced extension operations. This
check uses the stable REST action for the route. An extension route must also
check its domain action before it starts work. The Atlas example checks
`atlas.integration.sync` in addition to the route action.

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

A resolver that owns groups must return a `groups` key. An empty tuple is a
successful group refresh. A profile-only resolver cannot replace an
unavailable group resolver. This rule prevents stale approval groups when a
workplace uses more than one identity module.

Register every job and event subject with `ServiceIdentityContribution`.
Service identities do not pass through the human identity mapper. Startup
reserves their names and rejects a collision with a human account.

## Use public work commands

`WorkItems` is the first public command and query facade. It validates policy
and uses the existing service write path. That shared path preserves
provenance and writes a versioned outbox event in the same transaction for
human, agent, and integration callers.

```python
from app.extensions import JobExecutionContext, PolicySubject
from app.public import CommandContext, CreateTaskCommand

def import_work(context: JobExecutionContext):
    task = context.work_items.create_task(
        CreateTaskCommand(
            title="Map an external work item",
            idempotency_key="atlas-item:ATLAS-7",
        ),
        CommandContext(
            subject=PolicySubject("atlas-service", kind="service"),
            origin="atlas-integration",
        ),
    )
    return {"created": 1, "task_id": task.id}
```

Extensions receive typed views. They do not receive SQLite rows or a core
connection. Propose a new public command when an extension needs a stable core
operation that is not available. Do not make every internal service public.

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

`ToolHandlerContext.subject` is the human or service requester for policy.
`ToolHandlerContext.agent` is the specialist that executes the tool.
`ToolHandlerContext.correlation_id` links the tool receipt to outbox events.
The same correlation ID follows a reviewed call from its queue entry to its
final execution receipt.
If a tool writes through `WorkItems`, set `CommandContext.actor` to the agent
and set `actor_kind` to `agent`. Keep the requester as `subject`. This split
records the agent as the writer without giving it the requester's roles.

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

Use a registered tool for side effects. A context provider must return
deterministic, non-sensitive text and must not write. Put sensitive retrieval
behind a governed read tool. This rule keeps policy and audit controls on the
retrieval boundary.

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
unsupported for a scheduled job.

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

The REST, deterministic chat, and agent-tool paths use `playbook.create`.
They use one resolver to load the project class from the selected playbook.
The REST path
stores a workplace-policy review before it creates project work. A qualified
human can approve it. Skein then runs the exact saved playbook request and
records the result. A workflow approval step can create a second proposal.

Each durable playbook review stores a canonical content digest. Skein compares
that digest before approval or rejection. A changed overlay needs a new
review. No changed task, action, or project class can use the old verdict.

Skein evaluates all selected workflow steps before it creates core work. It
binds each successful decision to that immediate execution. The runner cannot
observe a second policy result after core work exists.

Each approval grant covers one structural step path. It also covers one input,
action version, policy result, and complete workflow definition. A changed
policy or playbook needs a new verdict.

Version 1 does not support timers, parallel branches, or a general workflow
state machine. Keep these processes in an extension service.

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
it unless `/api/capabilities` returns `permit`.

Only import the components that `@skein/extension-api` exports. A private
package that imports `frontend/components` or `frontend/lib` uses an internal
contract and can break on any release.

## Package and deploy

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
artifact contains an additive migration. The frontend script creates two
compatible host artifacts. It installs the same packed private package into
both hosts and runs a production build in each one. The image script builds
the backend and frontend derivative images from staged release artifacts.
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
