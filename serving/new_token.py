r"""Generate a fresh bearer token into serving/.token. Prints the fingerprint, never the token.

    .\.venv\Scripts\python.exe serving\new_token.py

Both the server and smoke_test.py read that file, so there is nothing to copy between terminals
and nothing to get out of step. To hand the token to someone, print it deliberately:

    Get-Content serving\.token | Set-Clipboard
"""
import secrets
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import config

if config.TOKEN_FILE.exists() and "--force" not in sys.argv:
    print(f"{config.TOKEN_FILE} already exists (fingerprint {config.token_fingerprint()}).")
    print("Pass --force to replace it. Replacing it invalidates the token your colleague holds.")
    sys.exit(1)

token = secrets.token_urlsafe(32)
config.TOKEN_FILE.write_text(token, encoding="utf-8")
print(f"wrote {config.TOKEN_FILE}")
print(f"fingerprint = {config.token_fingerprint(token)}   ({len(token)} chars)")
print("\nThe token itself was not printed. To copy it for your colleague:")
print(r"    Get-Content serving\.token | Set-Clipboard")
