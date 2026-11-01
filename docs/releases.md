# Releases

The pack follows dirigent's version. A release of dirigent is a release here: the pack relocks
its dirigent packages at the tagged commit, moves to the same number, and is tagged `vX.Y.Z`
with a GitHub release of its own. So `dirigent-fhir` 0.13.0 is the pack built against dirigent
0.13.0, and the two numbers are never read as independent.

Every release, with its notes, is on
[the pack's releases page](https://github.com/winterop-com/dirigent-fhir/releases). What
changed in the engine under it is in
[dirigent's release notes](https://winterop-com.github.io/dirigent/releases/).

An instance installs a matching pair: the pack's version and the dirigent it was released
with. A pack built against an older dirigent is not supported, because the plugin contract
changes directly before 1.0 rather than through compatibility shims.
