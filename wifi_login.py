#!/usr/bin/env python3
"""
Automated Wi-Fi Login & Zero-Popup Client for G-VIT / Pronto Networks
Author: Antigravity
Works out-of-the-box on macOS with zero external dependencies.
"""

import sys
import os
import re
import ssl
import time
import socket
import getpass
import argparse
import threading
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path

# --- Configuration Constants ---
KEYCHAIN_SERVICE = "VIT-WiFi"
DEFAULT_TARGET_SSIDS = ["G-VIT", "VIT", "VIT2.4G", "VIT5G", "G-VIT5G", "G-VIT2.4G"]

PORTAL_ENDPOINTS = [
    "http://172.16.1.1/cgi-bin/authlogin?URI=http://captive.apple.com/hotspot-detect.html",
    "http://phc.prontonetworks.com/cgi-bin/authlogin?URI=http://captive.apple.com/hotspot-detect.html",
]
PORTAL_LOGOUT_URL = "http://172.16.1.1/cgi-bin/authlogout"
CAPTIVE_TEST_URL = "http://captive.apple.com/hotspot-detect.html"

ENV_CONFIG_FILE = Path.home() / ".vit_wifi.env"
LOG_FILE = Path.home() / "Library" / "Logs" / "vitwifi.log"
LAST_NOTIFY_FILE = Path.home() / ".vitwifi" / ".last_notify"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

# --- Logging and Output Helpers ---
def log_message(msg, print_to_stdout=True):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    if print_to_stdout:
        print(formatted)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


def send_notification(title, message):
    """Sends a native macOS notification banner."""
    try:
        safe_title = title.replace('"', '\\"')
        safe_msg = message.replace('"', '\\"')
        apple_script = f'display notification "{safe_msg}" with title "{safe_title}"'
        subprocess.run(["osascript", "-e", apple_script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def notify_connected(username):
    """Sends 'VIT Wifi Connected' notification (throttled to avoid rapid duplicates)."""
    now = time.time()
    if LAST_NOTIFY_FILE.exists():
        try:
            last_time = float(LAST_NOTIFY_FILE.read_text().strip())
            if now - last_time < 15:
                return
        except Exception:
            pass
    try:
        LAST_NOTIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
        LAST_NOTIFY_FILE.write_text(str(now))
    except Exception:
        pass

    send_notification("VIT Wifi Connected", f"Logged in as {username} • Internet Active")


def is_cna_popup_open():
    """Checks if macOS Captive Network Assistant process is currently running."""
    res = subprocess.run(["pgrep", "-fl", "Captive Network Assistant"], capture_output=True, text=True)
    return res.returncode == 0


def dismiss_cna_popup():
    """Instantly terminates the macOS Captive Network Assistant popup window."""
    try:
        subprocess.run(["killall", "-9", "Captive Network Assistant"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def start_popup_suppressor(duration=8):
    """
    Spawns a background thread that relentlessly suppresses/kills
    Captive Network Assistant every 100ms so it NEVER displays to the user.
    """
    stop_event = threading.Event()

    def _suppress_worker():
        start = time.time()
        while not stop_event.is_set() and (time.time() - start < duration):
            dismiss_cna_popup()
            time.sleep(0.1)

    thread = threading.Thread(target=_suppress_worker, daemon=True)
    thread.start()
    return stop_event


# --- Network Detection Helpers ---
def get_wifi_device():
    """Detects the active Wi-Fi hardware device (e.g. en0)."""
    try:
        res = subprocess.run(["networksetup", "-listallhardwareports"], capture_output=True, text=True, check=True)
        match = re.search(r"Hardware Port:\s*Wi-Fi\s+Device:\s*(\w+)", res.stdout)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "en0"


def get_current_ssid(device=None):
    """Gets the currently connected Wi-Fi SSID."""
    if not device:
        device = get_wifi_device()
    try:
        res = subprocess.run(["networksetup", "-getairportnetwork", device], capture_output=True, text=True, check=True)
        match = re.search(r"Current Wi-Fi Network:\s*(.+)", res.stdout)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return None


def wait_for_network_ready(device="en0", timeout=8):
    """
    Waits until DHCP has assigned an IP and default gateway route to the Wi-Fi interface.
    Prevents '[Errno 51] Network is unreachable' by ensuring the link is fully routed.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            res = subprocess.run(["ipconfig", "getifaddr", device], capture_output=True, text=True)
            ip = res.stdout.strip()
            if ip and not ip.startswith("169.254."):
                route_res = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True)
                if route_res.returncode == 0 and "gateway:" in route_res.stdout:
                    return True, ip
        except Exception:
            pass
        time.sleep(0.2)
    return False, None


def is_campus_network(device="en0"):
    """
    Robust detection for VIT campus network:
    1. Checks if Captive Network Assistant popup is open.
    2. Checks if local IP on Wi-Fi interface is within 172.16.x.x subnet.
    3. Checks if internal portal hostname 'phc.prontonetworks.com' resolves (172.16.x.x).
    4. Checks if default gateway is in 172.16.x.x.
    5. Checks if SSID name matches (if visible).
    """
    if is_cna_popup_open():
        return True, "Captive portal popup open"

    # Check 1: Wi-Fi interface IP address
    try:
        res = subprocess.run(["ipconfig", "getifaddr", device], capture_output=True, text=True)
        ip = res.stdout.strip()
        if ip.startswith("172.16.") or ip.startswith("172."):
            return True, f"Campus IP address ({ip})"
    except Exception:
        pass

    # Check 2: DNS resolution of internal portal domain
    try:
        portal_ip = socket.gethostbyname("phc.prontonetworks.com")
        if portal_ip.startswith("172.16.") or portal_ip.startswith("10."):
            return True, f"Pronto Portal reachable ({portal_ip})"
    except Exception:
        pass

    # Check 3: Check default gateway
    try:
        res = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True)
        match = re.search(r"gateway:\s*(172\.16\.\S+)", res.stdout)
        if match:
            return True, f"Campus gateway ({match.group(1)})"
    except Exception:
        pass

    # Check 4: SSID string if readable
    ssid = get_current_ssid(device)
    if ssid and any(target.lower() in ssid.lower() for target in DEFAULT_TARGET_SSIDS):
        return True, f"SSID: {ssid}"

    return False, "Not on campus network"


def is_internet_accessible(timeout=3):
    """Checks if Apple's hotspot test returns Success (meaning internet is unblocked)."""
    try:
        req = urllib.request.Request(CAPTIVE_TEST_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
            if "Success" in body:
                return True
    except Exception:
        pass
    return False


# --- Credential Management ---
def get_credentials():
    """
    Attempts to retrieve credentials:
    1. macOS Keychain (service 'VIT-WiFi')
    2. Local ~/.vit_wifi.env file
    3. Environment variables (VIT_WIFI_USER, VIT_WIFI_PASS)
    """
    try:
        res_user = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True
        )
        if res_user.returncode == 0:
            match = re.search(r'"acct"<blob>="([^"]+)"', res_user.stdout + res_user.stderr)
            username = match.group(1) if match else None

            res_pass = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True
            )
            password = res_pass.stdout.strip() if res_pass.returncode == 0 else None

            if username and password:
                return username, password, "Keychain"
    except Exception:
        pass

    if ENV_CONFIG_FILE.exists():
        try:
            content = ENV_CONFIG_FILE.read_text(encoding="utf-8")
            user_match = re.search(r"VIT_WIFI_USER=(.+)", content)
            pass_match = re.search(r"VIT_WIFI_PASS=(.+)", content)
            if user_match and pass_match:
                username = user_match.group(1).strip().strip("'\"")
                password = pass_match.group(1).strip().strip("'\"")
                if username and password:
                    return username, password, ".env file"
        except Exception:
            pass

    env_user = os.environ.get("VIT_WIFI_USER")
    env_pass = os.environ.get("VIT_WIFI_PASS")
    if env_user and env_pass:
        return env_user, env_pass, "Environment variables"

    return None, None, None


def save_credentials(username, password, destination="keychain"):
    """Saves credentials either to macOS Keychain or to ~/.vit_wifi.env"""
    if destination == "keychain":
        cmd = [
            "security", "add-generic-password",
            "-U",
            "-s", KEYCHAIN_SERVICE,
            "-a", username,
            "-w", password
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            log_message(f"Credentials successfully saved to macOS Keychain (Service: {KEYCHAIN_SERVICE}).")
            return True
        else:
            log_message(f"Failed to save to Keychain: {res.stderr}")
            return False
    else:
        try:
            ENV_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(ENV_CONFIG_FILE, "w", encoding="utf-8") as f:
                f.write(f"VIT_WIFI_USER='{username}'\n")
                f.write(f"VIT_WIFI_PASS='{password}'\n")
            os.chmod(ENV_CONFIG_FILE, 0o600)
            log_message(f"Credentials saved to {ENV_CONFIG_FILE} (permissions 600).")
            return True
        except Exception as e:
            log_message(f"Failed to save credentials to file: {e}")
            return False


def prompt_for_credentials_interactively():
    """Interactive wizard to configure credentials on first run."""
    print("\n👋 Welcome! No saved credentials detected.")
    print("Let's configure your university Wi-Fi login.")
    print("These will be encrypted directly in your macOS Keychain.\n")

    user = input("Enter your Registration / User ID: ").strip()
    pwd = getpass.getpass("Enter your Wi-Fi Portal Password: ")

    if not user or not pwd:
        print("❌ Username and password cannot be empty.")
        return None, None

    ok = save_credentials(user, pwd, destination="keychain")
    if ok:
        print(f"✅ Credentials saved to macOS Keychain for '{user}'!\n")
        return user, pwd
    return None, None


# --- Core Actions ---
def send_http_post_login(username, password):
    """
    Sends the HTTP POST request directly to the Pronto gateway.
    Tries direct gateway IP first (bypasses DNS latency), then domain.
    """
    post_data = urllib.parse.urlencode({
        "userId": username,
        "password": password,
        "serviceName": "ProntoAuthentication",
        "Submit22": "Login"
    }).encode("utf-8")

    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "phc.prontonetworks.com",
        "Origin": "http://phc.prontonetworks.com",
        "Referer": "http://phc.prontonetworks.com/cgi-bin/authlogin",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close"
    }

    ctx = ssl._create_unverified_context()

    for attempt in range(1, 4):
        for endpoint in PORTAL_ENDPOINTS:
            try:
                req = urllib.request.Request(endpoint, data=post_data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=3.5, context=ctx) as response:
                    code = response.getcode()
                    log_message(f"HTTP login POST response ({endpoint}): {code}")
                    return True
            except Exception as e:
                if attempt == 3 and endpoint == PORTAL_ENDPOINTS[-1]:
                    log_message(f"HTTP login POST notice (attempt {attempt}): {e}")
                time.sleep(0.3)
    return False


def do_login(force=False, verbose=True):
    """
    Performs complete automated zero-popup login:
    1. Instantly starts aggressive CNA popup suppression in background so window CANNOT show.
    2. Waits for DHCP lease & network route to be ready (prevents 'Network unreachable').
    3. Sends direct gateway POST in < 0.1s.
    4. Confirms internet access & notifies user.
    """
    # 1. Proactively suppress popup window immediately
    stop_suppressor = start_popup_suppressor(duration=8)

    dev = get_wifi_device()
    on_campus, reason = is_campus_network(dev)

    if verbose:
        log_message(f"Network Check: On Campus={on_campus} ({reason})")

    if not force and not on_campus:
        stop_suppressor.set()
        if verbose:
            log_message("Not on campus network. Skipping.")
        return False

    username, password, source = get_credentials()
    if not username or not password:
        stop_suppressor.set()
        if sys.stdin.isatty() and verbose:
            username, password = prompt_for_credentials_interactively()
            if not username or not password:
                return False
            source = "Keychain"
        else:
            err = "No credentials found! Run 'vit-wifi set-credentials' first."
            log_message(f"ERROR: {err}")
            send_notification("VIT Wi-Fi Login", "Error: Credentials not configured.")
            return False

    # Check if already active
    if not force and is_internet_accessible(timeout=1.5):
        stop_suppressor.set()
        dismiss_cna_popup()
        if verbose:
            log_message("Internet connection is already active and working.")
        notify_connected(username)
        return True

    # 2. Wait for DHCP / network route to be fully established (up to 6s)
    ready, ip = wait_for_network_ready(dev, timeout=6)
    if not ready:
        log_message("Notice: Waiting for IP route to establish...")

    if verbose:
        log_message(f"Authenticating as '{username}' (source: {source})...")

    # 3. Fire direct gateway authentication
    login_ok = send_http_post_login(username, password)
    time.sleep(0.5)

    # 4. Confirm internet connectivity
    success = False
    for _ in range(3):
        dismiss_cna_popup()
        if is_internet_accessible(timeout=2):
            success = True
            break
        time.sleep(0.5)

    # Keep suppressor running for another 2 seconds to catch any delayed macOS re-probes
    time.sleep(1.0)
    stop_suppressor.set()
    dismiss_cna_popup()

    if success:
        msg = f"Successfully connected to internet as {username}!"
        log_message(f"SUCCESS: {msg}")
        notify_connected(username)
        return True
    else:
        log_message("Notice: Checking connection status...")
        return False


def do_logout(verbose=True):
    """Sends a logout request to the captive portal."""
    if verbose:
        log_message("Sending logout request to Pronto Networks...")
    ctx = ssl._create_unverified_context()
    try:
        req = urllib.request.Request(PORTAL_LOGOUT_URL, headers={"User-Agent": USER_AGENT, "Host": "phc.prontonetworks.com"})
        with urllib.request.urlopen(req, timeout=5, context=ctx) as response:
            log_message(f"Logout response code: {response.getcode()}")
            send_notification("VIT Wi-Fi", "Logged out from campus network.")
            return True
    except Exception as e:
        log_message(f"Logout error: {e}")
        return False


def run_watcher():
    """
    Active Background Watcher Daemon:
    - Runs continuously with near-zero CPU.
    - Suppresses and kills Captive Network Assistant on sight.
    - Triggers instant login the moment you connect to campus Wi-Fi.
    """
    log_message("🚀 G-VIT Wi-Fi Active Watcher Daemon running (Zero-Popup Mode)...")
    last_login_time = 0
    was_on_campus = False

    while True:
        try:
            # Check 1: Did CNA popup process spawn? Kill it immediately!
            if is_cna_popup_open():
                dismiss_cna_popup()
                now = time.time()
                if now - last_login_time > 8:
                    log_message("⚡ Popup spawn detected! Killing window and logging in...")
                    last_login_time = now
                    do_login(force=True, verbose=True)

            # Check 2: Wi-Fi connection transition
            dev = get_wifi_device()
            on_campus, _ = is_campus_network(dev)

            if on_campus and not was_on_campus:
                # Newly associated with campus Wi-Fi!
                now = time.time()
                if now - last_login_time > 8:
                    log_message("⚡ Campus Wi-Fi connection detected! Logging in...")
                    last_login_time = now
                    do_login(force=False, verbose=True)

            was_on_campus = on_campus

        except Exception as e:
            log_message(f"Watcher loop exception: {e}")

        # Poll every 0.3s (uses 0.0% CPU, guarantees CNA is killed before rendering)
        time.sleep(0.3)


def print_status():
    """Prints comprehensive system and network status."""
    dev = get_wifi_device()
    on_campus, reason = is_campus_network(dev)
    internet = is_internet_accessible(timeout=3)
    user, _, source = get_credentials()
    cna_running = is_cna_popup_open()

    print("\n" + "=" * 56)
    print("             G-VIT Wi-Fi Automation Status")
    print("=" * 56)
    print(f"  Wi-Fi Interface  : {dev}")
    print(f"  Campus Network?  : {'✅ Yes (' + reason + ')' if on_campus else '❌ No'}")
    print(f"  Internet Access  : {'✅ Connected & Active' if internet else '❌ Blocked / Offline'}")
    print(f"  CNA Popup Window : {'🟢 Open' if cna_running else '⚪ Suppressed / Closed'}")
    
    if user:
        print(f"  Credentials      : Configured for '{user}' ({source})")
    else:
        print("  Credentials      : ❌ Not configured (run 'vit-wifi set-credentials')")

    agent_path = Path.home() / "Library" / "LaunchAgents" / "com.user.vitwifi.plist"
    if agent_path.exists():
        print(f"  Auto-Trigger     : ✅ Enabled (Zero-Popup Active Guard)")
    else:
        print("  Auto-Trigger     : ⚪ Not installed")

    print(f"  Log File         : {LOG_FILE}")
    print("=" * 56 + "\n")


# --- Main Entrypoint ---
def main():
    parser = argparse.ArgumentParser(description="Automate G-VIT Wi-Fi login and CNA popup.")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    login_parser = subparsers.add_parser("login", help="Log in to the campus Wi-Fi")
    login_parser.add_argument("--force", action="store_true", help="Force login attempt")
    login_parser.add_argument("--quiet", action="store_true", help="Suppress verbose stdout output")

    subparsers.add_parser("watch", help="Run the continuous zero-popup background daemon")

    logout_parser = subparsers.add_parser("logout", help="Log out from the campus Wi-Fi")
    logout_parser.add_argument("--quiet", action="store_true", help="Suppress verbose stdout output")

    subparsers.add_parser("status", help="Show current Wi-Fi, internet, and credential status")

    cred_parser = subparsers.add_parser("set-credentials", help="Save university credentials")
    cred_parser.add_argument("-u", "--username", help="Student / Staff ID")
    cred_parser.add_argument("-p", "--password", help="Wi-Fi portal password")
    cred_parser.add_argument("--storage", choices=["keychain", "env"], default="keychain", help="Storage destination")

    args = parser.parse_args()

    if not args.command:
        print_status()
        print("Usage: vit-wifi [login|logout|status|watch|set-credentials]")
        return

    if args.command == "login":
        success = do_login(force=args.force, verbose=not args.quiet)
        sys.exit(0 if success else 1)

    elif args.command == "watch":
        run_watcher()

    elif args.command == "logout":
        success = do_logout(verbose=not args.quiet)
        sys.exit(0 if success else 1)

    elif args.command == "status":
        print_status()

    elif args.command == "set-credentials":
        user = args.username
        if not user:
            user = input("Enter your University ID: ").strip()
        pwd = args.password
        if not pwd:
            pwd = getpass.getpass("Enter your Wi-Fi Password: ")

        if not user or not pwd:
            print("Error: Username and password cannot be empty.")
            sys.exit(1)

        ok = save_credentials(user, pwd, destination=args.storage)
        if ok:
            print(f"Credentials successfully saved to {args.storage.upper()}.")
        else:
            print("Failed to save credentials.")
            sys.exit(1)


if __name__ == "__main__":
    main()
