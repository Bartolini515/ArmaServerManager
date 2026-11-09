import time
import hmac
import hashlib
import base64
from struct import pack, unpack
from ..utils.operation_logging import LogCallback
from ..utils.steam_errors import SteamCommandError, SteamSecretRedactor, safe_steam_diagnostic

def load_credentials(config_snapshot: dict | None = None) -> tuple[str, str]:
    """Loads Steam authentication credentials.

    Args:
        config_snapshot (dict, optional): One already-captured application
            configuration snapshot. Defaults to a fresh snapshot.

    Returns:
        tuple[str, str]: A tuple containing the username and password.
    """
    from ..utils.config import config

    snapshot = config_snapshot if config_snapshot is not None else config.snapshot()
    steam_auth = snapshot.get("steam_auth", {})
    username = steam_auth.get("username", "")
    password = steam_auth.get("password", "")
    return username, password

def generate_steam_guard_code(
    shared_secret: str,
    error_callback: LogCallback | None = None,
) -> str:
    """Generates a 5-character Steam Guard code.

    Args:
        shared_secret (str): The shared secret used to generate the code.
        error_callback (LogCallback, optional): Callback used to report
            generation errors. Defaults to None.

    Returns:
        str: A 5-character Steam Guard code.
    """
    if not shared_secret:
        return ""

    redactor = SteamSecretRedactor((shared_secret,))

    error_message: str | None = None
    try:
        # Decode the shared secret from Base64
        secret_bytes = base64.b64decode(shared_secret)

        # Get the current time in 30-second intervals
        current_time = int(time.time())
        time_buffer = pack('>Q', current_time // 30)  # Pack as 8-byte big-endian

        # Create the HMAC-SHA1 hash
        h = hmac.new(secret_bytes, time_buffer, hashlib.sha1)
        hashed_data = h.digest()

        # Truncate the hash to get the code value
        offset = hashed_data[-1] & 0x0F
        code_bytes = hashed_data[offset:offset + 4]
        
        # Unpack the 4 bytes and clear the most significant bit
        full_code = unpack('>I', code_bytes)[0] & 0x7FFFFFFF

        # The character set Steam uses for codes
        steam_guard_chars = "23456789BCDFGHJKMNPQRTVWXY"
        code = ""
        for _ in range(5):
            code += steam_guard_chars[full_code % len(steam_guard_chars)]
            full_code //= len(steam_guard_chars)

        return code
    except Exception:
        # Keep the message useful without serializing exception details that
        # could contain the shared secret.
        error_message = "Failed to generate Steam Guard code."

    if error_callback and error_message is not None:
        try:
            error_callback(error_message)
        except Exception as exc:
            raise SteamCommandError(
                "Nie udało się zapisać informacji o Steam Guard.",
                diagnostic=safe_steam_diagnostic(exc, redactor),
            ) from None
    return ""
