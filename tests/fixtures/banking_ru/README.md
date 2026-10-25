# Banking test inputs and maintained inventory

`run_basic_tests.py` names its offline inventory explicitly: 61 reference/noop
pairs, shared tool contracts, evaluator mechanisms, platform/corpus boundaries,
and representative shared banking mechanisms. New files require an explicit
inventory decision; they cannot silently extend this command through a glob.
This inventory is a reproducible entry point, not evidence that every task or
historical experiment has been validated.

Historical Bank2 version migrations, optimization/menu/stress experiments,
source receipt audits and per-version route matrices remain available by their
pytest paths, outside the basic command. Their version-specific sums, original
three-component grades and procedural requirements are not assertions about the
current release. They are not deleted, skipped, or reported as passing. No full
historical regression was run for this change. The retained resolution APIs,
selection oracle, certificates, dependencies, funding and promotion controls,
settlement/replay protocols, whole historical restoration control, current
catalog references/noop and evaluator checks preserve their distinct general
mechanisms; this does not establish equivalence to every excluded experiment.

Historical fixture construction starts with `fees_rewards_parent_2026_10_14`,
then applies versioned subject/source overlays. The base includes every corpus
file named by its manifest. Missing corpus files were recovered by their exact
manifest SHA256 from existing historical snapshots, versioned subject fixtures
and unchanged sources. Neither construction nor runtime reads today's corpus.
The v5 overlay additionally pins the 26 source descriptors/files differing
between that base and `bank2-hardening-2026-10-20/parent-data`. These are author
fixtures, never model input or a validation claim about production tasks.

`reward_workflow_2026_10_21` is a small isolated API fixture derived from v25;
see its README for retained rows and intentional component reduction. It replaces
runtime access to ignored v24r/v25 artifact snapshots. The settlement-v3 subject
fixture is applied over the same pinned base, not the current DB.

Each fixture's `integrity.json` pins all executable input bytes (DB, tasks,
answers, manifest, corpus and overlays). Loaders check it before copying or
reading. Corpus construction independently checks each article against its
manifest hash. README provenance and integrity manifests are versioned with the
input they describe. Intentional fixture updates must update both input and its
hash manifest; hash verification is integrity detection, not authenticity proof.
Generated evidence belongs under pytest's temporary directory.
