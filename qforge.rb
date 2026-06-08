cask "qforge" do
  version "1.0.4"
  sha256 "186b594daffa1a31881956d9d1469361835b5bb3cdf6c04c6ae2ef3f17e7e200"

  url "https://github.com/AdarshaGS/QForge/releases/download/v#{version}/QForge.dmg"
  name "QForge"
  desc "Professional database client - free alternative to TablePlus"
  homepage "https://github.com/AdarshaGS/QForge"

  livecheck do
    url :url
    strategy :github_latest
  end

  app "QForge.app"

  zap trash: [
    "~/Library/Application Support/QForge",
    "~/Library/Preferences/com.qforge.plist",
    "~/Library/Saved Application State/com.qforge.savedState",
    "~/connections.json",
    "~/query_history.json",
  ]

  caveats <<~EOS
    QForge has been installed!

    Features:
      • MySQL, PostgreSQL, SQLite support
      • SSH tunnels with key authentication
      • Inline editing with Cmd+S
      • Dark/Light themes
      • SQL autocomplete
      • Export to SQL, CSV, Excel, JSON

    Quick Start:
      1. Launch from Applications or run: open -a QForge
      2. Click "+" to add a database connection
      3. Start managing your databases!

    Documentation: https://github.com/AdarshaGS/QForge
  EOS
end
