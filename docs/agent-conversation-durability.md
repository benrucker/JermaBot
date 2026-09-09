# Coding agent: durable conversations

Status: implemented, v1 (supersedes the v0 "evict after 7 idle days"
behavior). Built on branch `durable-agent-conversations`, 2026-09-09; see
"Implementation notes" at the end for where the build deviates from the
wording below.

## Problem

Today a conversation is a local object: a checkout directory, an Agent SDK
session transcript keyed to that directory, and an entry in `state.json`.
Any of those going away (the daily idle sweep, a lost directory, a reimaged
host) ends the conversation. The Discord thread outlives all of them, so the
owner posts a follow-up into a thread that looks alive and the bot silently
ignores it. That happened on 2026-09-02 in a thread started 2026-08-16.

## Goal

From the owner's point of view a conversation never ends. A message posted
in an agent thread, at any later time, gets a response from an agent that
knows everything that happened in that thread and continues the same branch
and pull request. How the backend achieves that is unconstrained; resource
use is not a concern.

## Definitions

- **Agent thread**: a Discord thread the bot created to hold one
  conversation. Its id equals the id of the owner message that started it.
- **Identity**: the durable facts about a conversation: thread id, branch
  name, pull request URL per repo, SDK session id, and whether the
  conversation has run with the transcript backup as its session store
  (`backed_up`). Small, and kept forever.
- **Transcript**: the Agent SDK's session file for a conversation: every
  prompt, reply, tool call, and tool result, as the agent experienced them.
  The SDK writes it under its projects directory, keyed by the checkout
  path and session id, and resumes from it.
- **Materialization**: the local, rebuildable parts: worktrees, the local
  copy of the transcript, downloaded attachments. Disposable at any time.
- **Recovery**: rebuilding a materialization from identity, the transcript
  backup, GitHub, and the thread's own message history, so a turn can run.

## Requirements

### R1. Threads are recognized without local state

1. Any message from the owner in an agent thread is a turn of that
   conversation, no ping required, forever. This must hold when the bot
   has no record of the thread at all (fresh host, wiped state).
2. Recognition must therefore derive from Discord, not from the local
   conversations table. The thread's starter message is a reliable anchor:
   the thread's id is the starter message's id, the starter is authored by
   the owner, and it pings the bot. A local index may be consulted first as
   a fast path but is never the sole source of truth.
3. Archived threads count. Discord auto-archives idle threads; a message in
   one unarchives it. The bot must handle the message whether or not the
   thread was in its cache when the message arrived.
4. The bot never archives, locks, or deletes an agent thread, and sets the
   longest auto-archive duration Discord allows when it creates one.

### R2. Every turn runs with full context

1. Before running a turn, the agent has the entire history of that
   conversation. The standard is lossless: the same context it would have
   had if the conversation had never left memory, including its own tool
   calls and tool results, not only what was said in the thread.
2. Context comes from these sources, tried in order, and the first that
   works is used:
   1. The local transcript, when present and resumable.
   2. The transcript backup (R2b), restored to the local path the SDK
      expects for this conversation, then resumed.
   3. Reconstruction from the thread's messages (R2c). This is lossy and
      exists for v0 threads and for a backup that is missing or fails to
      restore. When it is used, the recovery status line in the thread
      says so (R4.3).
3. Whichever source is used, the agent also gets the git context it would
   have had: the conversation's branch checked out at its current remote
   tip.
4. If a higher-priority source is present but fails, fall through to the
   next rather than failing the turn. The failure is logged with its cause.

### R2b. Transcript backup and restore

1. At the end of every turn, after the SDK has finished writing, the
   conversation's transcript is copied to durable storage that survives
   loss of the host. The turn is not reported as complete until the copy
   succeeds; if it fails, the thread is told (fail-fast), though the turn's
   reply and PR still stand.
2. Durable storage is outside the bot's host. A private GitHub repository
   or gist written with the bot's existing token is the expected choice; a
   transcript may exceed Discord's attachment limit, so the thread itself
   is not suitable.
3. Restore places the file exactly where the SDK will look for it. The
   checkout path per conversation is already deterministic (the
   conversations root plus the thread id), and the session id is part of
   the identity record, so the restore location is computable without
   local state. The implementation must confirm the SDK's transcript
   layout on the production host rather than assuming it.
4. Session id is therefore promoted to identity and is recoverable with
   the rest of it (R3.3 sources plus the backup store's own index, keyed
   by thread id). `backed_up` travels with it: it is what says whether a
   turn may run at all while the store is unreachable (see the
   implementation notes).
5. Backups are never deleted by the eviction sweep. Local transcript
   copies may be.

### R2c. Reconstruction from the thread (fallback)

1. The history is built from every owner message (text and image
   attachments) and every bot reply (progress messages, answers, PR
   announcements, error and status messages), in order, with timestamps.
   It must not depend on anything stored locally.
1b. The harness's own lines are excluded from what the agent is told it
   said: the R4.3 recovery line, the R3.6 merge-conflict line, timeout and
   error notices, and PR announcements. They are recognizable by their
   fixed shapes. PR announcements and error notices may still be summarized
   as harness facts ("a PR was opened at ...", "turn 3 timed out") so the
   agent knows what happened, but never as its own words.
2. It is presented to the agent clearly labeled as prior history of this
   conversation (not as a new request), followed by the new message. Image
   attachments in prior messages are re-downloaded through Discord so the
   agent can see them; attachments Discord no longer serves are noted as
   missing rather than dropped silently.
3. Known losses, accepted for this path only: tool calls and results, the
   interleaving of narration with tool use, and harness-added prompt text.
4. Once a reconstructed turn completes, its new transcript is backed up
   per R2b, so the conversation is lossless from that turn on.

### R3. Git and pull request continuity

1. A conversation has one branch and at most one pull request per repo for
   its whole life, across any number of materializations.
2. GitHub is the durable store for code state. A recovered conversation
   checks out the existing remote branch; it never recreates the branch from
   the base. Local worktrees may be discarded freely between turns as long
   as every turn's edits were pushed (which the existing publish step
   guarantees).
3. The branch name and PR URLs must be recoverable when the identity record
   is lost. Sources, in order: the identity record; PR announcement messages
   in the thread ("Pull request for **repo**: url"); a GitHub search for
   open or closed PRs whose head branch carries the agent's prefix and whose
   body carries the agent's footer, matched to the thread by the starter
   message text embedded in the first commit message. If no branch is found
   (the earlier turns never edited code), the next turn that edits code
   creates one as it does today.
4. If the branch was merged or deleted on GitHub, the turn proceeds on a
   fresh branch from the base, and a muted line in the thread states that
   the previous PR was merged/closed and a new one was opened.
5. Before every turn, the harness catches the branch up to its base:
   fetch, then merge the base branch into the conversation branch. A
   clean merge is committed and pushed with the turn, so the agent always
   edits current code and the PR stays mergeable. This applies to every
   turn, not only recovered ones; a live conversation goes stale too.
6. If the merge conflicts, the harness aborts it, leaves the branch as it
   was, and posts one muted line in the thread in the R4.3 style, e.g.
   `-# _Couldn't merge develop into this branch: conflicts in a.py, b.py._`
   The turn then proceeds on the stale branch. Resolving the conflict is
   out of scope for the agent, which has no git; the owner does it on
   GitHub or locally, and the next turn's merge picks it up.
7. Neither the merge nor a conflict ever blocks recovery or drops the
   owner's message.

### R4. Eviction is allowed but invisible

1. Local materializations may still be swept on any schedule, or never.
   The sweep may not remove identity records.
2. Recovery is automatic and requires no action from the owner. It runs
   inside the same turn as the message that triggered it, under a lock
   that admits one recovery per conversation, with the typing indicator
   showing. (Branch and PR recovery comes before the conversation exists
   and so before its lock, on a recovery lock of its own; thread
   reconstruction runs under the conversation lock.)
3. The owner sees at most one short status line about recovery in the
   thread. Silent recovery is acceptable when it succeeds losslessly. The
   line is a Discord subtext heading in italics, so it reads as muted
   harness chatter rather than an agent reply:

   ```
   -# _Reloading thread history. Some context might be lost._
   ```

   Use that exact wording when thread reconstruction (R2c) is the source.
   A line for a successful backup restore, if shown at all, uses the same
   style, e.g. `-# _Resuming from saved transcript._`
4. If recovery fails, the thread gets a message stating that it failed and
   why (per the project's fail-fast rule). The message is never ignored.

### R5. Identity records are durable

1. The identity record is written to persistent storage on every change
   and is never dropped because a checkout directory is missing. (Today's
   startup loader drops such entries; that behavior goes away.)
2. Loss of the identity store as a whole is survivable: R1, R2b.4, and
   R3.3 make every conversation recoverable from Discord, GitHub, and the
   backup store alone.
3. Conversations started under v0 (including the 2026-08-16 thread) are
   covered. Nothing in the record format may be required that v0 threads
   lack.

### R6. Concurrency and restarts

1. Existing behavior stays: conversations run in parallel, turns of one
   conversation queue in order.
2. A message that arrives while its conversation is recovering waits its
   turn; it is not dropped and does not trigger a second recovery.
3. If the bot restarts mid-turn, the interrupted turn is lost but the
   conversation is not. The next message in the thread runs normally with
   the interrupted prompt visible in the reconstructed history.
4. Messages posted while the bot was offline are not lost. On startup the
   bot finds every agent thread with an owner message newer than the
   bot's last message in that thread, and runs those messages as turns, in
   order, as if they had just arrived. Several unanswered messages in one
   thread are queued in order (R6.1). This catch-up is a background job
   that must not delay the bot coming online for everything else, and a
   failure in one thread is reported in that thread and does not stop the
   others.
5. Catch-up does not depend on local state: the set of agent threads is
   discovered from Discord (R1.2), including archived threads, so it works
   on a wiped host.

## Non-requirements

- Lossless context for v0 threads, which have no transcript backup. Thread
  reconstruction is the best available for them.
- Keeping worktrees or local transcript copies around. They are a cache.
- Bounding disk, clone count, or GitHub API usage.
- Conversations outside bot-created threads (DMs, pings in channels)
  gaining new durability beyond what they have today.

## Acceptance scenarios

Each must pass with no owner intervention beyond posting the message.

1. **The original bug.** Start a conversation that opens a PR. Remove its
   entry from the conversations table and delete its checkout, including
   the local transcript. Post a follow-up in the thread. The bot replies,
   references specifics from the earlier turns including a file it read
   but did not edit, and pushes further commits to the same PR.
2. **Wiped host.** Same as 1, but delete the whole conversations directory,
   the pristine clones, and the SDK projects directory. Still passes, via
   the transcript backup.
2b. **Lost backup.** Same as 1, but also delete the transcript backup. The
   bot still replies with the thread's history, the thread shows the
   muted "Reloading thread history" line from R4.3, and a fresh backup
   exists after the turn.
3. **Archived thread.** Let the thread auto-archive (or archive it by hand),
   then post. Still passes.
4. **No PR yet.** A conversation whose turns were only questions. After
   eviction, post a request that edits code. A branch and PR are created
   and the reply reflects the earlier questions.
5. **Merged PR.** Merge the conversation's PR, then post a follow-up that
   edits code. A new branch and PR appear and the reply says why.
6. **Images.** Earlier turns included image attachments. After eviction the
   agent can still describe those images when asked.
7. **v0 thread.** Post in the thread started 2026-08-16. Passes scenario
   2b, since no backup exists for it.
8. **Recovery failure.** Make GitHub unreachable, then post in an evicted
   thread. The thread receives an error message naming the cause.
9. **Concurrent messages.** Post two messages in quick succession into an
   evicted thread. Both are answered, in order, with one recovery.
10. **Offline messages.** Stop the bot. Post one message in each of two
    agent threads, one of them archived. Start the bot. Both threads get
    answers without any further owner action.
11. **Stale branch.** Land an unrelated commit on the base branch, then
    post a follow-up. The turn's commit sits on top of a merge of the base,
    and the PR shows no conflicts.
12. **Conflicting base.** Land a commit on the base that conflicts with the
    branch, then post a follow-up. The thread shows the muted merge line
    naming the conflicting files, the turn still completes and pushes, and
    the branch has no half-finished merge state.

## Implementation notes

What the build settled that the requirements above left open, and where it
deliberately differs from their wording. The requirements themselves stand
as written.

- **The backup is the SDK's session store, not a copy taken after the
  turn.** R2b.1's "copied at the end of every turn" and R2b.3's "restore
  places the file exactly where the SDK will look for it" are realized as
  a store-backed resume: `ClaudeAgentOptions.session_store` writes each
  batch into the backup repo as the turn runs, and a resume is rebuilt
  from what the store returns rather than from a restored local file.
- **Once the store holds a session, this host's transcript stops being
  updated** (measured against SDK 0.2.110), so the store is the sole
  record of that conversation rather than a mirror of one. The first
  backup-enabled turn therefore seeds the store from the local transcript
  first, so enabling the backup on an existing conversation continues it
  instead of freezing or discarding what this host already had.
- **A conversation that has run with the store refuses to run when the
  backup repo is unavailable**, rather than falling through to a thread
  rebuild. This is deliberate: R2.4's fall-through is for a transcript
  that is missing, not for a store that is momentarily unreachable, and
  falling through would let one transient failure permanently downgrade a
  lossless conversation to a lossy summary and strand its backup under the
  old session id. Conversations that never used the store still run from
  their local transcript.
- **Backup layout**, keyed by thread id, the one identifier Discord
  guarantees: `<thread_id>/transcript/<session_id>.jsonl` and
  `<thread_id>/identity.json`.
- **Idle eviction is gone entirely**, taking R4.1's "or never". Nothing
  sweeps materializations; worktrees are simply rebuilt when a turn finds
  them missing.
- **A turn interrupted after it started narrating is not replayed** by
  the startup catch-up: its progress messages are indistinguishable from
  an answer, so the message counts as answered (R6.3). A turn that died
  leaving only muted harness lines does count as unanswered and is
  replayed (R6.4).
- **Catch-up covers guild threads only** — where conversations live. A
  ping in a channel with no thread, or a DM, keeps the durability it
  always had.
- **Catch-up runs on every fresh gateway session, not only at process
  start.** It hangs off `on_ready`, which fires on each IDENTIFY and never
  on a RESUME, so exactly the reconnects that could have missed messages
  get a catch-up.
- **Pull request bodies carry `Discord thread: <id>`**, so R3.3's GitHub
  search matches a thread to its pull request exactly for every PR opened
  from this version on. The first-commit-message match remains, for pull
  requests opened before it.
- **Pull request state is the harness's business, not the model's**, which
  is why R2.3's git context stops at the branch. The harness holds the
  pull request urls and their open/merged/closed state, acts on them
  (R3.4's restart), and reports them in the thread; nothing about them is
  passed to the model. A turn rebuilt from the thread does see the
  announcements and the R3.4 line, as harness facts — which is how such a
  turn knows a pull request exists at all.
- **A store the first backed-up turn cannot seed fails the turn**, for the
  same reason `_require_backup` refuses a turn whose store is unreachable:
  carrying on would rebuild from the thread and leave the local transcript
  behind under an id nothing will resume again.
- **The backup clone and the pristine repo clones are retried on the next
  turn**, not only at startup. A clone that failed at boot, or a directory
  deleted while the bot ran, is re-made by the readiness check every turn
  passes through, so R4.2's "no action from the owner" survives a
  transient GitHub failure without a restart. A backup clone that keeps
  failing is tried at most once a minute, so an outage does not cost every
  turn a network clone.
