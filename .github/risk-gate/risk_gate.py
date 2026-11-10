#!/usr/bin/env python3
"""risk-gate classifier for aio-automerge-pilot (plan A+).

Input: the JSON returned by GET /repos/{o}/{r}/pulls/{n}/files (all pages
concatenated) and, optionally, GET /repos/{o}/{r}/pulls/{n}. It never reads
or executes PR code; it only looks at file names, status, line counts and
the textual patch that the API returns.

Output: {"tier": "low"|"medium"|"high", "reasons": [...], "stats": {...}}

Rule: High if ANY high rule fires, else Medium if any medium rule fires,
else Low. Size is only a hint; path/content risk decides first. Any input
the classifier cannot fully see fails closed to High.
Stdlib only. Changing this file is itself High (.github/**).
"""
import json
import re
import sys

MEDIUM_LINES = 300
MEDIUM_FILES = 15
HIGH_LINES = 1500
HIGH_DELETED_FILES = 10
MAX_API_FILES = 3000  # GET /pulls/{n}/files hard limit

# (regex on lowercase path, reason). Matched against filename and
# previous_filename, so renames out of a risky path are still High.
HIGH_PATHS = [
    (r"^\.github/", "CI, workflow or gate file (.github/**)"),
    (r"^\.cursor/", "agent configuration (.cursor/**)"),
    (r"(^|/)\.gitlab-ci\.yml$|(^|/)\.circleci/|(^|/)jenkinsfile$|(^|/)azure-pipelines\.yml$",
     "CI configuration"),
    (r"(^|/)codeowners$", "ownership rules"),
    (r"(^|/)(approval_policy|bugbot|agents)\.md$", "review/agent policy file"),
    (r"(^|/)\.env($|\.)|(^|/)[^/]*\.(pem|key|p12|pfx|jks|keystore|asc|gpg)$"
     r"|(^|/)(id_rsa|id_ed25519)", "secrets or environment file"),
    (r"(^|[/_.-])(secrets?|credentials?|passwords?|passwd|private[_-]?keys?)([/_.-]|$)",
     "secrets handling"),
    (r"(^|[/_.-])(auth|authn|authz|oauth2?|login|sessions?|jwt|tokens?|crypto|crypt|"
     r"cipher|encrypt(ion)?|signing|certs?|saml|sso|mfa|2fa)([/_.-]|$)",
     "auth, session or crypto code"),
    (r"(^|[/_.-])(permissions?|rbac|acl|iam|roles?|policy|policies)([/_.-]|$)",
     "permission logic"),
    (r"(^|/)(dockerfile|containerfile)[^/]*$|(^|/)docker-compose[^/]*$|(^|/)compose\.ya?ml$"
     r"|\.tf$|\.tfvars$|\.hcl$|(^|/)(terraform|infra|infrastructure|deploy|deployment|"
     r"k8s|kubernetes|helm|charts|ansible|pulumi)/"
     r"|(^|/)(procfile|fly\.toml|vercel\.json|netlify\.toml|render\.yaml|app\.yaml|"
     r"serverless\.ya?ml|wrangler\.toml)$",
     "deploy or infrastructure"),
    (r"(^|/)(migrations?|alembic|migrate|db/schema)/|(^|/)schema\.(rb|prisma|sql)$|\.sql$",
     "database migration or schema"),
    (r"(^|/)(package\.json|requirements[^/]*\.(txt|in)|pyproject\.toml|setup\.py|setup\.cfg|"
     r"pipfile|go\.mod|cargo\.toml|gemfile|pom\.xml|build\.gradle(\.kts)?|composer\.json|"
     r"[^/]*\.csproj|environment\.ya?ml)$",
     "dependency manifest (possible new dependency)"),
    (r"(^|/)\.gitmodules$|(^|/)\.gitattributes$", "repository plumbing"),
]

MEDIUM_PATHS = [
    (r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|pipfile\.lock|"
     r"go\.sum|cargo\.lock|gemfile\.lock|composer\.lock|uv\.lock)$", "lockfile change"),
    (r"(^|/)(conftest\.py|pytest\.ini|tox\.ini|noxfile\.py|makefile|\.pre-commit-config\.yaml)$",
     "test or build harness"),
]

# Destructive/irreversible patterns, looked for in ADDED lines of the patch.
IRREVERSIBLE = [
    (r"\bdrop\s+(table|database|schema|column)\b", "destructive SQL (DROP)"),
    (r"\btruncate\s+(table\s+)?\w", "destructive SQL (TRUNCATE)"),
    (r"\bdelete\s+from\b", "destructive SQL (DELETE FROM)"),
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "recursive forced delete (rm -rf)"),
    (r"shutil\.rmtree|\.drop_collection\(|\bflushall\b", "bulk data deletion"),
    (r"git\s+push\s+[^\n]*--force|git\s+push\s+-f\b", "force push"),
]

AUTO_REVERT_PREFIX = "auto-revert/"


def _match(rules, path):
    p = path.lower()
    return [reason for rx, reason in rules if re.search(rx, p)]


def _added_lines(patch):
    for line in (patch or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            yield line[1:]


def classify(files, pr=None):
    high, medium = [], []
    if not isinstance(files, list):
        return {"tier": "high", "reasons": ["file list unreadable (fail closed)"], "stats": {}}

    additions = deletions = removed = 0
    for f in files:
        name = f.get("filename") or ""
        prev = f.get("previous_filename") or ""
        status = f.get("status") or ""
        additions += int(f.get("additions") or 0)
        deletions += int(f.get("deletions") or 0)
        if status == "removed":
            removed += 1
        for path in filter(None, {name, prev}):
            for r in _match(HIGH_PATHS, path):
                high.append("%s: %s" % (r, path))
            for r in _match(MEDIUM_PATHS, path):
                medium.append("%s: %s" % (r, path))
        patch = f.get("patch")
        changed = int(f.get("changes") or 0)
        if patch is None and (changed > 0 or status in ("added", "renamed", "copied", "changed")):
            medium.append("diff not visible to the gate (binary or too large): %s" % name)
        for line in _added_lines(patch):
            low = line.lower()
            for rx, reason in IRREVERSIBLE:
                if re.search(rx, low):
                    high.append("%s in %s" % (reason, name))

    total = additions + deletions
    nfiles = len(files)
    if nfiles == 0:
        high.append("empty file list (fail closed)")
    if nfiles >= MAX_API_FILES:
        high.append("file list truncated at API limit (fail closed)")
    if removed > HIGH_DELETED_FILES:
        high.append("deletes %d files (> %d)" % (removed, HIGH_DELETED_FILES))
    if total > HIGH_LINES:
        high.append("size hint: %d changed lines (> %d)" % (total, HIGH_LINES))
    elif total > MEDIUM_LINES:
        medium.append("size hint: %d changed lines (> %d)" % (total, MEDIUM_LINES))
    if nfiles > MEDIUM_FILES:
        medium.append("size hint: %d files (> %d)" % (nfiles, MEDIUM_FILES))

    if pr is not None:
        declared = pr.get("changed_files")
        if declared is not None and int(declared) != nfiles:
            high.append("file list incomplete: API listed %d of %s files (fail closed)"
                        % (nfiles, declared))
        head = pr.get("head") or {}
        base = pr.get("base") or {}
        if (head.get("repo") or {}).get("full_name") != (base.get("repo") or {}).get("full_name"):
            high.append("PR from a fork or deleted repository")
        if (head.get("ref") or "").startswith(AUTO_REVERT_PREFIX):
            high.append("automatic revert PR (never auto-merged)")
        labels = {(l.get("name") or "").lower() for l in pr.get("labels") or []}
        if "revert" in labels:
            high.append("labelled revert (never auto-merged)")

    tier = "high" if high else ("medium" if medium else "low")
    return {
        "tier": tier,
        "reasons": sorted(set(high)) + sorted(set(medium)),
        "stats": {"files": nfiles, "additions": additions, "deletions": deletions,
                  "removed_files": removed},
    }


def main(argv):
    if len(argv) < 2:
        print("usage: risk_gate.py FILES_JSON [PR_JSON]", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        files = json.load(fh)
    pr = None
    if len(argv) > 2:
        with open(argv[2], encoding="utf-8") as fh:
            pr = json.load(fh)
    print(json.dumps(classify(files, pr), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
