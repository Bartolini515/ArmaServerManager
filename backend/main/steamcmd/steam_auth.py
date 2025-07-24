import time
import hmac
import hashlib
import base64
from struct import pack, unpack
from ..utils.config import config
from ..utils.logger import Logger

def load_credentials() -> tuple[str, str]:
    """Loads Steam authentication credentials.

    Returns:
        tuple[str, str]: A tuple containing the username and password.
    """
    username = config.get("steam_auth.username")
    password = config.get("steam_auth.password")
    return username, password

def generate_steam_guard_code(shared_secret: str) -> str:
    """Generates a 5-character Steam Guard code.

    Args:
        shared_secret (str): The shared secret used to generate the code.

    Returns:
        str: A 5-character Steam Guard code.
    """
    if not shared_secret:
        return ""

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
    except Exception as e:
        Logger.error(f"Failed to generate Steam Guard code: {e}")
        return ""