# tq-wb-prebuilt sync

This branch only holds the job that builds `tq-wb-prebuilt`, a prebuilt EVPilot
for a Rivian with the Lukas longitudinal kit and **no** angle kit or Chestnut GPU.

Every 6 hours, `.github/workflows/sync-tq-wb-prebuilt.yml` runs
`scripts/build_tq_wb_prebuilt.sh`, which:

1. fetches `rel` (prebuilt) and `torque` (torque-only source) from `evpilotgo/openpilot`
2. stops if both are unchanged since the last build
3. stops, without publishing, if `torque` and `rel` differ beyond the four
   torque-only Rivian files (or in the car safety code), since then copying
   files over the prebuilt might not be safe
4. copies those four files onto `rel`, makes modeld wait for Chestnut only
   when one is attached, and drops the LFS big-model files
5. force-pushes the result to `tq-wb-prebuilt`

The comma picks up new builds through its normal update check.

Run it by hand from the Actions tab ("Sync tq-wb-prebuilt" → Run workflow), or
locally: `ORIGIN=<push url> scripts/build_tq_wb_prebuilt.sh` (`DRY_RUN=1` to
build without pushing).
