# TODO

Deliberate debts — things we decided to ship without, on purpose, with the
condition that brought them here. Backlog/ideation lives in docs/ROADMAP.md;
this file is only for accepted trade-offs that must eventually be repaid.

- **Harden field-guide identity.** `GET /api/field-guide` (and `/hint`)
  ride weak `X-User` identity — a spoofed header can read a teammate's
  untied cards and consume their "newly tied" state. Accepted 2026-07-31
  under the trusted-LAN model (same exposure class as the My Day briefing;
  truly private surfaces are excluded from the guide entirely — see
  docs/FIELD-GUIDE.md "Weak identity accepted"). Repay when the OIDC
  identity bridge lands: once every request carries a validated identity,
  move the guide (and the other personal-but-not-private surfaces) onto it
  instead of inventing a bespoke auth layer now.
