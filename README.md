# aio-automerge-pilot

Public, throwaway pilot for tiered auto-merge (plan A+). No secrets in code, no deploy, no real data.

- `ci` (`.github/workflows/ci.yml`): unit tests, required check `test`.
- `risk-gate` (`.github/workflows/risk-gate.yml`): trusted gate posted by the bc-risk-gate App.
  Low passes; Medium passes only with a `Cursor Bugbot` success on the head SHA; High waits for the owner.
- `post-merge-watch`: on a main CI failure, opens a diagnosis issue and at most a revert PR, never auto-merged.

Everything under `.github/**` is High risk: only the owner can approve changes to it.
