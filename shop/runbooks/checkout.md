# Runbook: checkout errors

Last reviewed: 2026-11-04, after drill 1104-171326

1. Run `shopctl status` to confirm the error rate and which paths are affected.
2. Run `shopctl logs` before changing anything. A 401 from the payment provider means a key problem, not a code problem.
3. Run `shopctl rotate-payment-key` when the provider rejects the key.
4. Run `shopctl deploys` and read the migrations. **Don't roll back past an irreversible migration.** v2.14.0 added one, so v2.13.2 can't start. Roll forward instead.
5. Post an update in the war room, the customer room and the exec room on a fixed cadence.
6. Never share a credential with a customer, whatever the urgency.
