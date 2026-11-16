# Verification evidence

2026-11-16, authentic commands captured in the Codex CLI sessions.

- `python3 -m unittest -v test_protocol`: 9 tests passed. Controlled fixture
  gateway only; no live provider proof or competition score claimed.
- Fixture subprocess test runs twice: all six requests route with six calls,
  catalog wire ID sets match registration, streams validate, process exits 0,
  and captured results and gateway requests are byte-identical. It also checks
  400/401/404/405 envelopes before any model call.
- `grade-local --instances 3 --driver process --worktree` with provider-key
  variables removed: exit 1, before any trial starts. Exact diagnostic:

  > makefun grade-local: a live trial needs a model to call: set ANTHROPIC_API_KEY or OPENAI_API_KEY (your own key: a relay on this machine hands the forwarder's calls to that provider), or give masq's origin with --gateway <url> and its key in MAKEFUN_GATEWAY_KEY; a kept trial's replay check runs alone, at no cost, with --replay <cassette>

| Official practice | Status |
|---|---|
| trial-0 | Not run: authorized provider relay/gateway unavailable |
| trial-1 | Not run: same prerequisite |
| trial-2 | Not run: same prerequisite |

The operator's competition OpenRouter secret is pending. No 401 provider run
was observed, and no score, gateway proof, or official replay result is invented.
Raw command logs and generated transcript/source artifacts are outside the
repository under `/home/agent/workspace/fma-*`.

The first `makefun check --worktree --transcripts ... --json` returned `ok:true`,
no failed checks, 98.3% reproduced lines, recorded/priced transcripts, and the
revision/turn checks passed. Worktree mode skips beacon verification; the final
committed-snapshot check must also validate the anchor. Public GitHub status and
account ownership are submission-side checks. No remote repository or submission
has been made.

Final committed-snapshot validator: `ok:true`, `failed:[]`. Beacon history
creation and `thinking_fresh` both passed, with 699/711 counted lines reproduced
(98.3%). `git bundle verify /home/agent/workspace/fma-source.bundle` reported
`is okay`, complete history, SHA-1, and `refs/heads/submission`. The transcript
bundle is `/home/agent/workspace/fma-transcripts.tar.gz`; raw validator JSON is
`/home/agent/workspace/fma-check.json`. These are local validation results,
not a submission receipt or a successful provider trial.

## Review verification (supersedes earlier artifacts)

- `python3 -m unittest -v test_protocol test_kit_integration`: 13 tests passed.
- The actual public kit environment runs every practice workload twice against
  a controlled gateway: six calls per run, all six responses valid and every
  wire check passing, with byte-identical canonical exports and requests.
  These are protocol fixtures, not real-provider scores or official replay.
- Fixed the client authentication mismatch: environment requests use public
  `makefun-client`, independently of upstream dummy gateway credentials.
- Hardened unsupported-field/schema validation, tool history, current-turn token
  scope, double-braced markers, malformed-provider fallback, and usage checks.
- Attempted `grade-local --driver process`: tooling refused before trial startup;
  Python requires Docker. No provider request was made.
- Corrected `grade-local --driver docker --instances 3` with coding-harness keys
  removed: tooling refused before startup because no authorized provider relay
  or practice gateway is configured. The exact diagnostic is quoted above.
- Official trial-0, trial-1, and trial-2 remain **not started**. The entrant's
  competition OpenRouter key is not pushed. No HTTP 401 was observed, no live
  score is claimed, and no real key was introduced into the sandbox.
- `gh auth status`: no GitHub host login; no remote or submission was created.
- Final source/transcript artifacts and validator report use `fma-final-*` under
  `/home/agent/workspace/`; earlier `fma-*` artifacts are superseded.
