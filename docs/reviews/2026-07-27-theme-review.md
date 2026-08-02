# Theme system — 5-agent review (2026-07-27)

> **Status (2026-08-02): CLOSED — TD1/TD2/TD6 and TP5/TP6/TP3 shipped. The
> open items (TD3-TD5, TD7-TD10, the Vellum pack concept, TP1) moved to
> `docs/ROADMAP.md` under "Open backlog". This file stays because
> `globals.css`, `theme.ts` and `whimsy.ts` cite these IDs in source comments,
> so it is the definition site for TD1, TD2, TD6, TP3, TP5 and TP6.**
>
> The `pack-contrast-check` scripts this review referenced are gone. They were
> replaced on 2026-08-02 by `scripts/check_theme_contrast.py`, which reads the
> packs out of `globals.css` and runs in `lint.sh`. The originals hardcoded
> their pack tokens, so Atelier was never checked.

Fixed same-day (verified findings): adopt-fetch race (TC1), defaults-stored-
as-absence letting the profile hijack a deliberate reset (TC2/TB5), merge
dropping the source user's theme + growth interests (TC3/TB2, test-pinned),
pagehide flush for saves within the 800ms debounce (TC4), rounded-md routed
per pack, 12px-tier legibility bump for Atelier serif / Phosphor mono
(TA2/TA3, via --fs-xs), texture kill-switch under prefers-contrast: more
(TA4), forced-colors hardening + nav aria-current (TA5/TA6), set_theme
no-log provenance exception documented (TB1). Verified sound: focus ring on
all packs incl. all 360 custom hues (TA1), Ledger shadowless floats (TA7),
debounce/loop/injection paths, export semantics (TB4), keys-only server
validation as design (TB6). Accepted: one-time adopt flash per new browser
(TA8; optional polish = adopt at name-pick).

## Proposals awaiting triage

Design (CSS-var level, AA checker stays the gate) — priority TD1, TD2, TD6:
- TD1 display-type voice per pack (tracking/case/Fraunces SOFT+WONK axes/glow)
- TD2 per-pack selvage (Oxford rule / phosphor line / weld thread)
- TD3 density dial via --spacing (ledger dense, atelier airy)
- TD4 loom weft (real cloth: warp+weft)
- TD5 phosphor dark-mode bloom + heading glow
- TD6 per-pack empty-state flourishes (blinking prompt / ruled blanks / fleuron)
- TD7 ledger masthead rule + 20px text-aligned ruling
- TD8 atelier laid-paper texture (light only)
- TD9 high-contrast commits: plain heading face + 3px focus ring
- TD10 phosphor light mode = paper teletype (dot-matrix texture)
- New pack concept: **Vellum** (drafting grid / blueprint night) — full token
  sketch in the review transcript; run `scripts/check_theme_contrast.py`
  before shipping.

Product — recommended order TP5 → TP6 → TP3, then stop:
- TP5 shareable theme code (copy/paste JSON or ?theme= link) — S, DO
- TP6 pack-aware voice for ~6 empty states w/ default fallback — S/M, DO
- TP3 team default theme (operator-set, adopt fallback) — S, DO
- TP1 named presets (MAYBE later), TP2 per-appearance packs / TP4 seasonal /
  TP7 OS accent (not buildable) / TP8 scheduled dark — SKIP.
