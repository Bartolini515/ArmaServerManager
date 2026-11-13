import copy
import math
import os
import subprocess
import threading
from time import monotonic, sleep, time
from typing import Any, Callable
from ..download_leases import LeaseLostError
from ..utils.process_output import stream_process_output
from ..utils.process_output import ProcessOutputCancelledError
from ..utils.config import config
from ..utils.operation_logging import LogCallback
from ..utils.steam_errors import (
    SteamCommandError,
    SteamSecretRedactor,
    safe_steam_diagnostic,
)
from ..workaround.cache_deletion import delete_steamcmd_appcache
from ..workaround.ghost_folder import GhostFolder
from ..workaround.modsrenamer import lowercase_addons_directory
from .steam_auth import load_credentials, generate_steam_guard_code
from .processes import CleanupUncertainError, start_process, terminate_owned_process_tree

STEAM_CONNECTION_TIMEOUT_SECONDS = 60.0


class DownloadResult(list[str]):
    """Legacy failed-ID list with safe filesystem cleanup status metadata."""

    def __init__(self, failed_ids=(), *, cleanup_pending: bool = False) -> None:
        super().__init__(failed_ids)
        self.cleanup_pending = bool(cleanup_pending)
        self.cleanup_reason = "filesystem_cleanup_pending" if cleanup_pending else None

    def mark_cleanup_pending(self) -> None:
        self.cleanup_pending = True
        self.cleanup_reason = "filesystem_cleanup_pending"


def _require_not_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is None:
        return
    try:
        cancelled = bool(cancel_check())
    except Exception:
        cancelled = True
    if cancelled:
        raise LeaseLostError() from None


def _before_shared_write(
    cancel_check: Callable[[], bool] | None,
    before_shared_write: Callable[[], None] | None,
) -> None:
    _require_not_cancelled(cancel_check)
    if before_shared_write is not None:
        before_shared_write()


def _cancel_aware_callback(
    callback: Callable[..., Any] | None,
    cancel_check: Callable[[], bool] | None,
) -> Callable[..., Any] | None:
    if callback is None or cancel_check is None:
        return callback

    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            if cancel_check():
                return None
        except Exception:
            return None
        return callback(*args, **kwargs)

    return guarded


def _cancellation_kwargs(
    cancel_check: Callable[[], bool] | None,
    before_shared_write: Callable[[], None] | None = None,
) -> dict[str, Callable[..., Any]]:
    kwargs: dict[str, Callable[..., Any]] = {}
    if cancel_check is not None:
        kwargs["cancel_check"] = cancel_check
    if before_shared_write is not None:
        kwargs["before_shared_write"] = before_shared_write
    return kwargs


def _interruptible_wait(
    seconds: float,
    cancel_check: Callable[[], bool] | None,
) -> None:
    if cancel_check is None:
        sleep(seconds)
        return
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        _require_not_cancelled(cancel_check)
        interval = min(0.25, remaining)
        sleep(interval)
        remaining -= interval
    _require_not_cancelled(cancel_check)


def assign_new_steamguard(
    steamguard: str | None,
    buffer_seconds: int = 7,
    log_callback: LogCallback | None = None,
    error_callback: LogCallback | None = None,
    secret_redactor: SteamSecretRedactor | None = None,
    config_snapshot: dict[str, Any] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Generate a new Steam Guard code using the supplied config snapshot."""
    snapshot = _configuration_snapshot(config_snapshot)
    steam_auth = snapshot.get("steam_auth", {})
    redactor = secret_redactor or SteamSecretRedactor(
        (
            steam_auth.get("username", ""),
            steam_auth.get("password", ""),
            steam_auth.get("shared_secret", ""),
        )
    )
    redactor.add_secret(steam_auth.get("username", ""))
    redactor.add_secret(steam_auth.get("password", ""))
    redactor.add_secret(steam_auth.get("shared_secret", ""))
    safe_log_callback = redactor.callback(log_callback)
    safe_error_callback = redactor.callback(error_callback)
    safe_log_callback = _cancel_aware_callback(safe_log_callback, cancel_check)
    safe_error_callback = _cancel_aware_callback(safe_error_callback, cancel_check)

    def _get_time_until_next_steamguard_change() -> float:
        """Get the time in seconds until the next Steam Guard code change.

        Returns:
            float: Seconds until next code change (0-30).
        """
        return 30 - (time() % 30)
    def _new_steamguard_code() -> str:
        """Generate a new Steam Guard code.

        Returns:
            str: The new Steam Guard code.
        """
        return generate_steam_guard_code(
            steam_auth.get("shared_secret", ""),
            error_callback=safe_error_callback,
        )

    # Generate a new Steam Guard code, ensuring it's different from the previous one
    for _ in range(10):  # Retry up to 10 times
        _require_not_cancelled(cancel_check)
        new_code = _new_steamguard_code()
        if new_code != steamguard or new_code == "":
            redactor.add_secret(new_code)
            return new_code
        time_until_change = _get_time_until_next_steamguard_change() + buffer_seconds
        if safe_log_callback:
            safe_log_callback(f"Waiting {time_until_change:.1f}s to generate new Steam Guard code...")
        _interruptible_wait(time_until_change, cancel_check)
    return ""

def download_mods(
    mods_to_download: list[str],
    name: str,
    log_callback: LogCallback | None = None,
    error_callback: LogCallback | None = None,
    lim: int = 10,
    progress_callback: Callable[[int, int], None] | None = None,
    secret_redactor: SteamSecretRedactor | None = None,
    config_snapshot: dict[str, Any] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    before_shared_write: Callable[[], None] | None = None,
) -> list[str] | None:
    """Downloads mods using SteamCMD.

    Args:
        mods_to_download (list[str]): The list of workshop IDs for the mods to download.
        name (str): The name of the mod set being downloaded.
        log_callback (LogCallback, optional): Callback for informational
            download messages. Defaults to None.
        error_callback (LogCallback, optional): Callback for download errors.
            Defaults to None.
        lim (int): The maximum number of downloads in one batch.
        progress_callback (Callable[[int, int], None], optional): Callback
            function to report download progress.
        config_snapshot (dict, optional): One stable application configuration
            snapshot to use for paths, credentials and Steam Guard generation.
        cancel_check (callable, optional): Returns whether the current lease
            has been lost, including while a subprocess produces no output.
        before_shared_write (callable, optional): Synchronously renews the
            lease and verifies the durable claim before a shared mutation.

    Returns:
        list[str]: A list containing mods that failed to download, if any.

    """
    snapshot = _configuration_snapshot(config_snapshot)
    failed_mods = []
    appid = 107410  # Arma 3 appid
    paths = snapshot.get("paths", {})
    steam_auth = snapshot.get("steam_auth", {})
    mods_dir = paths.get("mods_directory", "")
    download_dir = paths.get("download_directory", "")
    steamcmd_dir = paths.get("steamcmd", "")
    steamguard = None

    login, password = load_credentials(snapshot)
    redactor = secret_redactor or SteamSecretRedactor(
        (
            steam_auth.get("username", ""),
            steam_auth.get("password", ""),
            steam_auth.get("shared_secret", ""),
        )
    )
    redactor.add_secret(steam_auth.get("username", ""))
    redactor.add_secret(steam_auth.get("password", ""))
    redactor.add_secret(steam_auth.get("shared_secret", ""))
    redactor.add_secret(login)
    redactor.add_secret(password)
    safe_log_callback = redactor.callback(log_callback)
    safe_error_callback = redactor.callback(error_callback)
    safe_log_callback = _cancel_aware_callback(safe_log_callback, cancel_check)
    safe_error_callback = _cancel_aware_callback(safe_error_callback, cancel_check)
    steamguard = assign_new_steamguard(
        steamguard=steamguard,
        log_callback=safe_log_callback,
        error_callback=safe_error_callback,
        secret_redactor=redactor,
        config_snapshot=snapshot,
        **_cancellation_kwargs(cancel_check),
    )
    redactor.add_secret(steamguard)
    if safe_log_callback:
        safe_log_callback('Loaded login credentials.')
    
    _before_shared_write(cancel_check, before_shared_write)
    delete_steamcmd_appcache(steamcmd_dir, safe_log_callback)

    _before_shared_write(cancel_check, before_shared_write)
    ghost_folder = GhostFolder(
        name=name,
        path=download_dir,
        log_callback=safe_log_callback,
        **_cancellation_kwargs(cancel_check, before_shared_write),
    )
    primary_failure = False
    cleanup_uncertain = False
    claimed_success = False
    result: DownloadResult | None = None
    try:
        _before_shared_write(cancel_check, before_shared_write)
        if not test_connection(
            steamcmd_dir,
            login,
            password,
            steamguard,
            safe_log_callback,
            secret_redactor=redactor,
            **_cancellation_kwargs(cancel_check, before_shared_write),
        ):
            raise Exception("Nie udało się połączyć z SteamCMD. Sprawdź dane logowania lub połączenie internetowe.")

        if progress_callback:
            _require_not_cancelled(cancel_check)
            progress_callback(0, len(mods_to_download))

        for i in range(math.ceil(len(mods_to_download) / lim)):
            _require_not_cancelled(cancel_check)
            batch = mods_to_download[i * lim:min((i + 1) * lim, len(mods_to_download))]

            steamguard = assign_new_steamguard(
                steamguard=steamguard,
                log_callback=safe_log_callback,
                error_callback=safe_error_callback,
                secret_redactor=redactor,
                config_snapshot=snapshot,
                **_cancellation_kwargs(cancel_check),
            )
            redactor.add_secret(steamguard)

            _before_shared_write(cancel_check, before_shared_write)
            return_code = steamcmd_download(
                mod=batch,
                appid=appid,
                login=login,
                password=password,
                steamcmd_dir=steamcmd_dir,
                ghost_folder_path=ghost_folder.ghost_folder_path,
                steamguard=steamguard,
                log_callback=safe_log_callback,
                secret_redactor=redactor,
                **_cancellation_kwargs(cancel_check, before_shared_write),
            )

            if return_code == 84:
                failed_mods.extend(batch)
                _interruptible_wait(600, cancel_check)
                continue
            elif return_code == 0:
                claimed_success = True
            elif return_code != 0:
                steamguard = assign_new_steamguard(
                    steamguard=steamguard,
                    log_callback=safe_log_callback,
                    error_callback=safe_error_callback,
                    secret_redactor=redactor,
                    config_snapshot=snapshot,
                    **_cancellation_kwargs(cancel_check),
                )
                redactor.add_secret(steamguard)
                for mod in batch:
                    if download_fallback(
                        mod,
                        appid,
                        login,
                        password,
                        steamcmd_dir,
                        ghost_folder.ghost_folder_path,
                        steamguard,
                        log_callback=safe_log_callback,
                        error_callback=safe_error_callback,
                        secret_redactor=redactor,
                        config_snapshot=snapshot,
                        **_cancellation_kwargs(cancel_check, before_shared_write),
                    ):
                        failed_mods.append(mod)
                        if safe_error_callback:
                            safe_error_callback(f"Failed to download mod {mod}.")
                        elif safe_log_callback:
                            safe_log_callback(f"Failed to download mod {mod}.")
                    else:
                        claimed_success = True
            if progress_callback:
                _require_not_cancelled(cancel_check)
                progress_callback(min((i + 1) * lim, len(mods_to_download)), len(mods_to_download))

        if not (failed_mods and not claimed_success):
            for wid in mods_to_download:
                _before_shared_write(cancel_check, before_shared_write)
                lowercase_addons_directory(
                    wid,
                    os.path.join(ghost_folder.ghost_folder_path, "steamapps/workshop/content/107410"),
                    log_callback=safe_log_callback,
                )
            _before_shared_write(cancel_check, before_shared_write)
            ghost_folder.move_files(destination_path=mods_dir, internal_path="steamapps/workshop/content/107410")

        if failed_mods:
            if safe_error_callback:
                safe_error_callback(f"Errors occurred during mod downloads: {failed_mods}")
            elif safe_log_callback:
                safe_log_callback(f"Errors occurred during mod downloads: {failed_mods}")
            result = DownloadResult(failed_mods)
            return result
        if safe_log_callback:
            safe_log_callback("All mods downloaded successfully.")
        return None
    except CleanupUncertainError as exc:
        primary_failure = True
        cleanup_uncertain = True
        exc.workspace_path = ghost_folder.ghost_folder_path
        ghost_folder.retain()
        raise
    except BaseException:
        primary_failure = True
        raise
    finally:
        if cleanup_uncertain:
            # The SteamCMD process may still be writing into this workspace.
            # Keep it for trusted task-level quarantine and recovery handling.
            pass
        elif not ghost_folder.cleanup():
            if primary_failure:
                _best_effort_process_log(
                    safe_log_callback,
                    "Steam download filesystem cleanup is pending.",
                )
            else:
                if result is None:
                    result = DownloadResult()
                result.mark_cleanup_pending()
                _best_effort_process_log(
                    safe_log_callback,
                    "Steam download filesystem cleanup is pending.",
                )
                return result

def steamcmd_download(
    mod: str | list[str],
    appid: int,
    login: str,
    password: str,
    steamcmd_dir: str,
    ghost_folder_path: str,
    steamguard: str | None = None,
    log_callback: LogCallback | None = None,
    is_retry: bool = False,
    secret_redactor: SteamSecretRedactor | None = None,
    cancel_check: Callable[[], bool] | None = None,
    before_shared_write: Callable[[], None] | None = None,
) -> int:
    """Download a mod using SteamCMD.

    Args:
        mod (str | list[str]): The workshop ID or ID's of the mod to download.
        appid (int): The app ID of the game (Arma 3).
        login (str): The Steam username.
        password (str): The Steam password.
        steamcmd_dir (str): The directory where SteamCMD is located.
        ghost_folder_path (str): The path to the ghost folder.
        steamguard (str, optional): The Steam Guard code. Defaults to None.
        log_callback (LogCallback, optional): A callback function for logging.
            Defaults to None.
        is_retry (bool, optional): Whether this is a retry of a failed download. Defaults to False.

    Returns:
        int: The return code from the SteamCMD process.
    """
    args = ['bash', f'{os.path.join(steamcmd_dir, "steamcmd.sh")}', f'+force_install_dir {ghost_folder_path}']

    if steamguard:
        args.append(f'+set_steam_guard_code {steamguard}')

    args.append(f'+login {login} {password}')
    if is_retry:
        args.append(f'+workshop_download_item {appid} {int(mod)} validate')
    else:
        if isinstance(mod, list):
            for m in mod:
                args.append(f'+workshop_download_item {appid} {int(m)}')
        else:
            args.append(f'+workshop_download_item {appid} {int(mod)}')
    args.append("+quit")

    redactor = secret_redactor or SteamSecretRedactor.from_config()
    redactor.add_secret(login)
    redactor.add_secret(password)
    redactor.add_secret(steamguard)
    safe_log_callback = redactor.callback(log_callback)
    safe_log_callback = _cancel_aware_callback(safe_log_callback, cancel_check)

    error: SteamCommandError | None = None
    diagnostic = ""
    process_exception: BaseException | None = None
    _before_shared_write(cancel_check, before_shared_write)
    try:
        process = start_process(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        diagnostic = safe_steam_diagnostic(exc, redactor)
        error = SteamCommandError(
            "Nie udało się uruchomić SteamCMD. Sprawdź logi operacji.",
            diagnostic=diagnostic,
        )
    except BaseException as exc:
        # The child may have started before an asynchronous interruption.
        raise CleanupUncertainError(exc) from None
    if error is not None:
        if safe_log_callback and diagnostic:
            safe_log_callback(f"SteamCMD error: {diagnostic}")
        raise error from None
    
    error = None
    cleanup_complete = True
    try:
        stream_process_output(
            process,
            safe_log_callback,
            secret_redactor=redactor,
            **_cancellation_kwargs(cancel_check),
        )
    except ProcessOutputCancelledError as exc:
        process_exception = exc
        cleanup_complete = _cleanup_owned_process(process, close_pipes=False)
        readers_complete = exc.readers_quiesced(timeout=1.0)
        if not cleanup_complete or not readers_complete:
            uncertain = CleanupUncertainError(exc)
            raise uncertain from None
        raise LeaseLostError() from None
    except SteamCommandError as exc:
        process_exception = exc
        diagnostic = safe_steam_diagnostic(exc, redactor)
        cleanup_complete = _cleanup_owned_process(process)
        diagnostic = _with_cleanup_status(diagnostic, cleanup_complete)
        error = SteamCommandError(
            "Nie udało się odczytać wyjścia SteamCMD.",
            diagnostic=diagnostic,
        )
    except Exception as exc:
        process_exception = exc
        diagnostic = safe_steam_diagnostic(exc, redactor)
        cleanup_complete = _cleanup_owned_process(process)
        diagnostic = _with_cleanup_status(diagnostic, cleanup_complete)
        error = SteamCommandError(
            "Nie udało się odczytać wyjścia SteamCMD.",
            diagnostic=diagnostic,
        )
    except BaseException as exc:
        process_exception = exc
        cleanup_complete = _cleanup_owned_process(process)
        if not cleanup_complete:
            uncertain = CleanupUncertainError(exc)
            if cancel_check is None:
                _best_effort_process_log(safe_log_callback, uncertain.diagnostic)
            raise uncertain from None
        raise SteamCommandError(
            "Nie udało się odczytać wyjścia SteamCMD.",
            diagnostic="SteamCMD output supervision was interrupted",
        ) from None
    if error is not None:
        if not cleanup_complete:
            original_exception = process_exception if process_exception is not None else error
            uncertain = CleanupUncertainError(original_exception)
            if cancel_check is None:
                _best_effort_process_log(safe_log_callback, uncertain.diagnostic)
            raise uncertain from None
        if safe_log_callback and diagnostic:
            safe_log_callback(f"SteamCMD error: {diagnostic}")
        raise error from None
    
    return process.returncode

def download_fallback(
    mod: str,
    appid: int,
    login: str,
    password: str,
    steamcmd_dir: str,
    ghost_folder_path: str,
    steamguard: str | None = None,
    log_callback: LogCallback | None = None,
    error_callback: LogCallback | None = None,
    secret_redactor: SteamSecretRedactor | None = None,
    config_snapshot: dict[str, Any] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    before_shared_write: Callable[[], None] | None = None,
) -> str | None:
    """Attempt to download failed mod again.

        Args:
            mod (str): The workshop ID of the mod to download.
            appid (int): The app ID of the game (Arma 3).
            login (str): The Steam username.
            password (str): The Steam password.
            steamcmd_dir (str): The directory where SteamCMD is located.
            ghost_folder_path (str): The path to the ghost folder.
            steamguard (str, optional): The Steam Guard code. Defaults to None.
            log_callback (LogCallback, optional): A callback function for logging.
                Defaults to None.
            config_snapshot (dict, optional): The stable configuration snapshot
                used by the containing download operation.

        Returns:
            str | None: The workshop ID of the mod if the download fails again, otherwise None.
    """
    snapshot = _configuration_snapshot(config_snapshot)
    steam_auth = snapshot.get("steam_auth", {})
    redactor = secret_redactor or SteamSecretRedactor(
        (
            steam_auth.get("username", ""),
            steam_auth.get("password", ""),
            steam_auth.get("shared_secret", ""),
        )
    )
    redactor.add_secret(steam_auth.get("username", ""))
    redactor.add_secret(steam_auth.get("password", ""))
    redactor.add_secret(steam_auth.get("shared_secret", ""))
    redactor.add_secret(login)
    redactor.add_secret(password)
    redactor.add_secret(steamguard)
    safe_log_callback = redactor.callback(log_callback)
    safe_log_callback = _cancel_aware_callback(safe_log_callback, cancel_check)
    safe_error_callback = redactor.callback(error_callback)
    safe_error_callback = _cancel_aware_callback(safe_error_callback, cancel_check)

    stop_time = time() + 7200  # 120 minutes timeout
    while time() < stop_time:
        _require_not_cancelled(cancel_check)
        if safe_log_callback:
            safe_log_callback(f"Retrying download for mod {mod}...")
        _interruptible_wait(60, cancel_check)
        steamguard = assign_new_steamguard(
            steamguard=steamguard,
            log_callback=safe_log_callback,
            error_callback=safe_error_callback,
            secret_redactor=redactor,
            config_snapshot=snapshot,
            **_cancellation_kwargs(cancel_check),
        )
        redactor.add_secret(steamguard)
        _before_shared_write(cancel_check, before_shared_write)
        return_code = steamcmd_download(
            mod=mod,
            appid=appid,
            login=login,
            password=password,
            steamcmd_dir=steamcmd_dir,
            ghost_folder_path=ghost_folder_path,
            steamguard=steamguard,
            log_callback=safe_log_callback,
            is_retry=True,
            secret_redactor=redactor,
            **_cancellation_kwargs(cancel_check, before_shared_write),
        )
        if return_code == 0:
            if safe_log_callback:
                safe_log_callback(f"Successfully downloaded mod {mod} after retry.")
            return None
    return mod

def test_connection(
    steamcmd_dir: str,
    login: str,
    password: str,
    steamguard: str = None,
    log_callback: LogCallback | None = None,
    secret_redactor: SteamSecretRedactor | None = None,
    cancel_check: Callable[[], bool] | None = None,
    before_shared_write: Callable[[], None] | None = None,
) -> bool:
    """Test connection to SteamCMD.

    Args:
        steamcmd_dir (str): The directory where SteamCMD is located.
        login (str): The Steam username.
        password (str): The Steam password.
        steamguard (str, optional): The Steam Guard code. Defaults to None.

    Returns:
        bool: True if the connection is successful, False otherwise.
    """
    redactor = secret_redactor or SteamSecretRedactor.from_config()
    redactor.add_secret(login)
    redactor.add_secret(password)
    redactor.add_secret(steamguard)
    safe_log_callback = redactor.callback(log_callback)
    safe_log_callback = _cancel_aware_callback(safe_log_callback, cancel_check)

    args = ['bash', f'{os.path.join(steamcmd_dir, "steamcmd.sh")}']
    if steamguard:
        args.append(f'+set_steam_guard_code {steamguard}')
    args.append(f'+login {login} {password}')
    args.append("+quit")

    process = None
    _before_shared_write(cancel_check, before_shared_write)
    try:
        process = start_process(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        diagnostic = safe_steam_diagnostic(exc, redactor)
        error = SteamCommandError(
            "Nie udało się połączyć z SteamCMD. Sprawdź logi operacji.",
            diagnostic=diagnostic,
        )
        if safe_log_callback and diagnostic:
            safe_log_callback(f"SteamCMD connection error: {diagnostic}")
        raise error from None
    except BaseException as exc:
        raise CleanupUncertainError(exc) from None

    try:
        if cancel_check is None:
            process.communicate(timeout=STEAM_CONNECTION_TIMEOUT_SECONDS)
        else:
            _communicate_with_cancellation(process, cancel_check)
    except Exception as exc:
        cleanup_complete = _cleanup_communicate_process(process)
        if isinstance(exc, LeaseLostError):
            if not cleanup_complete:
                uncertain = CleanupUncertainError(exc)
                if cancel_check is None:
                    _best_effort_process_log(safe_log_callback, uncertain.diagnostic)
                raise uncertain from None
            raise LeaseLostError() from None
        diagnostic = _with_cleanup_status(safe_steam_diagnostic(exc, redactor), cleanup_complete)
        if not cleanup_complete:
            uncertain = CleanupUncertainError(exc)
            if cancel_check is None:
                _best_effort_process_log(safe_log_callback, uncertain.diagnostic)
            raise uncertain from None
        if safe_log_callback and diagnostic:
            safe_log_callback(f"SteamCMD connection error: {diagnostic}")
        raise SteamCommandError(
            "Nie udało się połączyć z SteamCMD. Sprawdź logi operacji.",
            diagnostic=diagnostic,
        ) from None
    except BaseException as exc:
        cleanup_complete = _cleanup_communicate_process(process)
        if not cleanup_complete:
            uncertain = CleanupUncertainError(exc)
            if cancel_check is None:
                _best_effort_process_log(safe_log_callback, uncertain.diagnostic)
            raise uncertain from None
        raise SteamCommandError(
            "Nie udało się połączyć z SteamCMD. Sprawdź logi operacji.",
            diagnostic="SteamCMD connection supervision was interrupted",
        ) from None

    return process.returncode == 0


def _communicate_with_cancellation(process, cancel_check: Callable[[], bool]) -> None:
    remaining = STEAM_CONNECTION_TIMEOUT_SECONDS
    while remaining > 0:
        _require_not_cancelled(cancel_check)
        interval = min(0.25, remaining)
        try:
            process.communicate(timeout=interval)
            return
        except subprocess.TimeoutExpired:
            remaining -= interval
    raise subprocess.TimeoutExpired("SteamCMD", timeout=STEAM_CONNECTION_TIMEOUT_SECONDS)


def _configuration_snapshot(config_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Copy one coherent configuration view for a complete download run."""
    snapshot = config_snapshot if config_snapshot is not None else config.snapshot()
    return copy.deepcopy(snapshot)


def _cleanup_owned_process(process, *, close_pipes: bool = True) -> bool:
    try:
        if close_pipes:
            return terminate_owned_process_tree(process)
        return terminate_owned_process_tree(process, close_pipes=False)
    except Exception:
        return False


def _cleanup_communicate_process(process, *, reader_timeout: float = 1.0) -> bool:
    """Reap the owned process, then boundedly account for communicate readers."""
    try:
        process_complete = _cleanup_owned_process(process, close_pipes=False)
    except BaseException:
        process_complete = False

    try:
        readers = _communicate_reader_threads(process)
        if readers is None:
            return False

        deadline = monotonic() + max(0.0, reader_timeout)
        for reader in readers:
            # Popen can publish a Thread before start() succeeds. Its absence of
            # an identity is not proof that no reader can still be started.
            if reader.ident is None:
                return False
            reader.join(max(0.0, deadline - monotonic()))
        readers_complete = not any(reader.is_alive() for reader in readers)
    except BaseException:
        # Reader discovery and quiescence are independent cleanup proofs. A
        # failed probe must preserve uncertainty even if process cleanup also
        # failed, without replacing the caller's primary exception.
        return False

    if not process_complete or not readers_complete:
        return False

    # Popen's Windows reader threads close their own streams. On selector-based
    # platforms there are no background readers, so close only after process
    # cleanup and the bounded reader check have established no concurrent read.
    for stream_name in ("stdout", "stderr"):
        stream = _process_attribute(process, stream_name)
        if stream is None:
            continue
        try:
            stream.close()
        except BaseException:
            return False
    return True


def _communicate_reader_threads(process) -> list[threading.Thread] | None:
    """Return Popen's bounded reader set, or fail closed on unknown Windows state."""
    try:
        process_state = vars(process)
    except TypeError:
        return None

    communication_started = process_state.get("_communication_started", False)
    readers = []
    for stream_name, reader_name in (
        ("stdout", "stdout_thread"),
        ("stderr", "stderr_thread"),
    ):
        if process_state.get(stream_name) is None:
            continue
        reader = process_state.get(reader_name)
        if isinstance(reader, threading.Thread):
            readers.append(reader)
        elif os.name == "nt" and communication_started is not False:
            return None
    return readers


def _process_attribute(process, name: str):
    try:
        return vars(process).get(name)
    except TypeError:
        return None


def _best_effort_process_log(callback: LogCallback | None, message: str) -> None:
    """Logging must not replace trusted process-cleanup uncertainty."""
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        pass


def _with_cleanup_status(diagnostic: str, cleanup_complete: bool) -> str:
    if cleanup_complete:
        return diagnostic
    return f"{diagnostic}; process_cleanup=unconfirmed"
