#!/usr/bin/env python3
"""
V2: Clean, Lightweight Wi-Fi Login Client for G-VIT
Zero popup suppression (handled natively by macOS defaults).
Rapid aggressive POST on connection.
"""

import time
import subprocess
import urllib.request
import urllib.parse
import ssl
import re

KEYCHAIN_SERVICE = "VIT-WiFi"
PORTAL_ENDPOINTS = [
    "http://172.16.1.1/cgi-bin/authlogin?URI=http://captive.apple.com/hotspot-detect.html",
    "http://phc.prontonetworks.com/cgi-bin/authlogin?URI=http://captive.apple.com/hotspot-detect.html",
]
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128.0.0.0 Safari/537.36"

def log_message(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)

def send_notification(message):
    try:
        safe_msg = message.replace('"', '\\"')
        apple_script = f'display notification "{safe_msg}" with title "VIT Wi-Fi"'
        subprocess.run(["osascript", "-e", apple_script], capture_output=True)
    except:
        pass

def get_credentials():
    try:
        res_user = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE], capture_output=True, text=True)
        if res_user.returncode == 0:
            match = re.search(r'"acct"<blob>="([^"]+)"', res_user.stdout + res_user.stderr)
            username = match.group(1) if match else None
            res_pass = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"], capture_output=True, text=True)
            password = res_pass.stdout.strip() if res_pass.returncode == 0 else None
            if username and password:
                return username, password
    except:
        pass
    return None, None

def is_campus_network():
    """Detects if we are on the campus network using IP subnet."""
    try:
        res = subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True, text=True)
        ip = res.stdout.strip()
        if ip.startswith("172.16.") or ip.startswith("172.") or ip.startswith("10."):
            return True
    except:
        pass
    return False

def is_internet_active():
    """Checks if Apple's hotspot test returns Success."""
    try:
        req = urllib.request.Request("http://captive.apple.com/hotspot-detect.html", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=1.5) as response:
            return "Success" in response.read().decode("utf-8", errors="ignore")
    except:
        return False

def do_login():
    """Rapidly fires the login payload until the network routes it."""
    username, password = get_credentials()
    if not username or not password:
        log_message("Error: Credentials not found in Keychain.")
        return False

    if is_internet_active():
        log_message("Internet is already active.")
        return True

    log_message(f"Authenticating as '{username}'...")
    
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
    }
    
    ctx = ssl._create_unverified_context()
    login_ok = False
    start_time = time.time()
    
    # Aggressively attempt POST for up to 20 seconds to cover wake-from-sleep delay
    while time.time() - start_time < 20:
        for endpoint in PORTAL_ENDPOINTS:
            try:
                req = urllib.request.Request(endpoint, data=post_data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=1.5, context=ctx) as response:
                    if response.getcode() == 200:
                        log_message(f"HTTP POST Success on {endpoint}")
                        login_ok = True
                        break
            except Exception:
                pass
        
        if login_ok:
            break
        time.sleep(1.0)
    
    # Verify
    if is_internet_active():
        log_message(f"SUCCESS: Connected to internet as {username}")
        send_notification(f"Connected to internet as {username}")
        return True
    
    log_message("Failed to connect.")
    return False

def run_daemon():
    log_message("🚀 V2 Wi-Fi Daemon Active. Waiting for campus connection...")
    was_on_campus = False
    
    while True:
        on_campus = is_campus_network()
        
        if on_campus and not was_on_campus:
            log_message("⚡ Campus Wi-Fi detected! Triggering login...")
            do_login()
            
        was_on_campus = on_campus
        time.sleep(2.0) # Check every 2 seconds

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        do_login()
    else:
        run_daemon()
