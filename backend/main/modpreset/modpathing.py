import os



def mod_path(mods_dir: str, wid: str, log_callback: callable = None) -> str:
    """Generate the file path for a mod.

    Args:
        mods_dir (str): The directory where mods are stored.
        wid (str): The workshop ID of the mod.
        log_callback (Logger.log): The logging callback function.

    Returns:
        str: The generated file path for the mod.
    """
    path = os.path.join(mods_dir, str(wid))
    if log_callback:
        log_callback(f"Generated mod path for {wid}: {path}")
    return path

def check_installed(wids: list, mods_dir: str, log_callback: callable = None) -> tuple:
    """Check if mods are installed.

    Args:
        wids (list): List of workshop IDs to check.
        mods_dir (str): Directory where mods are installed.
        log_callback (callable, optional): A callback function for logging. Defaults to None.

    Returns:
        tuple: A tuple containing two lists - the first with paths to installed mods,
            and the second with workshop IDs of mods to download.
    """
    mod_paths = []
    mods_to_download = []
    for wid in wids:
        mod_dir = mod_path(mods_dir, wid, log_callback)
        mod_paths.append(mod_dir)
        if os.path.exists(mod_dir):
            if log_callback:
                log_callback(f"Mod {wid} already installed in {mods_dir}. Skipping download.")
        else:
            mods_to_download.append(wid)
            if log_callback:
                log_callback(f"Mod {wid} not found in {mods_dir}. Marking for download.")
    return mod_paths, mods_to_download