class Agentsb < Formula
  desc "Run coding agents (claude, codex, aider, forge) in isolated Lima VMs"
  homepage "https://github.com/kmikowicz/agentsb"
  license "MIT"
  head "https://github.com/kmikowicz/agentsb.git", using: :git, branch: "main"

  depends_on "lima"
  depends_on "uv"

  def install
    libexec.install "bin/agentsb"
    libexec.install "lima"
    libexec.install "src"
    libexec.install "completions"

    (bin/"agentsb").write <<~SH
      #!/bin/bash
      export AGENTSB_HOME=#{libexec}
      exec #{libexec}/agentsb "$@"
    SH
    (bin/"agentsb").chmod 0755

    bash_completion.install libexec/"completions/agentsb.bash" => "agentsb"
    zsh_completion.install libexec/"completions/_agentsb"
    fish_completion.install libexec/"completions/agentsb.fish"
  end

  service do
    run [opt_bin/"agentsb", "--disk-check"]
    run_type :cron
    cron "0 3 * * *"
    log_path var/"log/agentsb-disk-check.log"
    error_log_path var/"log/agentsb-disk-check.log"
  end

  def caveats
    <<~EOS
      First run of each agent creates a Lima VM (2-5 min). Subsequent starts
      take ~3s. VMs are named `agentsb-<agent>` under ~/.lima/.

        agentsb claude            # interactive
        agentsb aider FILE...     # aider with files in /workspace
        agentsb --shell codex     # VM shell
        agentsb --stop claude     # stop VM when done
        agentsb resize <vm>       # manually grow a VM's disk by 1.5x

      Daily disk-usage check (marks VMs >80% full for resize on next start):

        brew services start agentsb

      Runs at 03:00 local time; logs to #{HOMEBREW_PREFIX}/var/log/agentsb-disk-check.log.

      Shell completion is installed into Homebrew's shared completion dirs.
      Non-Homebrew users can instead run:

        agentsb --install-completion
    EOS
  end

  test do
    assert_match "usage: agentsb", shell_output("#{bin}/agentsb --help")
  end
end
