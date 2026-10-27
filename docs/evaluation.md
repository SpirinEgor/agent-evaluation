# Task Schema and Evaluation

This page explains how a task is scored in τ-bench and — most importantly —
**what `evaluation_criteria.actions` actually does**. If you have looked at
`data/tau2/domains/airline/tasks.json` and assumed those listed actions are
required of the agent, this page is for you.

## Banking.ru contract

For development of our `banking_ru` domain, the accepted scoring contract is
`DB × ACTION`: final bank state and valid business execution. See
[the banking development guide](banking-ru.md) for the normative rules and concrete task examples. COMMUNICATE and NL_ASSERTION
are not part of the banking score.

The [banking evaluation guide](banking-ru/evaluation.md) records actual task profiles,
migration status and known differences from the agreed concept. Required current
sources must be received before dependent actions in the target contract; no
specific search path is required. Rejected calls with no disclosure or forbidden
effect are neutral in that target contract, but current access checks still
require migration. Terminal obligations outside semantic DB hashing belong to DB.
Removing legacy ENV guards requires transfer and coverage evidence.
ENV_ASSERTION remains supported by the shared framework for other domains.
The descriptions below cover general upstream mechanisms, not a requirement
to enable every component for banking_ru. reward_basis is task-specific.

## TL;DR

- A task's final reward is the **product** of the components listed in
  `evaluation_criteria.reward_basis`.
- The schema defaults `reward_basis` to `["DB", "COMMUNICATE"]`. Explicit task
  criteria take precedence; inspect the selected dataset version for each domain.
- `evaluation_criteria.actions` is **one reference trajectory** that
  solves the task — not the only correct one. It is replayed on a fresh
  "gold" environment to derive a target DB end state, which is then
  compared (by hash) to the predicted environment's DB end state. The
  agent is **not** required to take this specific path; any sequence of
  tool calls that produces an equivalent DB end state passes the DB
  check.
- The agent is required to match `actions` *only* when `RewardType.ACTION`
  is in `reward_basis` (used in a small subset of `banking_knowledge`
  tasks and in our banking extensions). ACTION checks the required actions
  and any declared alternatives, order, evidence and safety rules. The exact
  checks depend on the task and its action scope; not every reference action
  must be required.
- This was a deliberate design choice (see [issue #224][issue-224] and
  [RFC #129][issue-129] for the discussion). We surface this here so it
  is harder to miss.

[issue-224]: https://github.com/sierra-research/tau2-bench/issues/224
[issue-129]: https://github.com/sierra-research/tau2-bench/issues/129

## The task schema, in plain English

A task's `evaluation_criteria` (see
[`src/tau2/data_model/tasks.py`](../src/tau2/data_model/tasks.py)) includes these core
fields, alongside additional task-specific criteria:

| Field | What it is | When it gates the reward |
|-------|-----------|--------------------------|
| `actions` | A list of `Action`s recording **one** reference trajectory of tool calls that solves the task. Always replayed on a fresh "gold" environment to derive the target DB end state. Other trajectories may produce an equivalent end state and also pass. | ACTION can require the entries marked `required_for_reward`, subject to declared alternatives and the task's action scope. |
| `env_assertions` | A list of assertions to run against the predicted environment after the simulation. | Only when `RewardType.ENV_ASSERTION` is in `reward_basis`. |
| `communicate_info` | A list of strings the agent must say to the user (substring match). | Only when `RewardType.COMMUNICATE` is in `reward_basis`. |
| `nl_assertions` | Natural-language assertions judged by an LLM. | Only when `RewardType.NL_ASSERTION` is in `reward_basis`. (Experimental / WIP.) |
| `reward_basis` | The list of `RewardType`s that gate the final reward. | Always. Default is `[DB, COMMUNICATE]`. |

The reward components are evaluated by:

| `RewardType` | Evaluator | What it checks |
|--------------|-----------|----------------|
| `DB` | `EnvironmentEvaluator` | After the simulation, does the predicted environment's DB hash match the target DB hash (target = fresh env + replay of `evaluation_criteria.actions`)? Any agent trajectory that produces an equivalent end state passes. |
| `ENV_ASSERTION` | `EnvironmentEvaluator` | Do all `env_assertions` pass on the predicted environment? |
| `COMMUNICATE` | `CommunicateEvaluator` | Does every string in `communicate_info` appear in the agent's messages? |
| `NL_ASSERTION` | `NLAssertionsEvaluator` | Does an LLM judge return true for every entry in `nl_assertions`? (WIP) |
| `ACTION` | `ActionEvaluator` | Checks required actions and, when configured, their arguments, order, received evidence, access and other trajectory constraints. Exact behavior depends on the task and action scope. |

The final reward is the product. So if `reward_basis = [DB, COMMUNICATE]`,
the reward is `db_reward * communicate_reward`. `actions` is consumed
silently by `EnvironmentEvaluator` to set up the target environment, but
the agent's tool-call trajectory is never directly compared to it.

## Why `actions` looks like a requirement (and isn't)

`evaluation_criteria.actions` records *one* sequence of tool calls that
solves the task — typically how a task author or annotator solved it.
There is generally no claim that this is the only correct sequence, and
in many tasks several distinct trajectories produce an equivalent DB
end state (e.g., looking up a user before or after looking up a
reservation, or skipping a read-only lookup the agent doesn't actually
need). We keep this reference trajectory in the task because, for many
tasks, the target DB state is easier to express as "play these actions
on a fresh env" than to spell out by hand.

But because this list is also rendered to the agent in some debug views
and reads like a checklist, it is easy to misread as a list of *required*
agent actions. For a task scored only on DB and COMMUNICATE, scoring depends on
the predicted DB end state and the required strings. An agent that takes a
different (correct) path through the tools — or no tool calls at all when
the right answer is to refuse — can still receive full reward.

This is **intended**: the original τ-bench paper scores on outcomes
(`DB + COMMUNICATE`), not on whether the agent followed a particular
script. Switching the reward to gate on `actions` would break parity with
those numbers and would also penalize correct-but-different solutions.

## A worked example: airline task `1`

From [`data/tau2/domains/airline/tasks.json`](../data/tau2/domains/airline/tasks.json):

```json
{
  "id": "1",
  "evaluation_criteria": {
    "actions": [
      {
        "action_id": "1_0",
        "name": "get_user_details",
        "arguments": { "user_id": "raj_sanchez_7340" }
      },
      {
        "action_id": "1_1",
        "name": "get_reservation_details",
        "arguments": { "reservation_id": "Q69X3R" }
      }
    ],
    "communicate_info": [],
    "nl_assertions": ["Agent should not approve the cancellation."],
    "reward_basis": ["DB", "COMMUNICATE"]
  }
}
```

What this means in practice:

- `reward_basis = ["DB", "COMMUNICATE"]` → reward = `db_reward * communicate_reward`.
- `actions = [get_user_details, get_reservation_details]`. Both are
  **read-only** tools (per `tools.py` decorators), so replaying them on a
  fresh environment leaves the DB unchanged. The target DB hash
  therefore equals the *initial* DB hash.
- `db_reward = 1.0` iff the predicted DB hash also equals the initial
  hash, i.e., iff the agent did not write to the DB. The correct
  behavior here is to refuse the cancellation, so a correct agent
  produces no DB writes, the predicted hash equals the target hash, and
  `db_reward = 1.0`.
- `communicate_info = []` → `communicate_reward = 1.0` automatically
  (nothing required to be said).
- `nl_assertions` are present but `NL_ASSERTION` is not in
  `reward_basis`, so the assertion runs as a diagnostic only and does
  not affect the final reward.

Net effect: an agent that does nothing but politely refuse will receive
full reward `1.0`, even though it never called `get_user_details` or
`get_reservation_details`. That is the intended τ-bench scoring for this
task — but it is also why the field name `actions` and its old docstring
(`"actions that the agent should take"`) confused people. The actions
listed for task `1` document one valid information-gathering path that
solves the task; they are not a requirement on the agent, and an agent
that uses different read-only lookups (or skips them entirely) is
equally correct.

## How to inspect action correctness anyway

Even when `ACTION` is not in `reward_basis`, the `ActionEvaluator` can
still run for diagnostic purposes:

- The runner defaults to `EvaluationType.ALL`, which evaluates ACTION for
  diagnostics even when it is not in the reward basis; when evaluation runs,
  `action_checks` can be populated on `RewardInfo`,
  and `partial_action_reward` summarizes how many of the listed reference
  actions were matched. These are surfaced by `tau2 view` (look for
  `Partial Action Reward: m/n`) and by the metrics functions in
  `src/tau2/metrics/agent_metrics.py`. Note that this is a similarity
  signal against *one* reference trajectory, not a correctness verdict —
  an agent can score `0/n` on `partial_action_reward` and still be fully
  correct if it solved the task via a different sequence of tool calls.
- For diagnostics, `EvaluationType.ALL_IGNORE_BASIS` (or
  `ALL_WITH_NL_ASSERTIONS_IGNORE_BASIS`) multiplies ENV, ACTION and
  COMMUNICATE (and optionally NL) regardless of `reward_basis`. This is
  not the task's official score and ACTION can include more than reference
  matching, depending on the configured criteria. See
  [`src/tau2/evaluator/evaluator.py`](../src/tau2/evaluator/evaluator.py).
- The `partial_action_reward` property on `RewardInfo` further breaks
  down the action match rate by `ToolType.READ` vs `ToolType.WRITE`,
  which is useful for spotting "DB-passes-but-no-write-was-attempted"
  scenarios.

The official leaderboard score uses the task's `reward_basis` and is not
changed by these diagnostic options.

## When does `RewardType.ACTION` get used?

In the upstream bundled tasks, `ACTION` appears in `reward_basis` for a
small subset of `banking_knowledge` tasks, where
the *path* through specific knowledge-retrieval tools is the thing
being evaluated. Exact usage is version-dependent; inspect each task's
`reward_basis`. Our banking ACTION additionally checks safety and dependencies
without requiring a single literal reference trajectory.

If you build your own domain or task and want the agent's tool calls to
be a hard requirement (not just a side-effect on the DB), you can put
`RewardType.ACTION` in your task's `reward_basis`. Specify only genuinely required actions and their meaningful arguments.
Check that declared alternatives are accepted and that trajectory safety
rules do not reject a valid solution.

## Pointers

- Schema definitions: [`src/tau2/data_model/tasks.py`](../src/tau2/data_model/tasks.py)
- Evaluator source: [`src/tau2/evaluator/`](../src/tau2/evaluator/)
- Evaluator architecture overview: [`src/tau2/evaluator/AGENTS.md`](../src/tau2/evaluator/AGENTS.md)
- Reward shape (`RewardInfo`, `ActionCheck`, etc.):
  [`src/tau2/data_model/simulation.py`](../src/tau2/data_model/simulation.py)
- Discussion of the `actions`-vs-`reward_basis` confusion:
  [issue #224][issue-224] and [RFC #129][issue-129].
