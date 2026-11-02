"""Versioned solver profiles, separate from the banking environment."""

from tau2.platform.agents import v1


def profile(name):
    if name == "baseline":
        return None
    if name == "v1":
        return v1
    raise ValueError(f"Unknown agent profile: {name}")


def prompt_for(text, name="baseline"):
    agent = profile(name)
    return (
        text
        if agent is None
        else text.replace(
            "никаких других инструментов или источников.",
            "внешних источников. Локальный workbench разрешён для заметок и расчётов.",
        )
        + "\n\n"
        + agent.INSTRUCTIONS
    )
