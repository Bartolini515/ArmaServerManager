"""Helpers for keeping Steam authentication values out of diagnostics."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import json
import re
import subprocess
from typing import TypeAlias


LogCallback: TypeAlias = Callable[[str], None]
_STEAM_GUARD_CHARACTERS = frozenset("23456789BCDFGHJKMNPQRTVWXY")
_REDACTION_CANDIDATES = (
    "***",
    "~~~",
    "|||",
    ":::",
    "___",
    "---",
    "+++",
    "%%%",
    "&&&",
    "@@@",
    "###",
    "•••",
    "■■■",
)


class SteamSecretRedactor:
    """Redact Steam authentication values from text before it reaches a sink."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        self._marker = _REDACTION_CANDIDATES[0]
        for secret in secrets:
            self.add_secret(secret)

    def add_secret(self, secret: str | None) -> None:
        if secret:
            self._secrets.add(str(secret))
            self._marker = self._choose_marker()

    @property
    def marker(self) -> str:
        return self._marker

    @property
    def has_secrets(self) -> bool:
        return bool(self._secrets)

    def redact(self, value: object) -> str:
        """Return safe text, replacing every known credential with a marker."""
        try:
            message = str(value)
        except Exception:
            return "[diagnostic unavailable]"

        if not self._secrets:
            return message

        spellings = self._secret_spellings()

        alternatives = "|".join(
            re.escape(secret)
            for secret in sorted(spellings, key=len, reverse=True)
            if secret
        )
        return re.sub(alternatives, lambda _: self._marker, message)

    def _secret_spellings(self) -> set[str]:
        spellings: set[str] = set()
        for secret in self._secrets:
            spellings.add(secret)
            spellings.add(repr(secret)[1:-1])
            spellings.add(ascii(secret)[1:-1])
            spellings.add(json.dumps(secret, ensure_ascii=True)[1:-1])
            spellings.add(secret.encode("unicode_escape").decode("ascii"))
        spellings.discard("")
        return spellings

    def _choose_marker(self) -> str:
        spellings = self._secret_spellings()
        for candidate in _REDACTION_CANDIDATES:
            if any(character in _STEAM_GUARD_CHARACTERS for character in candidate):
                continue
            if all(
                candidate not in spelling and spelling not in candidate
                for spelling in spellings
            ):
                return candidate

        for codepoint in range(0xE000, 0xF900):
            candidate = chr(codepoint)
            if candidate in _STEAM_GUARD_CHARACTERS:
                continue
            if all(
                candidate not in spelling and spelling not in candidate
                for spelling in spellings
            ):
                return candidate
        # This requires all private-use code points to appear in the Steam
        # credentials; choose a supplementary private-use fallback as well.
        for codepoint in range(0xF0000, 0xFFFFE):
            candidate = chr(codepoint)
            if all(
                candidate not in spelling and spelling not in candidate
                for spelling in spellings
            ):
                return candidate
        return "[redacted]"

    def callback(self, callback: LogCallback | None) -> LogCallback | None:
        """Wrap a logging callback so callers receive only sanitized messages."""
        if callback is None:
            return None

        def safe_callback(message: str) -> None:
            safe_message = self.redact(message)
            try:
                callback(safe_message)
            except Exception as exc:
                diagnostic = safe_steam_diagnostic(exc, self)
                raise SteamCommandError(
                    "Nie udało się zapisać informacji o pobieraniu SteamCMD.",
                    diagnostic=diagnostic,
                ) from None

        return safe_callback

    @classmethod
    def from_config(cls) -> SteamSecretRedactor:
        """Build a redactor from the current Steam configuration values."""
        from .config import config

        return cls(
            (
                config.get("steam_auth.username", ""),
                config.get("steam_auth.password", ""),
                config.get("steam_auth.shared_secret", ""),
            )
        )


class SteamCommandError(Exception):
    """A safe public error with a separate sanitized diagnostic for logs."""

    def __init__(self, message: str, *, diagnostic: str = "") -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


def safe_exception_details(
    exception: BaseException,
    redactor: SteamSecretRedactor,
) -> str | None:
    """Return safe structured details without formatting exception arguments."""
    if isinstance(exception, subprocess.TimeoutExpired):
        try:
            timeout = exception.timeout
        except Exception:
            timeout = None
        timeout_text = (
            str(timeout)
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
            else "unknown"
        )
        return redactor.redact(f"timeout={timeout_text}")

    if isinstance(exception, subprocess.CalledProcessError):
        try:
            returncode = exception.returncode
        except Exception:
            returncode = None
        returncode_text = (
            str(returncode)
            if isinstance(returncode, int) and not isinstance(returncode, bool)
            else "unknown"
        )
        return redactor.redact(f"returncode={returncode_text}")

    if isinstance(exception, OSError):
        try:
            error_number = exception.errno
        except Exception:
            error_number = None
        if isinstance(error_number, int) and not isinstance(error_number, bool):
            return redactor.redact(f"errno={error_number}")
        return "operating system error"

    return None


def safe_exception_type(exception: BaseException) -> str:
    """Map exception classes to a small allowlist without using class names."""
    if isinstance(exception, subprocess.TimeoutExpired):
        return "TimeoutExpired"
    if isinstance(exception, subprocess.CalledProcessError):
        return "CalledProcessError"
    if isinstance(exception, OSError):
        return "OSError"
    if isinstance(exception, SteamCommandError):
        return "SteamCommandError"
    if type(exception) is RuntimeError:
        return "RuntimeError"
    if type(exception) is ValueError:
        return "ValueError"
    if type(exception) is TypeError:
        return "TypeError"
    return "Exception"


def safe_steam_diagnostic(
    exception: BaseException,
    redactor: SteamSecretRedactor,
) -> str:
    """Format diagnostics without including subprocess command arguments."""
    details = safe_exception_details(exception, redactor)
    exception_type = safe_exception_type(exception)
    if details is not None:
        return redactor.redact(f"{exception_type}: {details}")

    return redactor.redact(f"{exception_type}: details omitted")


def steam_command_error(
    exception: BaseException,
    redactor: SteamSecretRedactor,
    message: str = "Nie udało się uruchomić SteamCMD. Sprawdź logi operacji.",
) -> SteamCommandError:
    """Create a typed failure without putting the unsafe command in its text."""
    return SteamCommandError(
        message,
        diagnostic=safe_steam_diagnostic(exception, redactor),
    )
