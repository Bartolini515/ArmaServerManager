import os
import subprocess
from time import sleep, time
from .process_output import stream_process_output
from ..utils.logger import Logger
from ..utils.config import config
from ..workaround.cache_deletion import delete_steamcmd_appcache
from ..workaround.ghost_folder import GhostFolder
from ..workaround.modsrenamer import lowercase_addons_directory

from .steam_auth import load_credentials, generate_steam_guard_code


def download_mods(mods_to_download: list[str], name: str, logger: Logger) -> list[str] | None:
    """Downloads mods using SteamCMD.

    Args:
        mods_to_download (list[str]): The list of workshop IDs for the mods to download.
        name (str): The name of the mod set being downloaded.
        logger (Logger): Logger instance for logging download progress.
    
    Returns:
        list[str]: A list containing mods that failed to download, if any.

    """
    failed_mods = []
    appid = 107410  # Arma 3 appid
    mods_dir = config.get("paths.mods_directory", "")
    download_dir = config.get("paths.download_directory", "")
    steamcmd_dir = config.get("paths.steamcmd", "")
    

    login, password = load_credentials()
    steamguard = generate_steam_guard_code(config.get("steam_auth.shared_secret", ""))
    if logger:
        logger.log('Loaded login credentials.')
    
    delete_steamcmd_appcache(steamcmd_dir, logger.log if logger else None)

    ghost_folder = GhostFolder(name=name, path=download_dir, log_callback=logger.log if logger else None)

    for mod in mods_to_download:
        return_code = steamcmd_download(
            mod=mod,
            appid=appid,
            login=login,
            password=password,
            steamcmd_dir=steamcmd_dir,
            ghost_folder_path=ghost_folder.ghost_folder_path,
            steamguard=steamguard,
            log_callback=logger.log if logger else None
        )

        if return_code != 0:
            if download_fallback():
                failed_mods.append(mod)
                if logger:
                    logger.log(f"Failed to download mod {mod}.")

    for wid in mods_to_download:
        lowercase_addons_directory(wid, os.path.join(ghost_folder.ghost_folder_path, "steamapps/workshop/content/107410"), log_callback=logger.log if logger else None)
    ghost_folder.move_files(destination_path=mods_dir, internal_path="steamapps/workshop/content/107410")
    ghost_folder.cleanup()
    
    if failed_mods:
        if logger:
            logger.log(f"Errors occurred during mod downloads: {failed_mods}")
        return failed_mods
    if logger:
        logger.log("All mods downloaded successfully.")
    return None

def steamcmd_download(mod: str, appid: int, login: str, password: str, steamcmd_dir: str, ghost_folder_path: str, steamguard: str = None, log_callback: callable = None, is_retry: bool = False) -> int:
    """Download a mod using SteamCMD.

    Args:
        mod (str): The workshop ID of the mod to download.
        appid (int): The app ID of the game (Arma 3).
        login (str): The Steam username.
        password (str): The Steam password.
        steamcmd_dir (str): The directory where SteamCMD is located.
        ghost_folder_path (str): The path to the ghost folder.
        steamguard (str, optional): The Steam Guard code. Defaults to None.
        log_callback (callable, optional): A callback function for logging. Defaults to None.
        is_retry (bool, optional): Whether this is a retry of a failed download. Defaults to False.

    Returns:
        int: The return code from the SteamCMD process.
    """
    args = [f'{os.path.join(steamcmd_dir, "steamcmd.exe")}', f'+force_install_dir {ghost_folder_path}'] # TODO: Change exe to sh

    if steamguard:
        args.append(f'+set_steam_guard_code {steamguard}')

    args.append(f'+login {login} {password}')
    if is_retry:
        args.append(f'+workshop_download_item {appid} {int(mod)} validate')
    else:
        args.append(f'+workshop_download_item {appid} {int(mod)}')
    args.append("+quit")

    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    stream_process_output(process, log_callback if log_callback else None)
    
    return process.returncode

def download_fallback(mod: str, appid: int, login: str, password: str, steamcmd_dir: str, ghost_folder_path: str, steamguard: str = None, log_callback: callable = None) -> str | None:
    """Attempt to download failed mod again.

        Args:
            mod (str): The workshop ID of the mod to download.
            appid (int): The app ID of the game (Arma 3).
            login (str): The Steam username.
            password (str): The Steam password.
            steamcmd_dir (str): The directory where SteamCMD is located.
            ghost_folder_path (str): The path to the ghost folder.
            steamguard (str, optional): The Steam Guard code. Defaults to None.
            log_callback (callable, optional): A callback function for logging. Defaults to None.

        Returns:
            str | None: The workshop ID of the mod if the download fails again, otherwise None.
    """
    stop_time = time() + 3600  # 60 minutes timeout
    while time() < stop_time:
        if log_callback:
            log_callback(f"Retrying download for mod {mod}...")
        sleep(60)
        return_code = steamcmd_download(mod=mod, appid=appid, login=login, password=password, steamcmd_dir=steamcmd_dir, ghost_folder_path=ghost_folder_path, steamguard=steamguard, log_callback=log_callback, is_retry=True)
        if return_code == 0:
            if log_callback:
                log_callback(f"Successfully downloaded mod {mod} after retry.")
            return None
    return mod