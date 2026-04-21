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

    (bin/"agentsb").write <<~SH
      #!/bin/bash
      export AGENTSB_HOME=#{libexec}
      exec #{libexec}/agentsb "$@"
    SH
    (bin/"agentsb").chmod 0755
  end

  def caveats
    <<~EOS
      First run of each agent creates a Lima VM (2-5 min). Subsequent starts
      take ~3s. VMs are named `agentsb-<agent>` under ~/.lima/.

        agentsb claude            # interactive
        agentsb aider FILE...     # aider with files in /workspace
        agentsb --shell codex     # VM shell
        agentsb --stop claude     # stop VM when done
    EOS
  end

  test do
    assert_match "usage: agentsb", shell_output("#{bin}/agentsb --help")
  end
end
