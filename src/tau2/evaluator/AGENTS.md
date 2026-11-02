# AGENTS.md — src/tau2/evaluator/

## Banking.ru: концепт и реализация — 27 октября 2026

Перед изменением банковской оценки прочитай [концепт](../../../docs/banking-ru.md),
[механику и статус миграции](../../../docs/banking-ru/evaluation.md) и
[процесс разработки](../../../docs/banking-ru/development.md).
Целевой балл — DB × ACTION; текущие профили определяются критериями задач.
Не отключай ENV-защиты немигрированных задач без переноса и проверки покрытия.
Поддержку других доменов сохраняй.

DB проверяет конечное состояние, включая объявленные поля вне семантического
хеша. ACTION проверяет необходимые действия, существенные аргументы, полученные
актуальные источники до зависимых действий и совершённые нарушения процесса.
Обязательный источник не означает обязательную поисковую фразу или обход каталога.
Полный исходный журнал сохраняется для прав и фактического разглашения.
В целевом контракте отклонённая попытка без раскрытия данных и запрещённого
эффекта не снижает балл; текущая проверка доступа ещё требует миграции.
Не представляй это решение как уже реализованное поведение.

По решению 2 ноября 2026 отсутствие агентского done обнуляет DB и ACTION;
финальная проза не требуется. Общие правила ниже не переопределяют эти
банковские решения. Документацию обновляй вместе с изменением оценки.

## Overview

The evaluator module scores simulation runs. Each evaluator is a classmethod-only class that takes a task and trajectory, and returns a `RewardInfo`.

## Architecture

### Evaluator Types

| Evaluator | What it checks | `RewardType` |
|-----------|---------------|-------------|
| `EnvironmentEvaluator` | DB state matches gold after replaying actions | `DB`, `ENV_ASSERTION` |
| `ActionEvaluator` | Agent called the right tools with right args | `ACTION` |
| `CommunicateEvaluator` | Agent said required information to user | `COMMUNICATE` |
| `NLAssertionsEvaluator` | LLM-judged natural language assertions (WIP) | `NL_ASSERTION` |

### Half-Duplex vs Full-Duplex Variants

Every evaluator has TWO variants:

- `FooEvaluator(EvaluatorBase[Message])` — works on `list[Message]` (half-duplex)
- `FullDuplexFooEvaluator(EvaluatorBase[Tick])` — works on `list[Tick]` (full-duplex)

Full-duplex evaluators convert `Tick` lists to `Message` lists internally before evaluating. Each has a `ticks_to_message_history()` classmethod that handles this conversion. The conversion logic differs per evaluator:
- **Environment**: extracts tool calls in execution order (user first, then agent per tick)
- **Communicate**: merges agent chunks by overlapping `utterance_ids`
- **NL Assertions**: uses containment-aware linearization from the streaming module
- **Action**: extracts all tool calls (user + agent) from ticks

### Composition via `evaluate_simulation()`

The `evaluate_simulation()` function in `evaluator.py` is the main entry point. It:
1. Selects half-duplex vs full-duplex evaluator variants based on `CommunicationMode`
2. Runs the appropriate evaluators based on `EvaluationType`
3. Combines rewards multiplicatively based on the task's `reward_basis`

### `EvaluationType` Controls What Gets Evaluated

- `ALL` — runs ENV + ACTION + COMMUNICATE, but only includes each in the final reward if it's in the task's `reward_basis`
- `ALL_IGNORE_BASIS` — runs all three and always multiplies them together
- `ENV`, `ACTION`, `COMMUNICATE` — run a single evaluator
- `NL_ASSERTIONS`, `ALL_WITH_NL_ASSERTIONS` — include NL assertions (WIP)
- `ALL_WITH_NL_ASSERTIONS_IGNORE_BASIS` — like `ALL_IGNORE_BASIS` but also includes NL assertions (WIP)

## Rules When Modifying This Module

1. **Always maintain both variants**: If you change an evaluator, update BOTH the half-duplex and full-duplex versions.

2. **`calculate_reward` is a classmethod**: All evaluators use `@classmethod`. No instance state.

3. **Reward is multiplicative**: The combined reward is the product of component rewards. A 0 in any component zeroes the total.

4. **Handle missing criteria gracefully**: If `task.evaluation_criteria` is `None` or a specific criterion is absent, return `reward=1.0` (not 0). Missing criteria means "not evaluated", not "failed".

5. **Termination depends on profile**: The ordinary path returns 0 for termination outside `AGENT_STOP`/`USER_STOP`. Banking requires AGENT_STOP even in `business_process_only`; missing agent done zeros DB and ACTION. Other domains retain their profile behavior.

6. **`evaluation_criteria.actions` is *one* reference trajectory, not a per-action requirement**: `EnvironmentEvaluator` replays `actions` on a fresh env to derive the target DB hash, then compares against the predicted DB hash. Other agent trajectories producing an equivalent end state also pass. ACTION matches declared required actions (`required_for_reward=True`) when that component is enabled; optional reference steps may still supply separately required evidence. See [the shared evaluation guide](../../../docs/evaluation.md).

7. **Environment evaluator needs `environment_constructor` and `env_kwargs`**: Unlike other evaluators, `EnvironmentEvaluator.calculate_reward()` takes an `environment_constructor` callable to create fresh environments for gold vs predicted comparison. It also accepts `env_kwargs: dict` for domain-specific parameters (e.g., `retrieval_variant` for `banking_knowledge`). These kwargs are threaded from `evaluate_simulation()` through to both the predicted and gold environment constructors. Always pass `env_kwargs` when calling `calculate_reward()` to ensure domains with custom constructor parameters work correctly.

8. **NL assertions require LLM calls**: `NLAssertionsEvaluator` calls an LLM (configured via `DEFAULT_LLM_NL_ASSERTIONS` in `config.py`). This is experimental/WIP.

9. **Hallucination check**: `check_hallucination()` and `format_hallucination_feedback()` in `reviewer.py` are used by the runner's hallucination retry loop (full-duplex only). The underlying LLM judge is in `hallucination_reviewer.py`. See `tau2.runner.batch` and the `--hallucination-retries` CLI option.

Consider these rules if they affect your changes.
