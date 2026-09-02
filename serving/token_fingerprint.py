r"""Show which token *this* terminal would use, without printing it.

    .\.venv\Scripts\python.exe serving\token_fingerprint.py

Compare the fingerprint with the server's startup line. Matching means the two agree; differing
is the whole problem, and the "read from" line says which of the two sources won.
"""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import config

print(f"fingerprint = {config.token_fingerprint()}   read from {config.AUTH_TOKEN_SOURCE}")

if not config.AUTH_TOKEN:
    print("\nNo token anywhere. Create one:")
    print(r"    .\.venv\Scripts\python.exe serving\new_token.py")
    sys.exit(1)

print(f"length = {len(config.AUTH_TOKEN)} chars")

if config.AUTH_TOKEN_SOURCE == "$env:AUTH_TOKEN":
    print("\nA shell variable is overriding serving/.token — probably left over from an earlier")
    print("session in this window. To fall back to the file:")
    print(r"    Remove-Item Env:\AUTH_TOKEN")
    if config.TOKEN_FILE.exists():
        on_disk = config.TOKEN_FILE.read_text(encoding="utf-8").strip()
        if on_disk and on_disk != config.AUTH_TOKEN:
            print(f"\n    the file holds a different token: {config.token_fingerprint(on_disk)}")
