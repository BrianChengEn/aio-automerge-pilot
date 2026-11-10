#!/usr/bin/env python3
"""risk-gate runner (runs only from the default branch, never on PR code).

Subcommands
  decide    PR_NUMBER     classify + review evidence -> post `risk-gate` check, write outputs
  finalize  PR_NUMBER SHA after an owner-gate approval -> post `risk-gate` success on SHA
  prs-for-check-run       print PR numbers from a check_run event payload

Env: GH_TOKEN (bc-risk-gate installation token, checks:write), GITHUB_REPOSITORY,
GITHUB_SHA (gate commit on main), GITHUB_RUN_ID, GITHUB_SERVER_URL, GITHUB_OUTPUT,
BUGBOT_APP_ID, BUGBOT_CHECK_NAME, MEDIUM_EVIDENCE ("bugbot" or "owner"),
OWNER_LOGINS (comma list), ACTIONS_TOKEN (GITHUB_TOKEN, actions:read, finalize only).
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from risk_gate import classify  # noqa: E402

API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
CHECK_NAME = "risk-gate"


# ---------- pure decision logic (unit tested on the box) ----------

def evaluate_review(check_runs, head_sha, bugbot_app_id, check_name="Cursor Bugbot"):
    """Return (verdict, detail). verdict: pass | wait | escalate.

    pass     every `Cursor Bugbot` run on head_sha is from the Bugbot app,
             completed, conclusion success (success = no issues, none unresolved)
    wait     no Bugbot run yet, or one still queued/in progress
    escalate anything else: neutral (findings / cancelled / error), failure,
             skipped, a same-named run from any other app (tamper signal)
    """
    runs = [r for r in check_runs or []
            if r.get("name") == check_name and r.get("head_sha") == head_sha]
    foreign = [r for r in runs if (r.get("app") or {}).get("id") != bugbot_app_id]
    if foreign:
        ids = sorted({(r.get("app") or {}).get("id") for r in foreign}, key=str)
        return "escalate", "'%s' run(s) from unexpected app id(s) %s on %s" % (check_name, ids, head_sha[:12])
    if not runs:
        return "wait", "no '%s' run on %s yet" % (check_name, head_sha[:12])
    pending = [r for r in runs if r.get("status") != "completed"]
    bad = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") != "success"]
    if bad:
        r = bad[0]
        return "escalate", "'%s' concluded %s (check run %s); not a pass" % (check_name, r.get("conclusion"), r.get("id"))
    if pending:
        return "wait", "'%s' still %s" % (check_name, pending[0].get("status"))
    ids = ",".join(str(r.get("id")) for r in runs)
    return "pass", "'%s' success from app %s on %s (check run %s)" % (check_name, bugbot_app_id, head_sha[:12], ids)


def decide(tier, review=None, medium_evidence="bugbot"):
    """Map tier + review verdict to (check_state, needs_owner).

    check_state: success | waiting_review | waiting_owner
    """
    if tier == "low":
        return "success", False
    if tier == "medium":
        if medium_evidence != "bugbot":
            return "waiting_owner", True
        verdict = (review or ("wait", ""))[0]
        if verdict == "pass":
            return "success", False
        if verdict == "wait":
            return "waiting_review", False
        return "waiting_owner", True
    return "waiting_owner", True


# ---------- GitHub API helpers ----------

def _req(method, path, token, body=None, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "null")


def _paged(path, token, key=None, params=None):
    out, page = [], 1
    while True:
        p = dict(params or {}, per_page=100, page=page)
        data = _req("GET", path, token, params=p)
        items = data.get(key, []) if key else data
        out.extend(items)
        if len(items) < 100 or page >= 30:
            return out
        page += 1


def _env(name, default=None):
    v = os.environ.get(name, default)
    if v is None or v == "":
        raise SystemExit("missing env %s" % name)
    return v


def _output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join("%s=%s\n" % (k, v) for k, v in kv.items())
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(lines)
    sys.stdout.write(lines)


def _post_check(token, repo, sha, state, title, summary):
    body = {"name": CHECK_NAME, "head_sha": sha, "external_id": "gate@" + os.environ.get("GITHUB_SHA", "")[:12],
            "details_url": "%s/%s/actions/runs/%s" % (os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
                                                    repo, os.environ.get("GITHUB_RUN_ID", "")),
            "output": {"title": title[:250], "summary": summary[:60000]}}
    if state == "success":
        body.update(status="completed", conclusion="success")
    elif state == "failure":
        body.update(status="completed", conclusion="failure")
    else:
        body.update(status="in_progress")
    return _req("POST", "/repos/%s/check-runs" % repo, token, body)


def _summary(pr, result, review, state, gate_sha):
    lines = ["PR #%s head `%s`" % (pr["number"], pr["head"]["sha"]),
             "Tier: **%s**  State: **%s**" % (result["tier"], state),
             "Gate code: main@`%s` (never PR code)" % gate_sha,
             "Stats: %s" % json.dumps(result["stats"], sort_keys=True), "", "Reasons:"]
    lines += ["- " + r for r in result["reasons"]] or ["- none"]
    if review:
        lines += ["", "Review evidence: %s: %s" % review]
    return "\n".join(lines)


def cmd_decide(number, event_name):
    token, repo = _env("GH_TOKEN"), _env("GITHUB_REPOSITORY")
    pr = _req("GET", "/repos/%s/pulls/%s" % (repo, number), token)
    if pr.get("state") != "open" or pr["base"]["ref"] != pr["base"]["repo"]["default_branch"]:
        _output(needs_owner="false", head_sha=pr["head"]["sha"], tier="none", state="skipped")
        return 0
    head = pr["head"]["sha"]
    files = _paged("/repos/%s/pulls/%s/files" % (repo, number), token)
    again = _req("GET", "/repos/%s/pulls/%s" % (repo, number), token)
    if again["head"]["sha"] != head:
        # Head moved while reading files; the synchronize run for the new head decides.
        _output(needs_owner="false", head_sha=head, tier="none", state="head-moved")
        return 0
    result = classify(files, pr)
    review = None
    if result["tier"] == "medium":
        runs = _paged("/repos/%s/commits/%s/check-runs" % (repo, head), token, key="check_runs",
                      params={"check_name": os.environ.get("BUGBOT_CHECK_NAME", "Cursor Bugbot"), "filter": "all"})
        review = evaluate_review(runs, head, int(_env("BUGBOT_APP_ID")),
                                 os.environ.get("BUGBOT_CHECK_NAME", "Cursor Bugbot"))
    state, needs_owner = decide(result["tier"], review, os.environ.get("MEDIUM_EVIDENCE", "bugbot"))
    # A Bugbot check_run event re-evaluates Medium only; it must not re-queue owner approvals for High.
    if event_name == "check_run" and result["tier"] != "medium":
        _output(needs_owner="false", head_sha=head, tier=result["tier"], state="unchanged")
        return 0
    gate_sha = os.environ.get("GITHUB_SHA", "")[:12]
    titles = {"success": "risk:%s pass" % result["tier"],
              "waiting_review": "risk:medium waiting for one independent review",
              "waiting_owner": "risk:%s waiting for owner approval (owner-gate)" % result["tier"]}
    _post_check(token, repo, head, state, titles[state], _summary(pr, result, review, state, gate_sha))
    _output(needs_owner=str(needs_owner).lower(), head_sha=head, tier=result["tier"], state=state)
    return 0


def cmd_finalize(number, sha):
    token, repo = _env("GH_TOKEN"), _env("GITHUB_REPOSITORY")
    owners = {s.strip().lower() for s in _env("OWNER_LOGINS").split(",") if s.strip()}
    pr = _req("GET", "/repos/%s/pulls/%s" % (repo, number), token)
    if pr.get("state") != "open" or pr["head"]["sha"] != sha:
        print("PR head moved or PR closed; not posting success for stale %s" % sha)
        return 0
    approvals = _req("GET", "/repos/%s/actions/runs/%s/approvals" % (repo, _env("GITHUB_RUN_ID")),
                     _env("ACTIONS_TOKEN"))
    approved = [a for a in approvals or [] if a.get("state") == "approved"
                and any(e.get("name") == "owner-gate" for e in a.get("environments") or [])]
    who = sorted({(a.get("user") or {}).get("login", "") for a in approved})
    if not approved or not all(w.lower() in owners for w in who):
        _post_check(token, repo, sha, "failure", "risk-gate: owner approval not verified",
                    "owner-gate approvals: %s (expected one of %s)" % (who, sorted(owners)))
        return 1
    _post_check(token, repo, sha, "success", "risk-gate: owner approved",
                "PR #%s head `%s` approved in environment owner-gate by %s (run %s). Gate code main@`%s`."
                % (number, sha, ", ".join(who), os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_SHA", "")[:12]))
    return 0


def cmd_prs_for_check_run():
    with open(_env("GITHUB_EVENT_PATH"), encoding="utf-8") as fh:
        ev = json.load(fh)
    nums = [str(p["number"]) for p in (ev.get("check_run") or {}).get("pull_requests") or []]
    print(" ".join(nums))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["decide"] and len(a) == 2:
        sys.exit(cmd_decide(a[1], os.environ.get("GITHUB_EVENT_NAME", "")))
    if a[:1] == ["finalize"] and len(a) == 3:
        sys.exit(cmd_finalize(a[1], a[2]))
    if a[:1] == ["prs-for-check-run"]:
        sys.exit(cmd_prs_for_check_run())
    print(__doc__, file=sys.stderr)
    sys.exit(2)
