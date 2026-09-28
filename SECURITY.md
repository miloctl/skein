# Security policy

## Report a vulnerability

Report it privately through GitHub:
<https://github.com/miloctl/skein/security/advisories/new>. Do not open a
public issue.

Include the Skein version, the `SKEIN_AUTH_MODE` in use, and the steps that
show the problem. A report is in scope when it breaks one of the properties
below.

## Supported versions

Only the latest release gets security fixes. The release notes are in
[CHANGELOG.md](CHANGELOG.md).

## Properties Skein promises

These are in scope. [docs/VISIBILITY.md](docs/VISIBILITY.md) is the full
contract.

- **Visibility tiers.** A `private` row reaches only its author. A `crew` row
  reaches only that crew's members. A refused read answers 404, with the same
  words as an absent row. No private row reaches a shared sink: search,
  embeddings, context packs, digests, readouts, findings, the calendar feed,
  exports, or the body of an activity row.
- **Identity.** In `api-key` and `oidc` mode, a caller cannot act as another
  person. Scoped reads need strong identity. A person cannot claim a reserved
  system actor name or an agent's name.
- **Agent writes.** An agent write through a chat tool or the MCP server
  passes the review gate and the authority matrix. Four writers skip the gate
  by design, and each one obeys the `forbidden` level: the delegation loop
  (claim a delegated task, write a progress note, submit it for acceptance)
  works only on a task delegated to that agent, and a submission is itself a
  proposal to the sponsor. Handoff generation writes a report from records
  the agent can already read. Only a human can set an agent's authority. An
  agent cannot approve its own proposal.
- **Credentials.** Personal MCP tokens and browser sign-in tokens are sealed
  under `SKEIN_CREDENTIAL_KEY`. No credential is stored in `app_settings`, and
  no error response echoes a rejected value.
- **Provenance.** The activity ledger is hash-chained, and a nightly job
  copies the verified tip to anchor files outside the database. A rewrite of
  chained rows is exposed by an anchor file that the attacker could not also
  change. "Activity-ledger claim boundaries" in
  [docs/FEATURES.md](docs/FEATURES.md) states what the chain does not prove.
- **Signed inbound events.** The forge webhook refuses an unsigned delivery,
  and it ignores a signed delivery it already applied, within the limits that
  "Code forge webhook" in [docs/FEATURES.md](docs/FEATURES.md) states. The CI
  webhook refuses a self-asserted name.
- **Erasure.** Thirty days after offboarding, the records only that person
  could read are deleted.

## Out of scope

These are documented design properties, not vulnerabilities. The README
section "Security model, stated plainly" explains the first three.

- **`trusted-header` mode.** Identity is the self-asserted `X-User` header.
  The mode is for local development and trusted networks only.
- **`SKEIN_API_TOKEN`.** It is in the frontend's public JavaScript bundle. It
  keeps out network scanners, not people who can load the UI.
- **REST writes by people.** They do not pass the agent review gate. Issue
  personal keys to people, not to agent processes that must be gated.
- **Rate caps.** They limit floods. They are not a security control.
- **Prompt injection alone.** A model that says or proposes something wrong
  is expected. It is a vulnerability only when it crosses a property above:
  a write that skips the review gate, a read past a visibility tier, or an
  authority change without a human.
- **Trusted operator content.** System MCP servers, workplace extension
  modules, personas, playbooks and flocks are configured by whoever runs the
  server, and they run as trusted code or content.
- **Access to the host, the database or the deployment Secret.** A person
  with that access can read or change everything.
