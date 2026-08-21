# The SKEIN_* environment a contract rehearsal runs under. Source, then call
# skein_hermetic_env once, before anything builds or boots.
#
# A rehearsal already isolates its INFRASTRUCTURE with some care: its own
# virtualenvs, a database per instance, its own content directories. It used
# to read every other setting from whatever environment happened to run it,
# and that split is what made the same script pass on a developer box and
# fail in CI. Two real examples, both of which reached main:
#
#   * backend/.env sets SKEIN_AUTH_MODE=trusted-header, so a developer's run
#     authenticated. CI sets no auth mode at all, so its run got the shipped
#     default and every request 401'd.
#   * v0.2.3 defaults SKEIN_AUTH_MODE to trusted-header and HEAD defaults it
#     to api-key. With neither pinned, the prior-core leg authenticated and
#     the next-core leg did not — a default MOVING between the two artifacts
#     under test, reported as an extension failure.
#
# Both are the same defect: an ambient input to a rehearsal that claims to be
# isolated. Pinning them is what makes this script answer the question it
# says it answers — whether ONE unchanged extension spans a core range —
# instead of quietly also answering "did a default move?". Default drift is a
# real question, but it belongs to a deployment contract, not to this one.
#
# EMPTY, never `unset`: config.py calls load_dotenv(), which refills an
# ABSENT variable from backend/.env, so unsetting is precisely what lets a
# developer's overlay back in. An empty value survives load_dotenv and every
# setting reads it as "no overlay". tests/conftest.py pins its own
# environment this way, for this reason.
#
# CLOSED by default: the whole SKEIN_* namespace is blanked and only the
# handful below is set back. A setting added to config.py tomorrow cannot
# leak into a rehearsal without an edit here — the opposite of a list that
# protects what somebody remembered to add to it.
skein_hermetic_env() {
    # The one input a CALLER legitimately supplies: which server to create
    # per-instance databases on. That is infrastructure. Everything else in
    # the namespace is configuration, and configuration is the rehearsal's
    # own to state.
    local database_url="${SKEIN_DATABASE_URL:-}"

    local name
    for name in $(env | sed -n 's/^\(SKEIN_[A-Za-z0-9_]*\)=.*/\1/p'); do
        export "${name}="
    done

    export SKEIN_DATABASE_URL="$database_url"
    # trusted-header, so both artifacts under test authenticate identically —
    # see the v0.2.3-vs-HEAD note above. The rehearsal drives the X-User door
    # the same way scripts/skein.sh and tests/conftest.py do.
    export SKEIN_AUTH_MODE=trusted-header
    # The review gate is a DEPLOYMENT policy, and it changes what a governed
    # extension tool returns: direct write versus queued proposal. Pinned so
    # the ambient default cannot decide it, and exercised explicitly on both
    # settings where that difference is the subject (scripts/contract/).
    export SKEIN_AGENT_REVIEW=0
    export SKEIN_MODEL_PROVIDER=mock
    export SKEIN_SCHEDULER=0
    export SKEIN_EMBEDDINGS=0
}
