"""Author-only candidate construction and independent constraint search.

Search receives only public domains/conflicts, never the planted assignment.
Money optimum follows a separate per-group upper bound, not search heuristics.
"""

import random
import time


def construct(n=116, edges=314, seed=21102026):
    rng = random.Random(seed)
    planted = [rng.randrange(3) for _ in range(n)]
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    chosen = rng.sample(pairs, edges)
    conflicts = []
    for i, j in chosen:
        options = [
            (a, b)
            for a in range(3)
            for b in range(3)
            if (a, b) != (planted[i], planted[j])
        ]
        conflicts.extend((i, a, j, b) for a, b in rng.sample(options, 3))
    return {"groups": n, "conflicts": conflicts}, planted


def valid(problem, assignment):
    return (
        len(assignment) == problem["groups"]
        and all(v in range(3) for v in assignment)
        and all(
            not (assignment[i] == a and assignment[j] == b)
            for i, a, j, b in problem["conflicts"]
        )
    )


def solve(problem, max_nodes=1000000, seconds=30):
    n = problem["groups"]
    neighbors = [set() for _ in range(n)]
    forbidden = {}
    for i, a, j, b in problem["conflicts"]:
        neighbors[i].add(j)
        neighbors[j].add(i)
        forbidden.setdefault((i, j), set()).add((a, b))
        forbidden.setdefault((j, i), set()).add((b, a))
    nodes = 0
    deadline = time.monotonic() + seconds

    def propagate(domains, queue):
        while queue:
            i, j = queue.pop()
            bad = forbidden[i, j]
            remaining = {
                a for a in domains[i] if any((a, b) not in bad for b in domains[j])
            }
            if not remaining:
                return False
            if remaining != domains[i]:
                domains[i] = remaining
                queue.extend((k, i) for k in neighbors[i] if k != j)
        return True

    def search(domains):
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or time.monotonic() > deadline:
            raise TimeoutError("author solver budget reached; no difficulty claim")
        choices = [i for i in range(n) if len(domains[i]) > 1]
        if not choices:
            return [next(iter(d)) for d in domains]
        i = min(choices, key=lambda x: (len(domains[x]), -len(neighbors[x]), x))
        for a in sorted(domains[i]):
            trial = [set(d) for d in domains]
            trial[i] = {a}
            if propagate(trial, [(j, i) for j in neighbors[i]]):
                result = search(trial)
                if result is not None:
                    return result
        return None

    start = time.monotonic()
    domains = [set(range(3)) for _ in range(n)]
    result = search(domains) if propagate(domains, list(forbidden)) else None
    return result, {"nodes": nodes, "seconds": time.monotonic() - start}
