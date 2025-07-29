import os

def lowercase_addons_directory(wid: str, source_dir: str, log_callback: callable = None) -> None:
    """Lowercases the names of all files and directories in the addons directory of a mod.

    Args:
        wid (str): The workshop ID of the mod.
        source_dir (str): The base directory where the mod is located.
        log_callback (callable, optional): A callback function for logging messages. Defaults to None.
    """
    addons_path_lower = os.path.join(source_dir, str(wid), 'addons')
    addons_path_upper = os.path.join(source_dir, str(wid), 'Addons')

    if os.path.exists(addons_path_upper):
        os.rename(addons_path_upper, addons_path_lower)

    if os.path.exists(addons_path_lower):
        for root, dirs, files in os.walk(addons_path_lower, topdown=False):
            for name in files:
                src = os.path.join(root, name)
                dst = os.path.join(root, name.lower())
                if src != dst:
                    os.rename(src, dst)

            for name in dirs:
                src = os.path.join(root, name)
                dst = os.path.join(root, name.lower())
                if src != dst:
                    os.rename(src, dst)

        for root, _, files in os.walk(addons_path_lower):
            for name in files:
                if name.lower().endswith('.pbo') or name.lower().endswith('.bisign'):
                    src = os.path.join(root, name)
                    dst = os.path.join(root, name.lower())
                    if src != dst:
                        os.rename(src, dst)
        if log_callback:
            log_callback(f"Lowercased addon directory for mod {wid}")
    else:
        if log_callback:
            log_callback(f"Addons directory not found for mod {wid}")