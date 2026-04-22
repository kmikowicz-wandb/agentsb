# fish completion for agentsb

# Do not suggest files unless specifically noted.
complete -c agentsb -f

# Global flags.
complete -c agentsb -s h -l help                 -d "show help"
complete -c agentsb -s w -l workspace -r -F      -d "directory to mount at /workspace"
complete -c agentsb      -l ephemeral            -d "throwaway VM, destroyed on exit"
complete -c agentsb      -l auto                 -d "run agent in fully-automatic mode"
complete -c agentsb      -l with-claude-config   -d "copy safe subset of host ~/.claude/ into VM"

# VM lifecycle / maintenance flags (mutually exclusive modes).
complete -c agentsb -l shell        -d "open interactive VM shell"
complete -c agentsb -l stop         -d "stop the VM"
complete -c agentsb -l reset        -d "destroy and recreate the VM"
complete -c agentsb -l status       -d "show VM status"
complete -c agentsb -l prune        -d "delete stale VM registrations"
complete -c agentsb -l disk-check   -d "mark over-80%-used VMs for resize"

# Install-completion accepts a shell name.
complete -c agentsb -l install-completion -xa "bash zsh fish" -d "install shell completion"

# First positional: subcommand or agent name.
complete -c agentsb -n "__fish_is_nth_token 1" -a "resize" -d "grow a VM disk by 1.5x immediately"
complete -c agentsb -n "__fish_is_nth_token 1" -a "aider claude codex forge"

# `resize <vm>` — complete live VM names.
complete -c agentsb -n "__fish_seen_subcommand_from resize" \
  -a "(limactl list --format '{{.Name}}' 2>/dev/null)"
