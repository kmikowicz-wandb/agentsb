# agentsb

Run coding agents (Claude Code, Codex, Aider, Forge) inside isolated Lima VMs
on macOS. Reduces the blast radius of prompt-injection attacks by keeping the
agent away from your host `$HOME`, Keychain, SSH keys, and arbitrary network
destinations.

## Threat model

The agent is treated as untrusted — not the agent's binary itself, but
anything it processes (web content it fetches, files in the workspace,
responses from the LLM that may carry prompt injection). The goal is
**host isolation**: nothing the agent does inside the VM can reach the host's
credentials, home directory, other projects, or the network beyond a narrow
allowlist.

What the VM boundary gives you:

- Host `$HOME` is **not** mounted. `~/.ssh`, `~/.aws`, macOS Keychain,
  browser cookies, and every other project's files are physically absent
  from the VM.
- Only `$PWD` at invocation time is bind-mounted, as `/workspace`.
- Egress is firewalled to DNS / HTTP / HTTPS / SSH — no arbitrary ports.
- Host SSH pubkeys are not auto-copied in (`ssh.loadDotSSHPubKeys: false`).
- Native ARM only — no Rosetta emulation.

What it does **not** give you:

- Per-hostname egress filtering. Lima's firewall is port-level. For
  per-host enforcement, run a filtering HTTPS proxy (e.g. mitmproxy with an
  allowlist) on the host and set `HTTPS_PROXY` inside the VM.
- Isolation between projects or between agents. All invocations share one
  `agentsb` VM with every agent installed. A prompt injection during a
  `claude` session can leave state that affects the next `codex` or `aider`
  session in the same VM. Mitigations: `--ephemeral` for risky one-offs, or
  `AGENTSB_VM=project-foo` for a named per-project VM, or `--reset` when
  you suspect drift.
- Protection from supply-chain attacks against the agent's own install
  (npm/curl-piped installers run at provision time).

## Requirements

- macOS on Apple Silicon (templates install the aarch64 Ubuntu image)
- [Lima](https://lima-vm.io/) 2.x — installed automatically via Homebrew
- [uv](https://docs.astral.sh/uv/) — installed automatically via Homebrew;
  used for on-the-fly dependency resolution (PEP 723 script headers)

## Install

Modern Homebrew (4.x+) requires formulae to live in a tap. One-time setup:

```sh
# 1. Make the project a git repo (head formulas clone from git)
cd ~/Development/agentsb
git init && git add . && git commit -m "initial"

# 2. Register a local tap that points at this formula
TAP_DIR="$(brew --repository)/Library/Taps/kmikowicz/homebrew-agentsb"
mkdir -p "$TAP_DIR/Formula"
ln -sf "$PWD/Formula/agentsb.rb" "$TAP_DIR/Formula/agentsb.rb"

# 3. Install
brew install --HEAD kmikowicz/agentsb/agentsb
```

After edits, commit and reinstall:

```sh
git commit -am wip
brew reinstall --HEAD kmikowicz/agentsb/agentsb
```

The formula installs:

- `$(brew --prefix)/bin/agentsb` — shim that sets `AGENTSB_HOME` and execs
  the real wrapper
- `$(brew --prefix)/opt/agentsb/libexec/agentsb` — wrapper script
- `$(brew --prefix)/opt/agentsb/libexec/lima/*.yaml` — Lima templates

Verify:

```sh
agentsb --help
```

## Usage

```
agentsb [flags] [AGENT [agent-args...]]
```

`AGENT` is one of: **`claude`**, **`codex`**, **`aider`**, **`forge`**.
All four share a single VM named `agentsb`; `AGENT` just selects which
command runs inside it.

### First run

```sh
cd ~/some-project
agentsb claude                # boots agentsb VM (~3-7 min first time; installs all agents)
agentsb claude -p "fix the test in foo.py"
agentsb codex                 # same VM, different agent
agentsb aider foo.py
```

Subsequent invocations reuse the VM (~3s cold start).

### Modes

| Flag        | Effect                                               |
|-------------|------------------------------------------------------|
| *(none)*    | Run `AGENT` in the VM (AGENT required)              |
| `--shell`   | Drop into a VM shell at `/workspace`                 |
| `--stop`    | Stop the VM                                          |
| `--reset`   | Destroy and recreate the VM (re-provisions base)    |
| `--status`  | Print VM status                                      |

Management modes don't take an `AGENT` argument — there's one VM. The VM
is created lazily on first `agentsb <AGENT>` invocation; no explicit
`--start` is needed.

### Options

| Flag                       | Effect                                                          |
|----------------------------|-----------------------------------------------------------------|
| `-w PATH`, `--workspace`   | Dir bind-mounted at `/workspace`. Set at VM create time only.   |
| `--ephemeral`              | Throwaway VM, destroyed on exit. ~2-4 min base boot each run.  |
| `--with-claude-config`     | Copy a safe subset of host `~/.claude/` into the VM (claude only). |
| `-y`, `--yes`              | Skip the confirmation prompt when installing a never-before-seen agent. |
| `-h`, `--help`             | Help.                                                            |

### Environment

| Variable           | Purpose                                                  |
|--------------------|----------------------------------------------------------|
| `AGENTSB_VM`       | Override VM name (default `agentsb`). Use for per-project or per-task VMs. |
| `AGENTSB_HOME`     | Installation root (auto-detected by the brew shim).     |
| `AGENTSB_TEMPLATE` | Override template path (for local template dev).        |

Forwarded into the VM when set on the host:
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`,
`OPENAI_API_KEY`, `NO_COLOR`, `TERM`.

## Authentication

Because `$HOME` is not mounted, the VM does not see your host Keychain.
Two options:

1. **Log in once per VM** (persists in the VM disk):
   ```sh
   agentsb claude /login
   agentsb codex login
   ```
2. **Env var** on the host — forwarded automatically:
   ```sh
   export ANTHROPIC_API_KEY=...
   export OPENAI_API_KEY=...
   agentsb claude
   ```

## Recipes

```sh
# Risky analysis in a fresh VM, auto-destroyed on exit:
agentsb --ephemeral claude

# Use your host's global CLAUDE.md + custom commands inside the VM:
agentsb --with-claude-config claude

# Isolate per project (separate named VM, separate state):
AGENTSB_VM=proj-acme agentsb claude
AGENTSB_VM=proj-acme agentsb --reset   # later, reset just this one

# Drop into a shell to debug provision or inspect VM state:
agentsb --shell

# Force re-provision (e.g. after adding a new agent):
agentsb --reset
```

## Adding a new agent

1. Create a provision-only fragment at `lima/agents/<name>.yaml`:
   ```yaml
   provision:
     - mode: system   # or user
       script: |
         # install your agent so `<name>` is on PATH inside the VM
   ```
2. Run `agentsb <name>`. The wrapper detects the new fragment, prompts
   for confirmation (showing the script path), and runs the provision
   inside the existing VM. No `--reset` or VM rebuild required.
3. Pass `-y` to skip the prompt (useful in scripts or CI).

The install is in-place; VM state (existing agents, auth tokens,
workspace edits) is preserved across agent additions.

## Layout

```
agentsb/
├── Formula/agentsb.rb     # Homebrew formula
├── bin/agentsb            # wrapper (Python, PEP 723 / uv-managed deps)
├── lima/
│   ├── base.yaml          # the VM template: packages, firewall, node, uv
│   └── agents/            # per-agent provision fragments
│       ├── claude.yaml    # npm i -g @anthropic-ai/claude-code
│       ├── codex.yaml     # npm i -g @openai/codex
│       ├── aider.yaml     # uvx shim for aider-chat
│       └── forge.yaml     # curl-piped forgecode install
├── tests/
│   ├── conftest.py        # loads bin/agentsb as a module
│   ├── test_cli.py        # unit tests (argparse, env forwarding, ...)
│   └── run.py             # PEP 723 test runner (uv-managed pytest venv)
└── README.md
```

Only `lima/base.yaml` is used as the Lima VM template. Agent fragments
are standalone provision scripts that `bin/agentsb` parses with PyYAML
and executes inside the existing VM via `limactl shell VM -- sudo bash -s`.
This makes agent addition incremental (no VM rebuild) while still letting
Lima own base VM lifecycle.

## Testing

```sh
./tests/run.py                # full suite
./tests/run.py -k test_parse  # filter
```

`tests/run.py` is itself a PEP 723 script — uv resolves `pytest`, `rich`,
and `pyyaml` in an ephemeral venv on first run and caches them for later.

## Troubleshooting

- **`limactl: VM creation failed`** — run `agentsb --reset` after checking
  `limactl list` for stale entries. `rm -rf ~/.lima/agentsb` is the
  nuclear option.
- **Agent can't reach network** — check the egress firewall isn't blocking
  a port you actually need. The template allows 53/80/443/22 by default.
- **`--with-claude-config` doesn't pick up new files** — it copies a safe
  subset each run, not a full mirror. Add filenames to `sync_claude_config`
  in `bin/agentsb` if you want more.
- **First boot is slow** — yes, provision takes 2-5 min. Every subsequent
  start is ~3s. Don't `--reset` casually.
