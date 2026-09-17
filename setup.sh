#!/bin/bash
# ==========================================================
# Setup Script for Automatic G-VIT Wi-Fi Login on macOS
# ==========================================================

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
INSTALL_DIR="$HOME/.vitwifi"
PLIST_NAME="com.user.vitwifi.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"
PYTHON_BIN="$(which python3 || echo '/usr/bin/python3')"
USER_ID_NUM=$(id -u)

echo ""
echo "=========================================================="
echo "         🎓 G-VIT Wi-Fi Auto-Login Setup"
echo "=========================================================="
echo ""

# Handle --uninstall
if [ "$1" = "--uninstall" ]; then
    echo "Stopping and removing background LaunchAgent..."
    launchctl bootout "gui/$USER_ID_NUM" "$TARGET_PLIST" 2>/dev/null || true
    rm -f "$TARGET_PLIST"
    rm -rf "$INSTALL_DIR"
    echo "✅ Background LaunchAgent removed."
    echo "To remove credentials from Keychain:"
    echo "  security delete-generic-password -s 'VIT-WiFi'"
    echo ""
    echo "Uninstallation complete."
    exit 0
fi

# Step 1: Ensure executable permissions and deploy to ~/.vitwifi
chmod +x "$DIR/wifi_login.py"
mkdir -p "$INSTALL_DIR"
cp "$DIR/wifi_login.py" "$INSTALL_DIR/wifi_login.py"
chmod +x "$INSTALL_DIR/wifi_login.py"

# Step 2: Configure Credentials
echo "Step 1: Configure Credentials"
echo "-----------------------------------"
read -p "Enter your University Reg / User ID: " USER_ID
read -sp "Enter your Wi-Fi Portal Password: " USER_PASS
echo ""

if [ -z "$USER_ID" ] || [ -z "$USER_PASS" ]; then
    echo "❌ Error: ID and Password cannot be blank."
    exit 1
fi

echo ""
echo "Choose where to store credentials:"
echo "  1) macOS Keychain (Recommended - encrypted securely in Apple Keychain)"
echo "  2) Local ~/.vit_wifi.env file (stored with restricted 600 permissions)"
read -p "Select option [1/2, default: 1]: " STORAGE_CHOICE

STORAGE="keychain"
if [ "$STORAGE_CHOICE" = "2" ]; then
    STORAGE="env"
fi

"$PYTHON_BIN" "$INSTALL_DIR/wifi_login.py" set-credentials -u "$USER_ID" -p "$USER_PASS" --storage "$STORAGE"

# Step 3: Install LaunchAgent (Event-Driven on Wi-Fi connect)
echo ""
echo "Step 2: Install Background Service (Event-Driven)"
echo "-----------------------------------"
mkdir -p "$HOME/Library/LaunchAgents"

cat << PLIST_EOF > "$TARGET_PLIST"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.vitwifi</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$INSTALL_DIR/wifi_login.py</string>
        <string>login</string>
        <string>--quiet</string>
    </array>
    <key>WatchPaths</key>
    <array>
        <string>/var/run/resolv.conf</string>
        <string>/Library/Preferences/SystemConfiguration/preferences.plist</string>
    </array>
    <key>StandardOutPath</key>
    <string>$HOME/Library/Logs/vitwifi.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/Library/Logs/vitwifi.log</string>
</dict>
</plist>
PLIST_EOF

# Reload via modern bootstrap
launchctl bootout "gui/$USER_ID_NUM" "$TARGET_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$USER_ID_NUM" "$TARGET_PLIST"
echo "✅ Background Auto-Login installed (Event-driven: 0 background processes when idle)!"

# Step 4: Configure CLI shortcut
mkdir -p "$HOME/.local/bin"
cat << 'BIN_EOF' > "$HOME/.local/bin/vit-wifi"
#!/bin/bash
exec /usr/bin/python3 "$HOME/.vitwifi/wifi_login.py" "${@:-login}"
BIN_EOF
chmod +x "$HOME/.local/bin/vit-wifi"

# Step 5: Eliminate macOS Captive Portal Popup
echo ""
echo "Step 3: Eliminate Login Popup"
echo "-----------------------------------"
echo "By default, macOS pops up a browser window when connecting to campus Wi-Fi."
echo "Disabling this allows seamless, silent auto-login in the background."
read -p "Permanently disable macOS login popup? (requires sudo) [Y/n]: " DISABLE_POPUP_CHOICE

if [[ "$DISABLE_POPUP_CHOICE" =~ ^[Nn]$ ]]; then
    echo "Skipped. You can disable it anytime later with: vit-wifi disable-popup"
else
    echo "Configuring macOS..."
    if sudo defaults write /Library/Preferences/SystemConfiguration/com.apple.captive.control Active -boolean false 2>/dev/null; then
        echo "✅ macOS login popup permanently disabled!"
    else
        echo "⚠️  Could not disable popup automatically. Run 'vit-wifi disable-popup' anytime."
    fi
fi

echo ""
echo "=========================================================="
echo "🎉 Setup Complete!"
echo "=========================================================="
echo ""
"$PYTHON_BIN" "$INSTALL_DIR/wifi_login.py" status

echo "Next steps:"
echo "• When you connect to campus Wi-Fi, it will automatically log in silently!"
echo "• Test manually anytime: vit-wifi"
echo "• Check status:          vit-wifi status"
echo "• Turn popup off/on:     vit-wifi disable-popup / enable-popup"
echo "• View activity log:     tail -f ~/Library/Logs/vitwifi.log"
echo ""
