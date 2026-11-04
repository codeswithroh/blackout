# Runbook: checkout errors

Last reviewed: 2025-08-12

1. Run `shopctl status` to confirm the error rate.
2. Run `shopctl deploys` to see what shipped recently.
3. If errors started after a deploy, run `shopctl rollback`.
4. Post an update in the war room every 10 minutes.
