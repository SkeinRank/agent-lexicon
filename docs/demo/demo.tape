Output demo.gif

Set FontFamily "JetBrains Mono"
Set FontSize 17
Set Width 1200
Set Height 620
Set Padding 20
Set TypingSpeed 35ms
Set Theme "Catppuccin Mocha"

Hide
Type "bash setup.sh && cd ~/alex-demo"
Enter
Sleep 1.5s
Show

# ── Act 1: the agent drifted; the diff gate catches it ──
Type "# An AI agent just wrote code. Did it use OUR terminology?"
Enter
Sleep 0.8s
Type "git diff | agent-lexicon lint-diff --stdin --lexicon lexicon.yaml"
Enter
Sleep 6s

Type "clear"
Enter

# ── Act 2: wrong tool call blocked at runtime ──
Type "# Same layer guards tool calls: 'limit' is ambiguous — which one?"
Enter
Sleep 0.8s
Type "agent-lexicon guard lexicon.yaml 'raise the credit limit' --tool api.update_rate_limit"
Enter
Sleep 6.5s

Type "clear"
Enter

# ── Act 3: what the agent should receive up front ──
Type "# Prevention: a deterministic brief the agent gets before writing"
Enter
Sleep 0.8s
Type "agent-lexicon context lexicon.yaml"
Enter
Sleep 5s

Type "clear"
Enter
Type "# pipx install agent-lexicon"
Enter
Sleep 4s