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
    minimum_core="0.1.0",
    maximum_core_exclusive="0.2.0",
    routes=(RouteContribution("atlas.workplace.routes", router),),
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
- Missing module dependencies
- Dependency cycles
- Route or tool name collisions
- Invalid tool, event, migration, or workflow metadata

The module list is an allowlist. Import and instantiate modules in the private
composition root. Do not load all Python entry points automatically.

## Protect routes and apply policy

The backend is the enforcement point. Frontend capability checks only control
presentation.

Use `PolicySubjectDep` to get mapped roles, groups, capabilities, and other
identity attributes. Use the application policy engine before an extension
route performs work. Return `PublicError` codes that clients can handle.

```python
from fastapi import APIRouter, Request

from app.extensions import PolicyEffect, PolicySubjectDep, decide
from app.public import PublicError

router = APIRouter(prefix="/api/extensions/atlas.workplace")

@router.post("/sync")
def sync(request: Request, subject: PolicySubjectDep):
    result = decide(request, subject, "atlas.integration.sync", "atlas")
    if result.effect != PolicyEffect.PERMIT:
        raise PublicError("POLICY_DENIED", "The policy denied this action.", status_code=403)
    return run_sync()
```

Skein applies the same composed policy to authenticated core REST mutations,
agent tools, classified MCP tools, workflow steps, and capability responses.
Core REST actions use this stable form:

```text
skein.rest.<method>.<literal-path-segments>
```

For example, `PATCH /api/tasks/{task_id}` is
`skein.rest.patch.tasks`. A policy rule can return `permit`, `deny`, or
`review`. A deny returns `POLICY_DENIED`. A review returns
`POLICY_REVIEW_REQUIRED` and does not run the mutation.

The existing strong-identity, administrator, visibility, authority, and review
checks still run. A workplace permit cannot remove a core denial. Deny is
stronger than review, and review is stronger than permit.

Auth bootstrap endpoints and signed webhooks keep their specialized gates.
They do not use a human REST policy action.

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

## Use public work commands

`WorkItems` is the first public command and query facade. It validates policy
and uses the existing service write path. That shared path preserves
provenance and writes a versioned outbox event in the same transaction for
human, agent, and integration callers.

```python
from app.extensions import PolicyEngine, PolicySubject
from app.public import CommandContext, CreateTaskCommand, WorkItems

work = WorkItems(PolicyEngine())
task = work.create(
    CreateTaskCommand(title="Map an external work item"),
    CommandContext(
        subject=PolicySubject("atlas-service", kind="service"),
        actor="atlas-service",
        origin="atlas-integration",
    ),
)
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

Unknown effects fail closed. A write-capable MCP tool needs the same metadata.
Skein omits unclassified MCP tools from the agent.

A specialist contribution contains its prompt, context sources, tools, and
required capabilities. The Chief of Staff reads the registry. A private
package does not import or patch the Chief implementation.

Use a registered tool for side effects. A context provider must return text
and must not write.

## Subscribe to events

All shared task writes create version 1 domain events in the SQLite outbox.
Each event has an ID, type, schema version, time, actor, origin, resource
reference, safe change summary, visibility, and correlation data.

Subscribers select event types, schema versions, and visibility tiers. The
dispatcher retries failures. It records one delivery receipt for each event
and subscriber. A subscriber must also use the event ID as the idempotency key
for its external side effect.

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

The version 1 engine stops at a review boundary. It does not persist and resume
a general workflow run. Keep long-lived orchestration in an extension service
until a real process requires a durable core workflow state machine.

## Validate content overlays

Playbooks, personas, and flocks accept `schema_version: 1`. Existing content
without a version is treated as version 1.

Validate private overlays in deployment CI:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.content \
  --playbooks workplace/content/playbooks \
  --personas workplace/content/personas \
  --flocks workplace/content/flocks
```

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
  minimumCore: "0.1.0",
  maximumCoreExclusive: "0.2.0",
  navigation: [],
  dashboardCards: [],
};

export default extension;
```

Set the package allowlist during the frontend build:

```sh
SKEIN_FRONTEND_EXTENSIONS=@atlas/skein-extension npm run build
```

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
- A frontend build based on a compatible Skein source or build image
- A deployment overlay with secret references

Container layering is suitable for the backend. A derivative backend image
can install the private wheel on top of a released Skein image. Frontend code
must compose before `next build`, so a source or build-stage artifact is
required. Copying edited core frontend files into an image is a hidden fork.

Kustomize or Helm overlays are suitable for environment values, image tags,
volumes, and Secret references. They are not suitable for changing application
logic. Sidecars and separate services are safer for untrusted or separately
operated integrations. In-process modules are trusted code with the same
operating-system permissions as Skein.

Keep credentials in the deployment secret manager. Pass only references in
Git. Use separate service identities and least-privilege credentials for jobs
and integrations. Apply network policy outside the Python plugin boundary.

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
```

The script builds and installs separate wheels. It proves that the extension
depends on released artifacts instead of source patches.

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
