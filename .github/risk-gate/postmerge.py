#!/usr/bin/env python3
"""post-merge-watch helper: diagnose a failed `ci` run on main.

  diagnose  -> writes diagnosis.md and outputs revert=true|false, sha, short, pr
  open-pr   -> opens the revert PR for an already pushed auto-revert/<short> branch

Never merges anything and never arms auto-merge. Env: GH_TOKEN (GITHUB_TOKEN),
GITHUB_REPOSITORY, GITHUB_EVENT_PATH, GITHUB_OUTPUT, OWNER_LOGINS.
"""
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate  # noqa: E402
from risk_gate import classify  # noqa: E402

ERR = re.compile(r"(error|fail(ed|ure)?|traceback|assert|exception|exit code [1-9])", re.I)


def summarize_log(text, tail=40, max_hits=30):
    lines = [re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ", "", l) for l in (text or "").splitlines()]
    hits = [l for l in lines if ERR.search(l)][:max_hits]
    return hits, lines[-tail:]


def revert_decision(parent_ci_conclusion, commit_tier, is_head_of_main):
    """Return (open_revert_pr, reason). Pure; unit tested."""
    if commit_tier == "high":
        return False, "merged change is High risk (possible workflow, data or deploy impact); owner decides, no automatic revert"
    if parent_ci_conclusion != "success":
        return False, "parent commit CI was %s; failure may be pre-existing or flaky, no revert" % (parent_ci_conclusion or "unknown")
    if not is_head_of_main:
        return True, "introduced by this commit (parent green); main has moved on, revert may need care"
    return True, "introduced by this commit (parent CI green)"


def _get_text(url, token):
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def cmd_diagnose():
    token, repo = gate._env("GH_TOKEN"), gate._env("GITHUB_REPOSITORY")
    with open(gate._env("GITHUB_EVENT_PATH"), encoding="utf-8") as fh:
        run = json.load(fh)["workflow_run"]
    sha, short = run["head_sha"], run["head_sha"][:12]
    owners = [o.strip() for o in gate._env("OWNER_LOGINS").split(",") if o.strip()]

    existing = gate._req("GET", "/repos/%s/issues" % repo, token,
                         params={"labels": "ci-failure", "state": "open", "per_page": 100})
    if any(short in (i.get("title") or "") for i in existing):
        gate._output(revert="false", sha=sha, short=short, pr="", skip="true")
        return 0

    jobs = gate._req("GET", "/repos/%s/actions/runs/%s/jobs" % (repo, run["id"]), token).get("jobs", [])
    failed = [j for j in jobs if j.get("conclusion") == "failure"]
    commit = gate._req("GET", "/repos/%s/commits/%s" % (repo, sha), token)
    parent = (commit.get("parents") or [{}])[0].get("sha", "")
    prev = gate._req("GET", "/repos/%s/actions/workflows/%s/runs" % (repo, run["workflow_id"]), token,
                     params={"branch": "main", "event": "push", "head_sha": parent, "per_page": 5})
    prev_runs = [r for r in prev.get("workflow_runs", []) if r.get("status") == "completed"]
    parent_concl = prev_runs[0]["conclusion"] if prev_runs else None
    tier = classify(commit.get("files") or [], None)
    main_head = gate._req("GET", "/repos/%s/branches/main" % repo, token)["commit"]["sha"]
    prs = gate._req("GET", "/repos/%s/commits/%s/pulls" % (repo, sha), token)
    pr_ref = ("#%s" % prs[0]["number"]) if prs else "(no PR found)"
    do_revert, why = revert_decision(parent_concl, tier["tier"], main_head == sha)

    body = ["CI `%s` failed on main at `%s` (merged from %s)." % (run["name"], sha, pr_ref),
            "Run: %s" % run["html_url"], "",
            "Owner: %s" % " ".join("@" + o for o in owners), "",
            "## Automatic diagnosis",
            "- Failed jobs: %s" % (", ".join(j["name"] for j in failed) or "none reported"),
            "- Parent `%s` CI on main: %s" % (parent[:12], parent_concl or "unknown"),
            "- Merged change risk tier: %s" % tier["tier"],
            "- Main head now: `%s`" % main_head[:12],
            "- Revert decision: **%s**, %s" % ("open revert PR (not auto-merged)" if do_revert else "no revert PR", why), ""]
    for j in failed[:3]:
        try:
            hits, tail = summarize_log(_get_text("%s/repos/%s/actions/jobs/%s/logs" % (gate.API, repo, j["id"]), token))
        except Exception as e:  # logs may be unavailable; diagnosis still posts
            hits, tail = ["(log unavailable: %s)" % e.__class__.__name__], []
        body += ["### %s" % j["name"], "Error lines:", "```", *hits[:30], "```", "Last lines:", "```", *tail, "```", ""]
    body += ["This issue was written by post-merge-watch. Nothing was merged or reverted automatically."]
    with open("diagnosis.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(body)[:60000])
    gate._output(revert=str(do_revert).lower(), sha=sha, short=short, pr=pr_ref, skip="false")
    return 0


def cmd_open_pr(short, sha, issue):
    token, repo = gate._env("GH_TOKEN"), gate._env("GITHUB_REPOSITORY")
    pr = gate._req("POST", "/repos/%s/pulls" % repo, token, {
        "title": "Revert %s (auto, needs owner decision)" % short,
        "head": "auto-revert/%s" % short, "base": "main", "draft": False,
        "body": "Automatic revert candidate for `%s`, see #%s.\n\n"
                "This PR is never auto-merged: auto-merge is not enabled on it, risk-gate classifies "
                "`auto-revert/*` and label `revert` as High, and it was created with GITHUB_TOKEN so no "
                "workflows (CI, risk-gate) run until a person closes and reopens it." % (sha, issue)})
    gate._req("POST", "/repos/%s/issues/%s/labels" % (repo, pr["number"]), token, {"labels": ["revert"]})
    gate._req("POST", "/repos/%s/issues/%s/comments" % (repo, issue), token,
              {"body": "Revert candidate opened: #%s (not auto-merged)." % pr["number"]})
    gate._output(revert_pr=str(pr["number"]))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["diagnose"]:
        sys.exit(cmd_diagnose())
    if a[:1] == ["open-pr"] and len(a) == 4:
        sys.exit(cmd_open_pr(a[1], a[2], a[3]))
    print(__doc__, file=sys.stderr)
    sys.exit(2)
