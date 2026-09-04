# uni-wifi-login-client

A lightweight macOS utility that automatically connects and authenticates to university campus Wi-Fi without manual logins or popups.

## Features

- **Automatic Login**: Connects you to the internet seamlessly when joining the campus network.
- **No Popups**: Handles the captive portal prompt in the background.
- **Secure**: Credentials are encrypted directly inside your macOS Keychain.
- **Lightweight**: No background apps running, zero battery impact.

## Setup

1. Clone the repository:
```bash
git clone https://github.com/AzimAbdulla10/uni-wifi-login-client.git
cd uni-wifi-login-client
```

2. Run setup:
```bash
./setup.sh
```

Enter your university ID and password when prompted. That's it!

## Usage

The tool works automatically whenever you connect to Wi-Fi.

You can also use the terminal shortcut anytime:
```bash
vit-wifi          # Trigger login manually
vit-wifi status   # Check current status
```
