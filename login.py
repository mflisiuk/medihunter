#!/usr/bin/env python3
"""Standalone login script — run this directly in terminal to authenticate.

Usage:
    python3 login.py

You will be prompted for MFA SMS code if needed.
The 'Trusted device' checkbox is auto-checked, so next login won't need SMS.
"""

import json
import sys
import os

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth import login_playwright, _save_tokens, _load_credentials, save_credentials


def main():
    creds = _load_credentials()
    if not creds:
        card = input("Numer karty Medicover: ").strip()
        pwd = input("Hasło: ").strip()
        save_credentials(card, pwd)
        creds = _load_credentials()

    print(f"\nLogowanie karta {creds['card_number']}...")
    print("Jeśli wymagane MFA, wpisz kod SMS z telefonu.")
    print("Pole 'Zaufane urządzenie' zostanie automatycznie zaznaczone.\n")

    try:
        tokens = login_playwright(creds["card_number"], creds["password"], headless=True)
        _save_tokens(tokens)
        print(f"\nZALOGOWANO POMYŚLNIE!")
        print(f"  access_token: {tokens['access_token'][:50]}...")
        print(f"  refresh_token: {tokens['refresh_token'][:30]}...")
        print(f"  expires_in: {tokens.get('expires_in', '?')}s")
        print(f"\nTokeny zapisane. Możesz teraz uruchomić monitor.py")
    except Exception as e:
        print(f"\nLOGOWANIE NIEUDANE: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
