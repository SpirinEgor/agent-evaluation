"""SDK task collector: hidden criteria remain in TaskDefinition.gold."""

from platform_sdk import TaskDefinition

from tau2.domains.banking_ru.environment import get_tasks


def collect() -> list[TaskDefinition]:
    definitions = []
    for task in get_tasks():
        raw = task.model_dump(mode="json")
        definitions.append(
            TaskDefinition(
                id=task.id,
                split="all",
                input={
                    "scenario": {
                        key: raw[key]
                        for key in ("id", "initial_state", "max_bank_calls")
                    },
                    "ticket": task.agent_ticket(),
                },
                gold={"evaluation_criteria": raw["evaluation_criteria"]},
            )
        )
    return definitions
