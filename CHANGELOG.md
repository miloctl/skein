# Changelog

Step 1 of the upgrade procedure in `docs/EXTENSIONS.md` is to read the release
notes and deprecations. This file is those notes.

Each release records three kinds of change. **Contracts** covers anything a
private extension package imports or declares: `app.extensions`, `app.public`,
`@miloctl/skein-extension-api`, the content schemas, and the compatibility fields. A
package author reads this section and nothing else to plan an upgrade.
**Behavior** covers what the running system does differently. **Operations**
covers what whoever runs the server must do differently.

A contract entry names the version a package must declare to use it. Additive
contracts keep extension API 1.0: a package that does not use the new contract
keeps its existing `minimum_core` and needs no change.

## Unreleased

### Contracts

### Behavior

### Operations

## 0.6.4 — 2026-09-20

### Contracts

- `GET /api/delta` reports seven team calendar dates and carries `window_start`, `window_end`, `snapshot_id`, `review_revision`, `reviewed`, and `truncated`. Finding items carry `rule_id` and `severity`, and a new finding reports `direction` as `new` rather than `worse`. The reader's last-seen mark no longer decides the window, and a `mark=true` query writes nothing.
- `POST /api/delta/ack` records that one reader reviewed one summary. Its body takes `snapshot_id` and `review_revision` and refuses every other field. A summary that changed since the preview, or a revision another tab already spent, answers 409. An incomplete summary answers 400, because a refresh returns the same incomplete summary. The route stores a fingerprint and a revision, never a source timestamp, so a review cannot consume a change the reader never saw.
- A contributed `navigation` item renders in the workspace sidebar and in its narrow-screen drawer, with the declared query and fragment unchanged. Extension API 1.0 does not change. An item that declares no icon takes a neutral one.

### Behavior

- My Day's "Since you last looked" becomes Recent changes: one summary over seven team calendar dates, five headlines at the front, and low-severity feature-adoption findings grouped last. A finding reads as new rather than as a health call that worsened. **Mark this summary reviewed** records the summary on screen, not a point in time, so a summary that changes afterwards returns, and rows already reviewed can return with it. Opening the summary, expanding it, or reading its evidence records nothing.
- One workspace sidebar replaces the top navigation row and the per-page section tabs. It holds the five destinations and the open group's pages, at 240px from 1024px wide, at 56px when collapsed, and in a labelled drawer below that width. Search, page help, and Capture stay in the utility bar. Settings, the field guide, and the identity menu sit in the sidebar footer.
- My Day leads with Needs you and Your work. The standup composer sits in a closed disclosure below the task list, and the shared queues stay behind Show team context for every reader.
- Browse shows one register at a time, chosen from a grouped selector at every width. Hidden registers stay mounted, so a half-typed form survives a switch. The selector rewrites the address fragment in place: it adds no history entry and does not move focus, because a closed selector reports a change on every arrow key.
- Planning owns the weekly draft and the commitment. Health leads with engagement condition and risk and links to Planning for the plan. The specialist bench, the chat welcome, Approvals, and the task panel keep their main action and their evidence in view and fold reference material into disclosures.
- The page help control reads **Help** at every width.

### Operations

- The `delta_seen:<user>` rows in `app_settings` are no longer read. Nothing removes them. They are safe to leave in place or to delete.
- A browser still running the previous bundle asks for `GET /api/delta?mark=true`, which now writes nothing. That reader sees the same summary until the new bundle loads.

## 0.6.3 — 2026-09-18

### Contracts

- `WorkItems.get_blocker` and `update_blocker` read the blocker through the viewer the composition boundary granted, as the task and promise reads already did. A blocker outside that viewer's scope answers `BLOCKER_NOT_FOUND` with the same sentence the other reads use, no longer the row.

### Behavior

- A rename that merges into an existing account is refused when the account that moves is the caller's own. The merge carried the caller's API keys onto the target row, so any keyholder could become a colleague and read their private journal. A teammate can still run the merge.
- Memory recall with no person named returns the team-wide memories only. An unattended turn and a tool call with no requester read every person's targeted memories before.
- Deactivating an agent settles its queued wake as `refused` with reason `agent_unavailable`, the unattended runner refuses a deactivated identity, and a delegation to a deactivated agent is refused with the instruction to reactivate it first.
- A capture whose text carries a command-wrapped feedback line (`/x fb:`) is refused by the service, the same shape the capture route already skips policy for. A multi-line capture with such a line skipped both the policy check and the feedback branch and was captured as a task.

### Operations

- The restore fence in `deploy/k8s/README.md` step 7 now removes the sealed bearer token and OAuth sign-in from every restored personal MCP server row. A token revoked or a server deleted after the backup came back usable before. Owners enter the token or sign in again.

## 0.6.2 — 2026-09-17

### Contracts

- Solo chat threads carry `model_id` (migration 031). The rows of `GET /api/chats` return it; empty means the team model. A second message on a thread whose turn is still running answers 503 with `Retry-After: 5` and the sentence "The model session is in use. Wait for the current turn to finish.", no longer the generic database-busy sentence.

### Behavior

- `/model` in a solo chat lists the model menu and names the model the chat runs on. `/model <id>` picks one for that chat, `/model default` returns it to the team model. The composer completes the id.
- The chat composer shows **Stop** while a turn runs. Stop aborts the stream and frees the thread for the next message. After 30 seconds without a reply the working indicator says so and points to Stop. A turn that ended without a reply shows the reason the server gave.

### Operations

- The OpenAI-compatible and Anthropic clients retry a failed request once, not twice. Each retry re-waits the full read timeout on a provider that accepts the request and never answers.

## 0.6.1 — 2026-09-17

### Contracts

- No change to extension API 1.0, `app.extensions`, `app.public`, or the compatibility declarations. A package that loads on core 0.6.0 loads on this release unchanged.

### Behavior

- In a private shared chat, an `@` followed by the first letters of an invited agent narrows the agent chips to the matches. Enter, Tab, or a click completes the picked one. A slug typed in full sends on the first Enter. A sent message shows at once as "Sending…" and the box empties before the server answers. If the send fails, the draft comes back with the error.
- While a shared-chat agent answers, the room shows the reply streamed so far under the agent's status line, refreshed by the 2-second poll. The stored message stays the only durable reply.
- In solo chat, a specialist's nameplate joins the first word of the reply, so the working indicator stays visible through the model wait.
- A stale browser session cookie reaches the sign-in gate once. The nav no longer polls attention while the session is locked, which removed a sign-out loop.

### Operations

- `SKEIN_OIDC_GROUPS_SOURCE=userinfo` reads the groups claim from the issuer's userinfo endpoint once per access token, with the `sub` checked against the token. `SKEIN_OIDC_USERINFO_URL` overrides the discovery document. The default `token` reads the access token as before.
- Migration 030 adds `chat_agent_runs.partial_text`. It applies at startup.
- The solo-chat stream answers with `Cache-Control: no-cache` and `X-Accel-Buffering: no`, so a buffering edge delivers frames as they are sent.

## 0.6.0 — 2026-09-08

### Contracts

- Core 0.6.0 reaches the previous compatibility ceiling. To load on this core, a package with `maximum_core_exclusive = "0.6.0"` or `maximumCoreExclusive: "0.6.0"` must advance that ceiling to `"0.7.0"`, and a pip bound of `<0.6.0` must widen to `<0.7.0`. Run the extension contracts before changing these declarations. Minimum-core floors and extension API 1.0 remain unchanged.
- Solo-chat history adds `GET /api/chats/{thread_id}/messages/page` with `before=<message id>` and `limit` from 1 to 200, default 50. It returns `{messages, next_before}`. The existing messages endpoint retains its newest-1000 bare array.
- `GET /api/tasks/browse` returns a compact task projection after scope and workplace-policy checks. Its fields are `id`, `title`, `status`, `priority`, `assignee`, `due_date`, `completed_at`, `forge_url`, `visibility`, and `crew_id`. Read `/api/tasks/{id}` for full task details. Extension API 1.0 does not change.

- Browser sign-in now establishes an opaque server session. The browser token endpoint returns identity and CSRF metadata, not provider credentials, and refuses browser refresh tokens. Existing browser credentials are cleared on upgrade.
- Cookie-authenticated browser requests cannot mint permanent API keys. Explicit key entry exchanges the key once; direct bearer clients retain key creation.
- OIDC browser sign-in requires `SKEIN_CREDENTIAL_KEY` in the deployment Secret, HTTPS, and same-site frontend/API origins with explicit CORS. Browser-session rows are excluded from recovery archives.

- CI webhook writes require a personal API key or deployment sign-in. A shared token with a self-asserted name is refused.
- The roster omits other people's saved themes and internal identity ownership fields. Growth interests remain shared staffing context.

### Behavior

- My Day preserves keyboard focus after action refreshes, including when background refreshes overlap, without moving focus from another control.
- Keyless chat accepts attached-file turns without a server error. It states that files remain unread and processes only the person's message through the existing command and review gates. File content cannot become a command or capture request.
- Task Peek restores focus only after a panel has opened. Initial page load keeps the normal keyboard order and skip link. A history-backed close waits for URL synchronization before restoring focus, so native fragments stay in history without taking focus from the opener. Search remains the fallback when the opener is gone.
- Slack commands and outbound delivery are removed, along with the Tavily and Exa research tools. In-app notifications remain available at once. RSS and local helper tools remain supported.
- Signed Gitea replay checks use the repository namespace, native event type, and exact raw payload SHA-256, even under a new delivery UUID. A matching stored fingerprint prevents replay from overwriting a later human task edit. Suppressed new UUIDs receive permanent alias receipts with `task_id = NULL` and remain bound against conflicting bytes or event types, including unsupported events. Matching compatibility headers remain accepted. This conservative rule also suppresses genuinely new byte-identical occurrences. Changed bytes remain eligible for existing policy and task-state checks. Initial headerless requests and pre-028 receipts lack fingerprint history for new-UUID replay protection. This is scoped signed-byte replay protection, not an unconditional exactly-once guarantee.
- Readiness includes bounded database and application-pool checks. Liveness remains available during database loss or request-worker saturation. Transient database disconnects return safe JSON 503 responses with Retry-After at authentication and route boundaries. Callers must check a write's outcome before retrying it. Filesystem permission failures return safe server errors without exposing internal paths; policy refusals remain 403.

- Agent wakes, shared-chat turns, and scheduled firings use a unique token per execution claim. The heartbeat renews only registered live acquisitions before expiry. Execution-bound transactions fence local tool and session writes as well as completion. A stale worker cannot release a successor's claim. Unknown external outcomes still require reconciliation.
- Scheduled work holds job-wide exclusion separately from firing receipts. Only explicitly retry-safe jobs automatically release failed firings for retry. Other jobs preserve evidence of an uncertain outcome. The heartbeat starts before catch-up work. Cron keys progress across repeated daylight-saving hours, and acquisition failures do not abort startup. Failed advisory-lock cleanup discards the connection.
- MCP OAuth sign-in claims the numeric server before discovery. Codes are sealed in transient flow rows, and all callbacks use the same one-shot expiry checks. Concurrent grants cannot mix client and token state. Flow rows expire independently of another sign-in and are excluded from recovery dumps. Per-agent turns and chat in-flight markers use token-owned claims. A running agent turn polls the pause row.
- The five deployment-wide caps count in a `rate_hits` table as fixed windows across processes. Async ingress offloads these database checks and bounds lock waits. Per-person caps stay process-local.
- The anchor-log append holds a database lock across processes. Forge delivery receipts are exempt from generic job retention. New transient tables have explicit retention decisions.

- Saved solo chats load recent messages first. Load older messages adds earlier pages to the current transcript without replacing existing message nodes. Thread and identity changes discard loaded pages and obsolete responses. A field-guide card ties only after a successful, nonempty older-page read.
- Browse task editing changes title, assignee, and due date only. Task Peek separately loads and displays the full description without adding description editing.
- MCP task pages scan past the former 500-row window. Offsets and limits count readable tasks after policy checks, and linked records retain their access checks.
- Task proposals reuse service validation for fixed fields before storage. Invalid legacy task proposals become rejected at approval instead of returning to the queue. Relationships and permissions remain apply-time checks.

- Browser sessions have an eight-hour absolute lifetime, server-side refresh, explicit logout, and identity-bound request handling across tabs. Private mounted state and stale responses do not cross account changes. A Browser sign-in card joins the field guide.
- Reviewed remote MCP execution reads the SDK result envelope correctly. Successful Context7 documentation calls now report completion instead of failure.

- Forgetting a person-addressed memory requires that person's authority, including agent proposals applied through review. Deletion records do not copy memory content into shared activity.
- Concurrent engagement creation and renaming cannot bypass the duplicate-name check. Promise edits and settlement share a service-level row lock, and concurrent key requests produce one pending notification.
- NUL characters in database-bound text produce an input error rather than a server fault.
- Keyboard actions restore focus after rows or editors close. Chat completion and successful actions use status announcements. Forms keep visible labels, and the sticky header leaves room for focused controls.

- A theme change crossfades through the View Transitions API where the browser has it. The hue sliders and the Settings page apply at once, and reduced motion turns the fade off.
- The ⌘K box offers theme commands while you type: a mode, a theme pack by name, or `Colorway: next`. Enter still searches. A Themes card joins the field guide.

### Operations

- The backend requires MCP `>=2.1.1,<2.2`, Strands Agents SDK `>=1.55.1`, and HTTPX2 `>=2.9` at runtime. HTTPX `>=0.28.1,<1` remains the TLS context builder to preserve existing certificate trust. MCP 1.x is no longer supported. Rebuild the backend image and refresh workplace Python locks against the matching core wheel.
- The frontend pins Next.js and its ESLint configuration to 16.3.4 and Sharp to 0.35.4 to address image-processing and Windows server security advisories. Workplace roots must use the same Next.js pin and Sharp override.
- Database pool creation and shutdown are serialized, so concurrent first requests cannot leave an orphan pool. Browser-authentication tests reuse the existing application lifespan and stop maintenance before database teardown.
- Migration 028 adds generic namespaced forge delivery receipts. Migration 029 adds a nonunique repository/event/payload index that permits existing duplicate fingerprints and new alias receipts. Receipt creation and the corresponding policy-checked task mutation commit together. Receipt retention stays permanent. Existing execution fencing, scheduling, and backup/restore behavior remain independent of optional integrations.
- Local Docker fault and PostgreSQL recovery contract scripts use isolated resources and generated test credentials. The guarded OpenShift validation procedure requires an explicit disposable namespace. No production replica default changes. See deploy/k8s/README.md for required cluster and recovery checks.

- Migrations 022 to 025 add lease columns to `agent_wakeups`, `chat_agent_runs`, and `job_runs`, the `mcp_oauth_flows` and `rate_hits` tables, and `mcp_servers.oauth_signin_required`. After a restart, a turn the old process held stays `running` for up to two minutes before the sweep marks it `lease_expired`.
- Migrations 026 and 027 add per-acquisition execution tokens and replace legacy OAuth transit rows with server-bound, sealed claims. Existing transient sign-ins must restart after upgrade.
- The supported deployment stays one replica with Recreate. If more than one replica is ever intended, create the data PVC as `ReadWriteMany` from the start. `backend/tests/test_two_processes.py` runs the fences against a second real process.

- Coordinated manual dumps exclude `public.browser_sessions` and `public.mcp_oauth_flows` data. Before any application process starts after recovery, the runbook SQL clears restored sessions and invalidates API keys. It marks pending or running shared-chat agent requests and delegation wakes as `completion_unknown`, clears execution flags and follow-up wake requests, and preserves history. Run the guarded SQL for external full copies too. Keep ingress closed until reconciliation finishes. `SKEIN_SCHEDULER=0` alone does not stop shared-chat recovery.
- Upgrade instructions require each image tag and its matching reviewed digest. Render the private overlay and check both backend and environment-specific frontend references before sync. A tag-only edit retains the old image bytes.

- Atomic REST policy checks and transaction lifecycle work run off the event loop. Contended database locks have a bounded wait and return a retryable response instead of blocking the API.
- Failed scheduled backups can retry on the same day. Weekly-plan and stale-work claims commit with their database effects.
- Personal MCP discovery starts in a bounded background pool. Registration and chat do not wait for remote startup, and deleted or renamed connections cannot return through a late discovery result.
- Web pages refuse framing and MIME-type guessing. Cross-origin requests omit the referrer.

- The npm packages publish to public npmjs.com through OIDC Trusted Publishing, with provenance, instead of GitHub Packages. `@miloctl/skein-extension-api` and `@miloctl/skein-frontend-host` install with no token, and the `.npmrc` scope routing and `read:packages` PAT are gone. The first version of each package is published by hand once, then the workflow publishes (RELEASING.md).

## 0.5.0 — 2026-09-02

### Contracts

- This release reaches the compatibility ceiling of the previous line. A package that declared `maximum_core_exclusive = "0.5.0"` must declare `"0.6.0"` to load on this core, and a pip bound of `<0.5.0` must widen to `<0.6.0`. A package that does not move stops loading with `supports core versions from X up to but not including Y`. Nothing else in a package needs an edit for the version itself.
- The in-API MCP endpoint composes the same modules as the REST API. A private package's policy, identity, and tool contributions apply to MCP calls over HTTP without `SKEIN_MCP_MODULES`.
- MCP policy actions add `skein.mcp.week.read` and `skein.mcp.memories.read`. A workplace policy that enumerates MCP actions must include them.
- A governed remote MCP server can omit its `tools` block. Skein then derives effect and risk from each tool's own MCP annotations, and every write from that server needs a human review.
- A human identity cannot take a name that ends in `-mcp`. That suffix names the agent identity a person's remote MCP calls act through. Existing rows keep resolving.

### Behavior

- Skein serves its own MCP tools over HTTP at `/api/mcp-server`. The endpoint sits under the perimeter and resolves each caller from a personal API key or deployment sign-in. A self-asserted `X-User` and an agent-owned key are refused.
- A remote MCP caller acts as the agent `<name>-mcp`, reserved on first use. Writes record origin `agent`, actor `<name>-mcp`, and `requested_by` the person. Each person's MCP agent earns authority on its own matrix row.
- The MCP server adds `update_task`, `ask_question`, `answer_question`, `resolve_blocker`, `recall_memories`, and `week`. It offers 23 tools. Every tool declares its MCP annotations.
- `list_tasks` and `search_workspace` take a limit. An MCP result above 256 KiB and a request body above 1 MiB are refused. An unexpected tool error answers a fixed sentence, and the server log records the exception class.
- A person registers their own remote MCP servers in Settings → Connections. Those tools join only the chat turns that person drives. A flock member, a shared chat, the unattended runner, and the MCP actor never receive them.
- A personal MCP server signs in with OAuth 2.1 or a bearer token. Skein seals the tokens and the registered client under `SKEIN_CREDENTIAL_KEY`. A database backup carries ciphertext only.
- Every write from a personal MCP server opens a review. A read opens one review the first time that server, tool, and version runs, then it runs under policy.
- A registered MCP server URL cannot name loopback, link-local, multicast, or reserved addresses. Skein checks the URL again at every connect and does not follow redirects. A person registers up to 8 servers, and one server contributes up to 32 tools.
- `GET /api/agents/trust` shows a person's `-mcp` agent to that person and to administrators only. Its rejection streak is person-level data.
- A rename carries the person's MCP agent, its authority, and its trust history. Deactivation deactivates that agent and deletes the person's registered servers.

### Operations

- `SKEIN_CREDENTIAL_KEY` seals personal MCP credentials and belongs in the deployment Secret. Generate it with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. If the key is absent, a personal server accepts no token.
- Core migration 019 adds the `mcp_servers` table. Core migration 020 adds its OAuth columns.
- An OAuth sign-in for a personal MCP server lives in the process that started it. Run one backend replica, or a sign-in that returns to a different replica fails.
- Claude Code reaches `/api/mcp-server` through a `.mcp.json` entry that reads `SKEIN_API_KEY`, the variable the CLI already uses. The key stays out of the shell history.
- `deploy/k8s/overlays/example-prod/backend-egress.yaml` models the backend egress allowlist. Add one row for each service the deployment reaches before you apply it.
- MCP tool bodies share the backend's sync thread pool with the REST handlers. Every MCP call, reads included, draws on the `mcp` rate bucket.
- Release finalization signs the immutable tag with a fine-grained `RELEASE_TAG_TOKEN` from the protected environment. The token needs Contents write and Workflows write. A preflight names the missing secret before any checkout.
- Release publication and finalization flatten downloaded artifacts. A single-ID download no longer lands under a nested directory the publishers cannot see.
- The installed frontend contract proves the root-owned override refusals. It removes each override and corrupts each lock entry, and it requires the refusal both times.

## 0.4.0 — 2026-08-30

### Contracts

- A retryable `PublicError` with status 503 includes `Retry-After: 60`. Scheduled extension jobs preserve the declared machine code and retryable value. They do not log chained adapter details.
- The workplace template owns an unpublished local contract. It accepts current source or exact prebuilt Skein artifacts without a source checkout at runtime.
- Artifact-only consumer contracts require one shared `SHA256SUMS` file for the exact Skein wheel and npm tarballs.
- The reference directory resolver now fails closed. A private package must supply its authoritative server-side directory adapter before production approval revalidation.
- The backend requires Strands Agents 1.50 or later for per-turn limits and context offload.
- Persona frontmatter accepts optional `flock: false`. Flock validation reads the resolved overlay definition, and omitted means flock-eligible.
- Task dependencies accept `waiting_on=question:<id>`. An answered question satisfies the edge through the existing shared work service.

### Behavior

- A temporary OIDC refresh failure keeps the OIDC session. The browser does not send a stored key or shared token. The request stays bound to the signed-in identity.
- OIDC issuer and endpoint URLs require HTTPS. Skein permits literal loopback HTTP only for local tests. Server-side redirects cannot change the origin.
- OIDC discovery, token, error, and signing-key responses have a 256 KiB body limit and a 5-second total deadline. Four fixed provider slots bound timed-out reads.
- OIDC requires `sub` and binds each human to the verified `(iss, sub)` pair. Username changes cannot transfer private data to a different subject. Overlength and control-character usernames are refused.
- Provider failures write only the exception class to platform logs. Provider response text and tracebacks stay out of logs that bypass Skein content access rules.
- The reference workplace adapter bounds response size and item count. It rejects malformed data, redirects, control characters, duplicate IDs, and unsupported status values.
- The reference status outbox uses leases, per-item order, retry delays, dead-letter rows, and count and wall-clock drain limits. Task events use the same queue.
- The reference dashboard shows an unavailable state when metrics fail. The **Try again** control loads the metrics without a page reload.
- A browser navigation can cancel an in-flight request body. Skein classifies that disconnect as a 400 instead of logging an unhandled server error.
- Frontend extension policy reloads when the stored OIDC session, API key, or trusted-header identity changes. A sign-in cannot leave permitted extension UI hidden.
- Zero-adoption findings start after the field-guide grace period. The final grace day no longer creates a finding.
- The stock bench grows to 23 personas and 5 flocks. Deterministic routing evaluations and pressure traces keep adjacent specialist descriptions distinct.
- Unattended turns have per-invocation turn and token limits. An administrator can pause all unattended runs, queued work stays pending, and cancelled turns retain a durable reason.
- Oversized tool results move to session-scoped offload storage before chat persistence. A storage backstop truncates uncovered tool results above 128 KiB.
- The hourly embedding reconcile job repairs missing vectors after provider recovery. It does not block startup, and partial or complete failures make job health red.
- The findings engine adds `task_abandoned` for open work with multiple contributions followed by silence. Its receipt carries counts, never contributor names.
- Chat distinguishes transient provider load from configuration faults by safe status and exception class. The orchestrator marks external and model-produced text as content, not instructions.
- Governed MCP tools validate the nested result against `output_schema`. MCP and embedding failures log exception classes without remote response text or tracebacks.

### Operations

- The provider for OIDC browser tests handles concurrent connections from the browser and the backend. Browser preconnects no longer block the token exchange.
- Kubernetes readiness uses `/ready` and returns 503 when authentication configuration is invalid. Startup and liveness remain on `/health`.
- Atlas migration 5 adds ordered status leases and preserves pending migration 4 rows. The migration applies during normal extension composition.
- Core, PostgreSQL, and workplace base images use immutable digests. A registry tag cannot change executed image bytes without a source diff.
- OIDC browser tests disable embeddings and run the managed servers as direct child processes. Local runs can prestart the stack with `PW_REUSE=1`.
- The consumer-owned contract uses a run-specific non-superuser database role. Build tools and tested application code never receive the administrator credential.
- Existing roster users need an explicit `python -m app.bind_oidc '<subject>=<user>'` binding before an OIDC cutover. New users bind on first sign-in.
- Application workloads disable default service-account tokens. PostgreSQL bootstrap also removes `REPLICATION` from a pre-existing app role.
- Package installers, frontend builds, identity providers, and browser tests do not receive the database administrator credential. Contract setup and test backends receive it only when they need database access. CI PostgreSQL services and helper images use immutable digests.
- Image publication requires a clean commit at the trusted remote's annotated release tag. It validates every target tag before any build or push.
- The installed frontend contract uses digest-pinned Node 22 and dynamic loopback ports. Ambient browser tokens cannot enter the build or managed runtime.
- Core migration 017 extends task dependency types with questions. Core migration 018 adds session-scoped context-offload storage.
- New reserved content identities are `security-engineer`, `test-engineer`, `requirements-interviewer`, `workflow-architect`, `release-captain`, `research-synthesist`, `plan-reviewer`, `codebase-archaeologist`, `migration-steward`, `org-psychologist`, `technical-writer`, `experiment-tracker`, `judgment`, `people`, and `shiproom`. Before upgrade, run `python -m app.identity_audit`. If a name conflicts, rename the incoming stock file and its references. Do not rename an established human or private machine identity.
- `SKEIN_AGENT_RUN_TURNS` and `SKEIN_AGENT_RUN_TOKENS` bound unattended SDK loops. `SKEIN_OFFLOAD_RESULT_TOKENS` and `SKEIN_OFFLOAD_PREVIEW_TOKENS` control chat result offload.
- The durable `agent_automation` setting pauses and resumes unattended runs without changing authority or review policy.
- The lint gate runs the Simplified Technical English self-test, then checks field-guide `how:` instructions. Service wording remains a warning-only ring.

## 0.3.2 — 2026-08-28

This patch aligns the published frontend host with the tested workplace package boundary. Existing `0.3.x` extensions need no compatibility change.

### Contracts

- No `app.extensions`, `app.public`, or `@miloctl/skein-extension-api` signature changed. Extension API stays 1.0.
- Atlas stays at `2.0.0` with `skein-agents>=0.3.0,<0.4.0`. Version `0.3.2` remains inside that range.

### Behavior

- Browse create controls now have stable form names. Browser autofill no longer reports empty form metadata for these controls.

### Operations

- Workplace tests use a hash-locked dependency closure constrained by the production lock. Current-package tests start in a fresh installed environment.
- The package-built browser contract uses signed OIDC identities for denied, integration, and manager paths. It also completes one core write.
- Contract cleanup is bounded and uses explicit disposable database names. Ambient Atlas credentials cannot enter the browser contract.
- Partial publication retries validate the original run and reuse its tested artifact. PyPI skips an identical wheel and refuses different bytes.
- Release preparation now uses one version input to update synchronized packages, exact artifacts, locks, documentation, and the release marker.
- A protected finalization workflow verifies registry bytes from the original artifact before it creates an annotated release tag.

## 0.3.0 — 2026-08-27

Two deployment defaults changed, and both are visible to a private package.
This is a MINOR release for exactly that reason: a package declaring
`skein>=0.2.0,<0.3.0` will not resolve 0.3.0, which is the cap doing its job.
Widen to `<0.4.0` and re-read the two Behavior entries below before you do.

### Contracts

- No `app.extensions`, `app.public` or `@miloctl/skein-extension-api` signature
  changed, and extension API stays 1.0. The only contract action is the
  compatibility range: a package that declared `maximum_core_exclusive =
  "0.3.0"` must declare `"0.4.0"` to load on this core. Nothing else in a
  package needs an edit for the version itself.
- The Python distribution is now `skein-agents` and publishes to public PyPI through GitHub OIDC. The public imports remain
  `app.extensions`, `app.public`, and `app.main.create_app`. Public PyPI owns
  the unrelated `skein` name, so current packages must not depend on it.
  The import boundary now scans Python stubs and permits only `create_app`
  from `app.main`. Bare, wildcard, private, and aliased private imports fail.
- The complete frontend host now publishes as the private GitHub package `@miloctl/skein-frontend-host`. A workplace project installs it with the private `@miloctl/skein-extension-api` package and runs
  `skein-frontend-build` after it compiles its private extension.
- Atlas 2.0 declares `skein-agents>=0.3.0,<0.4.0`. Atlas 1.x remains the
  pinned fixture for the explicit 0.2.3 package transition.

### Behavior

- **The review gate is ON by default.** `SKEIN_AGENT_REVIEW` defaults to 1;
  it defaulted to 0 through 0.2.x. A mutating agent write — including a
  governed extension tool at the `review` authority level — becomes a
  proposal a human approves, so `execute_tool` returns `review_required`
  carrying a `review_id` where it previously returned `completed` with the
  write already done. An extension whose job or test asserts `completed`
  must either assert both outcomes or grant that (agent, entity) pair
  `autonomous` in the authority matrix. `SKEIN_AGENT_REVIEW=0` restores the
  0.2.x behavior deployment-wide. This is the one trust boundary that
  shipped open while every other one failed closed, and it is what lets
  trust accrue at all: with the gate off no verdict is ever recorded, so no
  agent can earn autonomy.
- **The authentication mode fails closed.** `SKEIN_AUTH_MODE` defaults to
  `api-key`; it defaulted to `trusted-header` through 0.2.x. A deployment
  that set nothing authenticated every caller by a self-asserted `X-User`
  header and now refuses them. Set `SKEIN_AUTH_MODE=trusted-header`
  explicitly to keep the old posture, or supply real credentials.

- `fold_identity` now normalizes before it strips and composes after. The
  old order was not idempotent: a compatibility character that decomposes
  into whitespace ("¯" becomes space plus combining macron) kept the
  space, and a stripped zero-width joiner left a base and its mark
  uncomposed. Two spellings that render the same could fold to two
  identities. Property tests (`tests/test_identity_fold.py`) hold the fold
  to idempotence, case, compatibility forms, and invisible characters. A
  roster with names that only now fold equal surfaces them through the
  existing conflict quarantine, `python -m app.identity_audit`.

### Operations

- CI gates backend line coverage at 90% and prints frontend coverage.
  Local `pytest` is unchanged. `RELEASING.md` now records the release
  procedure. `./scripts/mutation-test.sh <module>` runs on-demand mutation
  testing over `app/services/`.
- The contract rehearsals pin their own `SKEIN_*` environment
  (`scripts/lib/hermetic-env.sh`) and take only `SKEIN_DATABASE_URL` from
  the caller, so a rehearsal answers the same way on a developer machine
  and in CI. Their steps are files under `scripts/contract/` rather than
  shell heredocs, so ruff checks them and a failure names a real file and
  line.
- `reference-images-contract.sh` builds and starts the core and Atlas images.
  It starts a PostgreSQL container on an isolated network and points both
  backend images at it. Skein requires PostgreSQL in 0.2.3 and later. The old
  gate supplied no server, so the image could not start. The contract does
  not use the caller's `SKEIN_DATABASE_URL`. That URL can point to a developer
  database, where image startup can run core migrations. Each readiness loop
  prints the container log when the service does not start.
- The Atlas reference repository owns one npm lock and one combined Python
  production lock. Its final images install exact package artifacts and do
  not inherit Skein application images.
- The old frontend-host archive and host-image contracts are removed. The
  clean frontend contract installs npm tarballs and starts the completed
  standalone server.
- A 0.2.x deployment must uninstall the old `skein` and Atlas 1.x
  distributions before it installs `skein-agents` and Atlas 2.0.

## 0.2.2 — 2026-08-13

The second tagged release, and the first that proves an upgrade: the Atlas
reference extension passes every contract unchanged across the 0.2.1 to 0.2.2
hop, and a database built by 0.2.1 reaches the same schema as a fresh 0.2.2
build with its activity chain intact.

### Contracts

- `WorkItems` gains blocker commands: `create_blocker`, `update_blocker`, and
  `get_blocker`, with `CreateBlockerCommand`, `UpdateBlockerCommand`, and
  `BlockerView`. An impediment from an external system is a blocker in
  Skein's vocabulary, and the facade previously offered only tasks, so an
  integration filed it as the wrong entity. An update resolves a blocker or
  corrects its wording; escalation stays with the scheduled sweep that owns
  the clock. Needs `minimum_core = "0.2.2"`.
- `WorkItems` gains promise commands: `create_promise`, `update_promise`, and
  `get_promise`, with `CreatePromiseCommand`, `UpdatePromiseCommand`, and
  `PromiseView`. A promise carries a direction, an audience, and a settlement
  status that no other entity has. It settles once. Needs
  `minimum_core = "0.2.2"`.
- The event catalog gains `skein.promise.created` and `skein.promise.updated`.
  Both need `minimum_core = "0.2.2"` in a subscribing package.
- The event catalog gains `skein.blocker.created` and `skein.blocker.updated`,
  emitted from the shared blocker write path so every caller produces them.
  Composition still refuses a subscription outside the catalog.
- A public work command that policy holds for review is now durable. A rule
  that returns `review` on any public work command stores the command, and `PublicError.review_id` names the proposal a human approves;
  approval runs the exact saved command under a new grant with the integration
  still recorded as its author. Before this a route answered `409` and a job
  answered `POLICY_REVIEW_UNSUPPORTED` and neither left anything to approve,
  so an unattended integration could be stopped but never asked. A rule on the
  operation action itself still returns `POLICY_REVIEW_UNSUPPORTED`: there is
  no request to resume. Needs `minimum_core = "0.2.2"` to read `review_id`.
- `ExtensionStore(path, include_in_backup=True)` puts an extension-owned
  database into the daily core backup. Previously nothing in core copied it,
  so every private package's data survived on deployment-side discipline
  alone. Skein does not mirror an extension store off the box, and retention
  stays extension-owned. Needs `minimum_core = "0.2.2"` only to set the flag;
  a store from an older package is backed up by the default.
- `app.extensions.assert_import_boundary` raises when a private package
  imports a Skein module outside `app.extensions`, `app.public`, and
  `app.main`. It reads source, so a dynamic import evades it: this is a drift
  check, not a security boundary. Use it for the import-boundary test that
  `docs/EXTENSIONS.md` requires. Needs `minimum_core = "0.2.2"`.

### Behavior

- A public read applies the caller's own visibility filter on every entity.
  `get_promise` read any row, so an extension route could return a teammate's
  private promise. Blocker and task reads were already filtered.
- An idempotency key names one kind of record. The receipt stored the kind and
  nothing compared it, so a key reused across two commands replayed one entity
  as another and returned a row the caller never wrote. A reused key now
  answers `IDEMPOTENCY_KEY_REUSED`.
- A linked write carries the project class of the row it attaches to. A
  blocker and a promise read that class from the caller instead, so a rule
  keyed on it governed task writes into a regulated engagement and skipped
  the other two.
- An approval releases the one action it answered. A command that meets two
  independent review rules is held again for the second, rather than riding
  the first approval through a gate whose approvers were never computed.
- The review queue carries a bounded preview of the command it is holding, so
  a status change bundled into a create is visible on the approve screen.
- A held command whose target vanished before the verdict settles as rejected
  instead of returning to the queue on every attempt.
- A domain write carries the declared effect and risk of the contribution
  performing it. A route, job, tool, event subscriber, or workflow action
  declares `effect` and `risk`, and the `work.task.*` decision previously
  reached the policy engine as `effect="none"`, `risk="low"` whatever the
  contribution said, so a workplace rule keyed on risk never fired on the
  write it meant to gate. Rules keyed on project type are unaffected.
- An extension route's work grant ends with its response. A route declares no
  deadline, so a thread the handler started kept writing core rows under the
  route's provenance after the response and after shutdown. A later call now
  returns `EXECUTION_CONTEXT_CLOSED`. Use a `JobContribution` for background
  work.
- `ExtensionStore` connections refuse `ATTACH`. The configured-path check
  sees only the file the store opened, so one `ATTACH` statement reached a
  core database from a connection that had already passed it. Both checks
  prevent accidents and neither is an isolation boundary.
- Composition logs a warning when no installed `skein` distribution names the
  core version and the source fallback is used instead. Every module
  compatibility range is checked against that number, so a guessed one can
  refuse a valid private package.

### Operations

- Core migration 021 widens the reviewed-invocation kinds so a held public
  command can be stored. It rebuilds `extension_review_invocations` and copies
  every existing row.
- `SKEIN_REVIEW_SEPARATION=1` refuses an approver who is the person a
  proposal came from, so an approval costs a second pair of eyes without a
  policy rule. Off by default. A policy rule that names `approver_groups`
  composes with it and both checks must pass. Rejection is unchanged: a rule
  that traps a proposal in the queue is worse than one person declining it.

## 0.2.1 — 2026-08-13

First release after the workplace extension boundary. Extension API 1.0 for
both the backend and the frontend.

### Contracts

- A tool contribution can declare `error_codes`, and Skein preserves a
  declared code from `PublicError` instead of returning the generic
  `tool_error`. Needs `minimum_core = "0.2.1"`.
- A reviewed tool that writes through the supplied `WorkItems` service runs
  its command in the reviewer's transaction. Needs `minimum_core = "0.2.1"`.
  A tool that performs no reviewed local write keeps a `0.2.0` floor.
- A workflow action that uses `WorkItems` after review has the same
  requirement. An external-only action keeps a `0.2.0` floor.
- `ExtensionStore.transaction` supplies an explicit SQLite transaction. Needs
  `minimum_core = "0.2.1"`. A package with a `0.2.0` floor uses `connect`.

### Behavior

- Core REST mutations, agent tools, classified MCP tools, workflow steps,
  contributed routes, scheduled jobs, and frontend capability checks all
  evaluate one composed policy engine. A workplace permit cannot remove a
  core denial.
- Core migrations 018 through 020 record durable identity ownership,
  notification sources, and creation-time policy context.

### Operations

- `SKEIN_MCP_MODULES` gives the standalone MCP process the same module
  composition the API process uses. Without it that process composes core
  only, which leaves two policy boundaries in one deployment.
- `python -m app.identity_audit` reports and repairs roster identity
  conflicts. Run the documented claim commands before the first restart on
  this release.
