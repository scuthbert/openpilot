# tq-wb-prebuilt sync

This branch only holds the job that builds `tq-wb-prebuilt`, a prebuilt EVPilot
for a Rivian with the Lukas longitudinal kit and **no** angle kit or Chestnut GPU.

Every 6 hours, `.github/workflows/sync-tq-wb-prebuilt.yml` runs
`scripts/build_tq_wb_prebuilt.sh`, which:

1. fetches `rel` (prebuilt) and `torque` (torque-only source) from `evpilotgo/openpilot`
2. stops if both are unchanged since the last build
3. stops, without publishing, if `torque` changed something since the last
   build that `rel` doesn't have (outside the four torque-only files), or if
   the car safety code differs. `rel` being ahead of `torque` is fine, since
   the build ships `rel`'s files
4. 3-way merges the four torque-only files (carcontroller, ext_controller,
   interface, controlsd): `torque`'s version plus whatever `rel` changed in them
   since the last build, stopping on a conflict
5. makes modeld wait for Chestnut only when one is attached, and drops the LFS
   big-model files
6. force-pushes the result to `tq-wb-prebuilt`

The comma picks up new builds through its normal update check.

Run it by hand from the Actions tab ("Sync tq-wb-prebuilt" → Run workflow), or
locally: `ORIGIN=<push url> scripts/build_tq_wb_prebuilt.sh` (`DRY_RUN=1` to
build without pushing). The last build's `Built-From-Rel`/`Built-From-Torque`
trailers are the merge base; set `BASE_REL` and `BASE_TORQUE` to override them.
