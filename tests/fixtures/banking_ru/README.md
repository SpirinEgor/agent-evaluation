# Banking API test fixtures

These self-contained inputs exercise shared banking APIs without loading task
contracts, the current benchmark corpus, or archived experiment snapshots.
Their records represent only test scenarios; they do not establish validity
of any benchmark task or model result.

- `operation_api/`: compensation, case documents, delivery and fee operations.
- `investigation_api/`: investigation creation and idempotency.
- `settlement_api/`: settlement and reward adjustment protocols.
- `reward_api/`: client statements, discovery and formal reward review.

Each fixture has its own DB, minimal corpus and compact manifest. Hash checks
cover the DB, intake answers and every corpus file. Tests cover independence
from the active benchmark and detection of modified fixture data.

`scripts/banking_ru/run_basic_tests.py` lists the maintained offline tests
explicitly; collecting or passing those tests does not validate every task.
Historical run results remain in their original reports.
