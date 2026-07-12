#!/usr/bin/env bash
set -euo pipefail

rm -rf ~/alex-demo && mkdir -p ~/alex-demo/src && cd ~/alex-demo
git init -q .

cat > lexicon.yaml <<'EOF'
version: 1
scopes:
  - id: billing
    label: Billing
  - id: api
    label: API
terms:
  - id: billing.credit_limit
    canonical: credit limit
    scopes: [billing]
    tools: [billing.update_credit_limit]
  - id: api.rate_limit
    canonical: rate limit
    scopes: [api]
    tools: [api.update_rate_limit]
  - id: core.context_space
    canonical: ContextSpace
    scopes: [billing, api]
    aliases:
      - surface: WorkspaceScope
        deprecated: true
EOF

cat > src/handlers.py <<'EOF'
class ContextSpace:
    """Shared execution context."""
EOF

git add -A && git -c user.email=d@d -c user.name=demo commit -qm "baseline"

# "агент" дописывает код со старым термином
cat >> src/handlers.py <<'EOF'

def resolve_workspace(scope: "WorkspaceScope"):
    # WorkspaceScope holds the current billing context
    return scope
EOF

clear