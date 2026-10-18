# Search inside chats

Decisions confirmed 2026-10-17 with the repository owner (last section).

- **Outcome:** A person types words or parts of words in the chat sidebar and finds the chat
  where something was said, in their own solo chats and in the shared chats they belong to.
  Selecting a result opens that chat at the message.
- **User:** Everyone who uses Chat. Heavy users most: the sidebar holds up to 500 chats, and
  titles come from the first message, so a later topic in a long chat has no title to find it.
- **Why now:** The owner asked for it. Today the only way back to an old answer is to scroll
  the sidebar by title and then read the transcript.
- **Success:** Ana asked last month how to rotate the staging certificate. She types
  `rotat cert` in the sidebar, presses Enter, and sees that chat with the matching words in
  bold. She selects it, and the transcript opens with that message in view and focused. Raj,
  who is not a member of Ana's shared room, never sees a hit from it. On
  `SKEIN_MODEL_PROVIDER=mock`, all of it works: no model call, no embeddings.
- **Constraint:** Keyless. Chat text never enters `search_index`, so global search, `/ask`,
  MCP `search_workspace` and the embeddings endpoint never see it. No migration, no new
  setting, no new UI library.
- **Out of scope:** Agent, MCP and CLI access to chat history. An index for substring search.
  Exact phrases and stemming. Chat hits in the nav search box. Filters by date or author.
  Each becomes a cut-table row in `docs/ROADMAP.md` (list at the end).

Chat is not a portfolio surface, so the 2026-08-21 posture note does not block this.

## What exists before this work (2026-10-17)

| Part | Where | Note |
|---|---|---|
| Transcript | `chat_messages` (`001_baseline.sql:578`, `012`, `014`) | `content` is plain text. One table for solo and shared chats. A shared delete blanks `content` and sets `deleted_at`. Only `idx_chat_messages_thread (thread_id, id)` exists. |
| Solo access | `chat_threads._own`, `CurrentUser` | Owner-scoped by the caller's name, weak identity allowed. A miss is 404. |
| Shared access | `_require_member`, `StrongUser` | Strong identity plus an active `chat_members` row (`left_at IS NULL`). Invitations always share history (`CHECK (share_history)`), so a member can read every message in the room. |
| Strength in a route | `ViewerDep` | `viewer.name` is the caller for a strong identity and `""` for a weak one (`deps._stash`, `briefing.py:697` uses this for the shared unread badge). |
| History paging | `GET /api/chats/{id}/messages/page?before=` | Newest 50, then older pages. `ThreadHydrator` in `app/runtime-provider.tsx` loads it, and "Load older messages" pages back. No way to open at one message. |
| Shared deep link | `/chat?shared=<id>#shared-message-<n>` | `components/shared-chat.tsx:294` loads from message n onward and focuses it. Reusable as is. |
| Message DOM ids | assistant-ui `MessagePrimitive.Root` | Renders `data-message-id`, `saved-<id>` for a stored message. `thread.tsx:1013` already reads it. |
| Safe snippet render | `Snippet` in `components/nav-search.tsx:53` | Splits on `<b>` and lets React escape the rest. Never HTML. |
| Database role | `deploy/postgres-init/10-app-role.sh` | `CREATE` on schema `public` only, no database-wide `CREATE`. `pg_trgm` is a trusted extension, but installing one needs database `CREATE`, so a migration that creates it fails the boot. This is why substring search has no index. |
| Field guide | `fieldguide/knots.yaml`, `fieldguide.mark` | `chat_history` is the model: `ties: mark`, `None` predicate, marked by the route only on a nonempty result. |
| Policy route walk | `tests/test_policy_axis.py::no_project_routes` | Every `/api/chats*` route is listed there. A new one must be too. |

## Design

### Matching

- Split `q` on whitespace into words. A message matches when every word appears in it, in
  any order, ignoring case: one `content ILIKE ? ESCAPE '\'` per word, joined with `AND`.
- Escape `\`, `%` and `_` in each word before it becomes a pattern. Unescaped, a search for
  `%` matches every message and `_` matches any character. A test pins this.
- At most 8 words. More is a 400 that does not echo the query (`The search has more than 8
  words. Use 8 words or fewer.`). `q` is at most 200 characters (422 past that, from FastAPI).
- No index (see Database role above). The query joins the caller's own threads first, so it
  reads only the messages in the caller's own chats, not the whole table. Ceiling: a heavy
  user with tens of thousands of messages waits a few hundred milliseconds. The code carries a `ponytail:`
  comment that names the upgrade: the database owner installs `pg_trgm`, then a migration adds
  a `gin_trgm_ops` index on `content`. A `LEFT JOIN LATERAL` per thread (newest message first,
  `LIMIT 1`) keeps the plan on `idx_chat_messages_thread`, so the planner cannot filter the
  whole table. Measured on 96,000 seeded messages (about 100 MB, 8 people), one person's
  12,000 messages took 40 to 270 ms: the high end is a cold search straight after the
  insert.
- ILIKE costs text length times pattern length, and the caller picks both. Long runs of one
  letter in their own chats, searched with a crafted 8-word pattern, took 6 to 9 seconds,
  and 8 at once held every worker in the sync thread pool. So a search runs under a 3-second
  statement timeout, local to its own transaction (`set_config(..., true)`), which answers a
  400 that says the search stopped, and under its own rate cap (`chat_search`, 30 a minute).

### Service: `chat_threads.search_chats(owner, member, q)`

- `owner` is the caller (solo chats). `member` is `viewer.name` (shared chats). A weak
  identity passes `""` and gets solo hits only, which is the same reach it has today.
- The generic policy gate judges search as `chats`. The route asks the workplace policy
  about `skein.rest.get.shared-chats.messages`, the action the shared-chat read route is
  judged under. A rule on every room blanks `member`, so closed rooms take no result slot. A
  rule on one room is judged on that room's id, and its hits are dropped.
- Candidate threads: the caller's solo threads, plus shared threads with an active
  membership. Exactly the rows `_own` and `_require_member` admit.
- Message hits skip rows with `deleted_at` set (already blank, but explicit).
- Title hits: a thread whose title holds every word, with no message hit, comes back with
  `message_id: null`. A renamed chat is found by the name its owner gave it.
- One result per chat: the newest matching message. Results newest match first, the message
  id breaking a tie (`db.now()` keeps whole seconds). Limit 30, fetched as 31 to set
  `truncated`.
- The solo branch keeps the sidebar's 500-chat cap (`THREAD_LIMIT`). The client loads a solo
  transcript only for a chat `GET /api/chats` lists, so a hit past the cap would open empty.
  A shared hit opens through its own route, whatever the list shows, so that branch has no
  cap.
- The snippet is built in Python from the matched message: whitespace collapsed first, then
  about 120 characters around the first match, each word's matches wrapped in `<b>`, so the existing `Snippet` renders it.
  The message body itself never goes to the client. A literal `<b>` in a message shows bold
  in its snippet. That is cosmetic only, the same as nav search today, because `Snippet`
  never renders HTML.

Response:

```json
{"results": [{"thread_id": "...", "kind": "solo", "title": "...", "message_id": 812,
              "matched_at": "...", "snippet": "…<b>rotat</b>e the staging <b>cert</b>ificate…"}],
 "truncated": false}
```

### Routes (`routes/chat.py`)

- `GET /api/chats/search?q=` with `q: Query(min_length=1, max_length=200)`. Declared before
  the `{thread_id}` routes. Marks `chat_search` after the read and only when a result came
  back. The query text is never logged.
- `GET /api/chats/{id}/messages/page?through=<message_id>`: the newest page, extended back to
  include that message, capped at `MESSAGE_LIMIT` (1000). `next_before` works as before, so
  "Load older messages" keeps working. `through` from another thread is the same 404 as a
  foreign `before`. `through` with `before` is a 400. The count and the read run in one
  snapshot: apart, a reply committed between them pushed the message out of the window.
  If the message is more than 1000 messages back, the client pages back to it.

### Frontend

- `components/chat-search.tsx` (new, small): a labeled search field under "+ New chat".
  Enter runs the search, Escape clears it. While a query is active the results replace the
  chat list. A status region announces the count. Each result shows the title, a "Shared"
  tag for a room, the date and the snippet.
- Move `Snippet` out of `nav-search.tsx` into its own module and use it in both places.
- `app/chat/page.tsx`: `open(id, kind, messageId?)`.
  - Shared: write `#shared-message-<id>` into the address, as the existing deep link does.
  - Solo: pass the target to `ThreadHydrator`, which loads with `?through=`. After load,
    `thread.tsx` focuses `[data-message-id="saved-<id>"]` (with `tabIndex=-1`, see
    `lib/hash-target.ts`) and shows a ring until the reader acts. This must win over the
    initial scroll-to-bottom, and the viewport's `autoScroll` stays off until the reader sends.
    Left on, assistant-ui follows the bottom on the next content resize and the landed message
    scrolls out of view (`e2e/chat-search.spec.ts`).
  - A hit in the solo chat already on screen never remounts it: a remount drops the unsent
    draft, the loaded pages, and a streaming reply (the runtime aborts its run on unmount).
    The page sends `TARGET_EVENT`, and `Thread` lands in place, or pages back with Load older
    messages until the message loads. A message sent in this session has a local id, never
    `saved-N`, and is newer than the oldest saved one, so `Thread` leaves the view there.
  - A shared hit in the room on screen re-lands through `skein-hash` when the room holds the
    message, and remounts the room at it otherwise. The room's draft is in sessionStorage.
  - Selecting a hit drops the cached chat list first (`skein-chat-activity`). A chat made in
    another tab is in the results but not in that list, and it opened empty.
  - On a phone the drawer closes on select, as it does for a normal open.
- Wording (STE): field label `Search your chats`. No hit: `No chat matches these words.`
  Truncated: the status reads `More than 30 chats match. The list shows the 30 chats with
  the newest matches. To find fewer chats, add a word.` A refusal the server answered shows
  as its own sentence. An unreachable backend reads `The search did not run.` before the
  shared backend-unreachable string. Each solo message is named for its author (`Your
  message`, `Reply`), because focus can land on one. "New folder" is not offered while results hide the
  folder list.

### Field-guide card

```yaml
  - id: chat_search
    feature: Search your chats
    knot: Icicle Hitch
    set: loops
    ties: mark
    pitch: Every conversation stays on the line, and this hitch grips the one you need.
    how: "Open Chat. Type words or parts of words in Search your chats, then press Enter. Select a result to open the chat at that message."
    link: /chat
```

`fieldguide.py` gets `"chat_search": None` with a comment that only the route can see a
search. Card counts move 75 to 76 and 74 to 75 tieable (`test_fieldguide.py`, `FEATURES.md`).

## Build order

1. **Backend** - `search_chats` and its snippet helper, both route changes, card, tests.
   Gate: `.venv/bin/pytest` exit status, `./scripts/lint.sh`.
2. **Frontend** - `Snippet` move, `chat-search.tsx`, page and hydrator wiring, focus landing,
   vitest, one Playwright walk with axe. Gate: `npm test` exit status, `npm run build`,
   `./scripts/lint.sh`. Check it live on the seeded mock stack, phone width included.
3. **Docs** - `FEATURES.md` "Chat history + folders" row and card count, `ROADMAP.md` cut
   rows.

About 120 lines of backend code, 200 of frontend, and the tests.

## Tests

Each one fails against the code before this work. Fixtures come from the real write path
(`POST /api/chat` on mock, `chat_threads.log_message`, the shared-chat routes), never
hand-built rows.

Backend, `tests/test_chat_search.py`:

- Part of a word from a mock reply finds the chat, in any case. The result has the right
  thread, message id, and a snippet with each match in `<b>`.
- Two words match only a message that holds both.
- `%`, `_` and `\` match only themselves: a search for `%` does not return every chat.
- Bob's chat with the same word never reaches Alice, by `X-User` or by key.
- Shared: a strong member gets the room hit. The same person on `X-User` gets solo hits only.
  A nonmember gets none. A member who left gets none. A deleted message does not match.
- A renamed chat is found by its new title with `message_id: null`.
- A deleted solo chat stops matching.
- `q` empty and `q` over 200 are 422. Nine words is a 400 whose text does not hold the query.
- More than 30 matching chats sets `truncated`.
- `through=`: the window starts at the target and reaches the newest message. A foreign id is
  404. With `before` is 400. A target 1000+ messages back is absent and `next_before` is set.
- The snippet is an exact window around the first match, and whitespace collapses first.
- A search past the statement timeout is a 400. The `chat_search` cap answers 429.
- `through=` reads one snapshot: a reply committed between the count and the read, from a
  second connection, does not push the message out.
- `test_policy_axis.py`: add `("GET", "/api/chats/search")` to `no_project_routes`. A rule
  on every shared room, and a rule on one room, close the same rooms in search as on their
  own route. Closed rooms take no result slot: 31 newer closed-room hits still leave the
  solo hit.
- `test_fieldguide.py`: the new counts. The route marks only on a nonempty result.

Frontend:

- vitest for `chat-search.tsx`: Enter searches, Escape restores the list, the empty and
  truncated lines, a hit calls `open` with the message id, a snippet holding `<img onerror>`
  renders as text.
- vitest for the hydrator: a target loads with `through=` and focuses the message. A target
  older than the window pages back by itself, then lands.
- vitest for the chat page (`chat-search-same-chat.test.tsx`): a hit in the chat on screen
  lands in place and pages back in place, with the draft and the transcript nodes kept.
- vitest for the sidebar: "More than 30 chats match." with a real 30-result truncated
  payload, Escape inside the phone drawer clears first and closes second, no "New folder"
  during a search, and the cached list dropped before a hit opens.
- Playwright: send a message on mock, search part of a word from the reply, open the hit, the
  message has focus. Axe clean.

## Cut rows (in `docs/ROADMAP.md`)

| Item | Trigger |
|---|---|
| Indexed substring search (`pg_trgm` with a `gin_trgm_ops` index) | Chat search gets slow for a heavy user, or the 3-second timeout stops ordinary searches. The database owner installs the extension first, because the app role cannot (`deploy/postgres-init/10-app-role.sh`). |
| Exact phrase in quotes, and stemming (`deploying` finds `deployed`) | Somebody asks. Part-of-a-word matching covers most of it: `deploy` finds both. |
| Chat results in the nav search box (a "Search your chats for ..." link) | Somebody looks for a chat from the nav box. |
| More than 30 results, or every matching message in one chat | Somebody needs a match the 30 hide. |
| Date and author filters | Somebody asks. |
| Agent tool, MCP and CLI access to chat history | Refused for now. An agent tool sends old chat text to the model provider, and MCP or the CLI sends it to an outside client. Each needs its own privacy decision. |
| Semantic search over chats | Refused. Chat text must not reach the embeddings endpoint (the `_embeddable` rule). |

## Decisions (confirmed 2026-10-17)

1. **Shared chats are included**, for a strong identity with an active membership. The reader
   can already open every message there.
2. **Titles match too.** A chat is found by the name its owner gave it.
3. **Words match as parts of words** (`ILIKE`, every word must appear). The owner chose this
   over indexed whole-word search. There is no index, because the app role cannot install
   `pg_trgm`.
4. **One result per chat**, with the newest matching message as the snippet.
