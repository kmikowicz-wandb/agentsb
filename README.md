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
- Isolation between projects. By default all invocations share one VM per
  agent; a prompt injection in project A can leave state that affects
  project B. Mitigations: `--ephemeral` for risky one-offs, or
  `AGENTSB_VM=project-foo` for a named per-project VM.
- Protection from supply-chain attacks against the agent's own install
  (npm/curl-piped installers run at provision time).

## Requirements

- macOS on Apple Silicon (templates install the aarch64 Ubuntu image)
- [Lima](https://lima-vm.io/) 2.x — installed automatically via Homebrew
- Bash 4+

## Install

From this directory:

```sh
git init && git add . && git commit -m "initial"
brew install --HEAD ./Formula/agentsb.rb
```

The formula installs:

- `$(brew --prefix)/bin/agentsb` — a shim that sets `AGENTSB_HOME` and execs
  the real wrapper
- `$(brew --prefix)/libexec/agentsb/agentsb` — wrapper script
- `$(brew --prefix)/libexec/agentsb/lima/*.yaml` — Lima templates

Verify:

```sh
agentsb --help
```

## Usage

```
agentsb [flags] AGENT [agent-args...]
```

`AGENT` is one of: **`claude`**, **`codex`**, **`aider`**, **`forge`**.
Each agent has its own Lima VM named `agentsb-<agent>`.

### First run

```sh
cd ~/some-project
agentsb claude                # boots agentsb-claude VM (~2-5 min first time)
agentsb claude -p "fix the test in foo.py"
```

Subsequent invocations reuse the VM (~3s cold start).

### Modes

| Flag        | Effect                                               |
|-------------|------------------------------------------------------|
| *(none)*    | Run `AGENT` in its VM                                |
| `--shell`   | Drop into a VM shell at `/workspace`                 |
| `--start`   | Start/create the VM and exit                        |
| `--stop`    | Stop the VM                                          |
| `--reset`   | Destroy and recreate the VM                         |
| `--status`  | Print VM status                                      |

### Options

| Flag                       | Effect                                                          |
|----------------------------|-----------------------------------------------------------------|
| `-w PATH`, `--workspace`   | Dir bind-mounted at `/workspace`. Set at VM create time only.   |
| `--ephemeral`              | Throwaway VM, destroyed on exit. ~2-5 min provision each run.  |
| `--with-claude-config`     | Copy a safe subset of host `~/.claude/` into the VM (claude only). |
| `-h`, `--help`             | Help.                                                            |

### Environment

| Variable           | Purpose                                                  |
|--------------------|----------------------------------------------------------|
| `AGENTSB_VM`       | Override VM name (default `agentsb-<AGENT>`).           |
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

# Isolate per project (separate VM, separate state):
AGENTSB_VM=proj-acme agentsb claude
AGENTSB_VM=proj-acme agentsb --reset   # later, reset just this one

# Drop into a shell in the forge VM to debug installation:
agentsb --shell forge
```

## Adding a new agent

1. Create `lima/<name>.yaml` inheriting from `base.yaml`:
   ```yaml
   base:
     - ./base.yaml
   provision:
     - mode: system   # or user
       script: |
         # install your agent here
   ```
2. Make sure the install puts an executable named `<name>` on `PATH`.
3. Validate: `limactl validate lima/<name>.yaml`.
4. Run: `agentsb <name> …`.

## Layout

```
agentsb/
├── Formula/agentsb.rb     # Homebrew formula
├── bin/agentsb            # wrapper (bash)
├── lima/
│   ├── base.yaml          # shared VM config + firewall + node + uv
│   ├── claude.yaml        # base + npm i -g @anthropic-ai/claude-code
│   ├── codex.yaml         # base + npm i -g @openai/codex
│   ├── aider.yaml         # base + uvx shim for aider-chat
│   └── forge.yaml         # base + curl-piped forgecode install
└── README.md
```

Each agent template inherits from `base.yaml` via Lima's `base:` field;
`provision:` lists concatenate (base first, then agent-specific install).

## Troubleshooting

- **`limactl: VM creation failed`** — run with `--reset` after checking
  `limactl list` for stale entries. `rm -rf ~/.lima/agentsb-<agent>` is
  the nuclear option.
- **Agent can't reach network** — check the egress firewall isn't blocking
  a port you actually need. The template allows 53/80/443/22 by default.
- **`--with-claude-config` doesn't pick up new files** — it copies a safe
  subset each run, not a full mirror. Add filenames to `sync_claude_config`
  in `bin/agentsb` if you want more.
- **First boot is slow** — yes, provision takes 2-5 min. Every subsequent
  start is ~3s. Don't `--reset` casually.
