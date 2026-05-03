"""
ChronoScope AI — Final System Check
Runs before any demo or buyer meeting.
Verifies everything is working correctly.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import subprocess
import requests
from datetime import datetime, timezone

def check(label, fn):
    try:
        result = fn()
        status = "✓" if result else "✗"
        color = "\033[92m" if result else "\033[91m"
        print(f"  {color}{status}\033[0m  {label}")
        return result
    except Exception as e:
        print(f"  \033[91m✗\033[0m  {label} — {e}")
        return False

def run():
    print("\nChronoScope AI — Pre-Demo System Check")
    print("=" * 50)
    passed = 0
    total = 0

    checks = [
        ("Python environment active", lambda: True),
        ("NOAA DSCOVR API reachable", lambda: requests.get(
            "https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json",
            timeout=10).status_code == 200),
        ("OpenSky Network reachable", lambda: requests.get(
            "https://opensky-network.org/api/states/all?lamin=24&lamax=72&lomin=-140&lomax=-52",
            timeout=15).status_code == 200),
        ("CelesTrak reachable", lambda: requests.get(
            "https://celestrak.org/satcat/records.php?CATNR=25544&FORMAT=json",
            timeout=10).status_code == 200),
        ("ChronoScope API running", lambda: requests.get(
            "http://localhost:8000/", timeout=5).status_code == 200),
        ("Dashboard accessible", lambda: requests.get(
            "http://localhost:8000/dashboard", timeout=5).status_code == 200),
        ("World map accessible", lambda: requests.get(
            "http://localhost:8000/map", timeout=5).status_code == 200),
        ("API docs accessible", lambda: requests.get(
            "http://localhost:8000/docs", timeout=5).status_code == 200),
    ]

    print()
    for label, fn in checks:
        result = check(label, fn)
        total += 1
        if result:
            passed += 1

    print()
    print("=" * 50)
    print(f"  {passed}/{total} checks passed")

    if passed == total:
        print("  \033[92mAll systems go. Ready for demo.\033[0m")
    elif passed >= total - 2:
        print("  \033[93mMost systems ready. Check failures above.\033[0m")
    else:
        print("  \033[91mSystem not ready. Fix failures before demo.\033[0m")

    print()
    print("  Dashboard:  http://localhost:8000/dashboard")
    print("  World Map:  http://localhost:8000/map")
    print("  API Docs:   http://localhost:8000/docs")
    print("  Sale Demo:  python scripts/sale_demo.py")
    print("=" * 50 + "\n")

    return passed == total

if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)