import os
import shutil


def delete_steamcmd_appcache(source_dir: str, log_callback: callable = None) -> None:
    """Delete the SteamCMD appcache directory.

    Args:
        source_dir (str): The source directory containing the appcache.
        log_callback (callable, optional): A callback function for logging. Defaults to None.
    """
    appcache_path = os.path.join(source_dir, 'appcache')
    if os.path.isdir(appcache_path):
        shutil.rmtree(appcache_path)
        if log_callback:
            log_callback('Deleted steamcmd appcache.')
    else:
        if log_callback:
            log_callback('No appcache directory found to delete.')