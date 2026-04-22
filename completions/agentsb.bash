# bash completion for agentsb

_agentsb_completions() {
  local cur prev words cword
  if declare -F _init_completion >/dev/null; then
    _init_completion || return
  else
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    words=("${COMP_WORDS[@]}")
    cword=$COMP_CWORD
  fi

  local flags="-h --help -w --workspace --ephemeral --auto --with-claude-config --shell --stop --reset --status --prune --disk-check --install-completion"
  local agents="aider claude codex forge"
  local subcommands="resize"
  local shells="bash zsh fish"

  # `agentsb resize <TAB>` — complete Lima VM names.
  if [[ $cword -eq 2 && "${words[1]}" == "resize" ]]; then
    local vms
    vms=$(limactl list --format '{{.Name}}' 2>/dev/null)
    COMPREPLY=($(compgen -W "$vms" -- "$cur"))
    return
  fi

  case "$prev" in
    -w|--workspace)
      compopt -o dirnames 2>/dev/null
      COMPREPLY=($(compgen -d -- "$cur"))
      return
      ;;
    --install-completion)
      COMPREPLY=($(compgen -W "$shells" -- "$cur"))
      return
      ;;
  esac

  if [[ "$cur" == -* ]]; then
    COMPREPLY=($(compgen -W "$flags" -- "$cur"))
    return
  fi

  COMPREPLY=($(compgen -W "$agents $subcommands" -- "$cur"))
}

complete -F _agentsb_completions agentsb
