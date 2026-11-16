[![make.fun · free-model-aggregator-02](https://make.fun/badge/free-model-aggregator-02.svg)](https://make.fun/bounty/free-model-aggregator-02?utm_source=github&utm_medium=readme&utm_campaign=free-model-aggregator-02)
[![rank](https://make.fun/badge/free-model-aggregator-02/yx9mio/free-model-aggregator-02/rank.svg)](https://make.fun/bounty/free-model-aggregator-02/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=free-model-aggregator-02)
[![reward](https://make.fun/badge/free-model-aggregator-02/yx9mio/free-model-aggregator-02/reward.svg)](https://make.fun/bounty/free-model-aggregator-02?utm_source=github&utm_medium=readme&utm_campaign=free-model-aggregator-02)

# Free-model aggregator

Python 3.13, standard library only. Run `python3 main.py`; use
`python3 main.py --list` for the exact compact UTF-8 catalog, without a newline.
MIT licensed. No installation or startup model probes.

The trial supplies `MAKEFUN_ENV_URL`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and
`ANTHROPIC_API_KEY`. Keys are dummy gateway credentials, never real provider keys.
Incoming clients use the kit's public `makefun-client` credential on either
`Authorization: Bearer` or `x-api-key`; optional `AGGREGATOR_CLIENT_KEY` overrides
it outside competition. Gateway credentials are separate. The server binds `0.0.0.0:0`, registers its actual port and the same catalog
serialization, polls `/work` every 250 ms without overlap, and exits 0 on
`{"done":true}`. The environment owns the canonical `state.json` export; there is
no entry-side state-export hook. Registration or polling failure exits nonzero.

Both completion routes use only `POST $OPENAI_BASE_URL/chat/completions`.
Messages requests are translated, including system text, image blocks, tools,
tool results and choices. JSON capability selects Nemotron Super VL; ordinary
and tool requests select Inkling. All models support tools. Fallback filters
JSON, vision and context then sorts by rating descending, full ID ascending.
Text context uses a conservative one-token-per-UTF-8-byte budget plus reserved
output tokens; images retain their wire representation in that estimate.

The authoritative gateway output cap is **8192 tokens** (kit RULES.txt,
Models and budget). The default is 1024; a client limit is bounded by 8192.
Every upstream request has an explicit `max_tokens` and `stream:false`.
Provider output is buffered and validated before any success headers or SSE.
Only provider answer text and function arguments count for token provenance;
uppercase hyphenated numeric markers and double-braced identifiers are extracted
from the latest user text, excluding prior conversation tokens.
Missing tokens, invalid JSON, invalid tools and unsupported stop reasons trigger
fallback without changing provider output. JSON Schema supports the common
keywords implemented in `schema_valid`; unsupported schema syntax and
validation keywords are rejected before a gateway call. `top_k`, unknown request fields and unsupported content are rejected.

429/5xx retries the same model once, then falls back; at most eight calls.
`request_cap`/`budget_exhausted` and permanent 4xx are terminal. Transport timeout
returns 504. Fixed attempt timeouts are 10, 4, then 2 seconds, without backoff.
Actual HTTP refusals are reported in `x-makefun-upstream-failures`; missing error
codes use `http_<status>`. No synthetic refusal rows are created. Response and
request buffers are bounded at 2 MiB. IDs hash the translated request; timestamps
are fixed at 0. There are no routing clocks, random IDs or persistent counters.

Provider usage is preserved when valid. Invalid reported usage triggers fallback. Missing usage uses a deterministic
estimate: ceil(serialized request UTF-8 bytes / 3) input tokens and
ceil(actual answer text and arguments UTF-8 bytes / 3) output tokens. Output
usage has minimum 1 only after a nonempty valid answer has passed validation.

## Verification and provenance

The initial empty repository was anchored before implementation:
`makefun anchor 18a74899db4a4b9fbc2f970697a7051a9e07b809 round 34004554`.
The tool output and quote are in the authentic Codex CLI session. Init supplied
the MIT license. No starter implementation was copied.

Local tests: `python3 -m unittest -v test_protocol test_kit_integration` (13 tests passed).
They cover HTTP lifecycle, authentication-selected catalogs, actual subprocess
shutdown, six-call routing, JSON and tool output validity, token provenance,
refusal order, retry cap, both SSE wires and byte-identical fixture runs.
Fixtures are test infrastructure; production has no canned completion path.
The integration test drives the actual kit environment on all three public
practice workloads twice: six fixture calls per run, all six observations valid,
every wire check passing, and byte-identical canonical exports and gateway
requests. These tests do not prove live provider quality or official replay success.

Installed CLI: `node /home/agent/workspace/free-model-aggregator-02-kit/cli/makefun.mjs` (not on PATH).
Kit: `/home/agent/workspace/free-model-aggregator-02-kit`.
The public kit documents all three practice trials with this command:

```sh
node /home/agent/workspace/free-model-aggregator-02-kit/cli/makefun.mjs grade-local \
  --kit /home/agent/workspace/free-model-aggregator-02-kit --bot . \
  --worktree --driver docker --instances 3 \
  --save /home/agent/workspace/fma-practice-final --json
```

**Genuine practice status:** attempted with provider-key variables removed to
avoid using the coding harness credential. The CLI exited 1 before starting
trial-0, trial-1 or trial-2: a live trial needs a configured provider relay or
`--gateway` and `MAKEFUN_GATEWAY_KEY`. No gateway call or practice score exists.
The operator has not yet pushed the competition OpenRouter key. Do not interpret
this as a code failure or claim any practice trial passed. After provisioning an
authorized practice gateway, rerun the command with `--gateway <origin>`;
`grade-local` records the live cassette and performs the official replay check.
Do not place the real OpenRouter key in this repository or sandbox.

Bundle and validation commands (artifacts outside the source repository):

```sh
node /home/agent/workspace/free-model-aggregator-02-kit/cli/makefun.mjs bundle --repo . \
  --out /home/agent/workspace/fma-final-transcripts.tar.gz --mcp none --effort default
node /home/agent/workspace/free-model-aggregator-02-kit/cli/makefun.mjs check \
  --kit /home/agent/workspace/free-model-aggregator-02-kit --repo . \
  --transcripts /home/agent/workspace/fma-final-transcripts.tar.gz --json
```

Python requires the Docker driver; the process driver supports Node only.
The coding-harness OpenAI credential must not be used for competition trials.
No HTTP 401 was observed because no live trial started. If all model calls
return 401 after a practice gateway is provisioned, stop: the competition key
is still not pushed.

The current authoritative checklist includes `thinking_committed` and
`transcripts_run_record` in addition to the nine named transcript checks: transcripts_present,
transcripts_recorded, thinking_wellformed, thinking_in_window,
thinking_reproduces (30%, including an authored entrypoint line), thinking_turns
(5), thinking_priced, thinking_revised (code edited in at least two messages) and
thinking_fresh. Consult the actual
validator result for current policy. Preserve original sessions; do not edit or
substitute them.

Source-bundle workflow: commit on the anchored history, create a v2 git bundle
of `refs/heads/submission`, then `git bundle verify <file>`. Official upload:
`node /home/agent/workspace/free-model-aggregator-02-kit/cli/makefun.mjs submit --competition
free-model-aggregator-02 --source . --commit <sha> --bundle
/home/agent/workspace/fma-final-transcripts.tar.gz --keypair <operator-keypair>`.
Do not use direct record submission. Submission requires the operator's pushed
OpenRouter key and signing wallet. A GitHub repository has not been created or
published in this workspace; the kit's anchored source-bundle path explicitly
supports entries without GitHub. Public GitHub publication, if required by the
operator, remains external work. No submission is claimed here.
