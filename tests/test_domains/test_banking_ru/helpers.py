from tau2.domains.banking_ru.environment import get_environment, get_tasks
from tau2.platform.session import PlatformSession


def task(number):
    return next(t for t in get_tasks() if t.id == f"Bank {number}")


def execute(number, actions):
    session = PlatformSession(task(number), get_environment)
    session.replay_actions(actions)
    session.call("done", {})
    return session
