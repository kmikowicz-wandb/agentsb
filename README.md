# agentsb

Are you tired of hitting "Yes" on parallel codex sessions? Do
you hate managing global and fine grained shell allowlists like
a sysadmin from the 20th century? Do you wish that you could run "--dangerously-skip-permissions"
without worrying about your coding agents providing a RCE vector
for criminals?

Coding agents ship their own permissions systems for interacting
with your host system. You rely on the validity of that system
to design good security profiles, and are bound to its security UX.
Most coding agents could safely complete their work in a thin
container. On Linux you could build a firejail or bubblejail
around your workspaces.

Unfortunately we sometimes have to work on Mac OS which doesn't
support containers in the big 26.  Codex comes close to a sane solution using "Sandbox mode"
implemented over Seatbelt. Seatbelt is deprecated.

This project takes a different approach. Run coding agents (Claude Code, Codex, Aider, Forge) inside isolated Lima VMs
on macOS. Reduce the blast radius of prompt-injection attacks by keeping the
agent away from your host `$HOME`, Keychain, SSH keys, and arbitrary network
destinations.

# Features
* Secure base VM
* Supports popular coding agents
* VM reuse across parent directories

## Future features
* GPU sharing support
* Hardware sharing support

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

```sh
brew install kmikowicz/agentsb/agentsb
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
2. Run `agentsb <name>`. The wrapper detects the new fragment and runs
   the provision inside the existing VM. No `--reset` or VM rebuild
   required.

The install is in-place; VM state (existing agents, auth tokens,
workspace edits) is preserved across agent additions.

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
- **`--with-claude-config` doesn't pick up new files** — it copies a safe
  subset each run, not a full mirror. Add filenames to `sync_claude_config`
  in `bin/agentsb` if you want more.
- **First boot is slow** — yes, provision takes 2-5 min. Every subsequent
  start is ~3s. Don't `--reset` casually.
