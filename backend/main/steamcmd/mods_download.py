import os
import math
import subprocess
from process_output import stream_process_output
from ..utils.logger import Logger


def download_mods(workshop_ids, workshop_dir, lim=1):
    errors = {}
    mod_paths = []
    appid = 107410  # Arma 3 appid

    login = os.getenv('USERNAME')
    password = os.getenv('PASSWORD')
    steamguard = os.getenv('STEAMGUARD')
    log('Loaded login credentials.')

    mods_to_download = []
    download_logger = Logger(name='mods_download')
    for wid in workshop_ids:
        mod_dir = modpath(workshop_dir, wid)
        mod_paths.append(mod_dir)
        if os.path.exists(mod_dir):
            download_logger.log(f"Mod {wid} already installed in {workshop_dir}. Skipping download.")
        else:
            mods_to_download.append(wid)

    for i in range(math.ceil(len(mods_to_download) / lim)):
        batch = mods_to_download[i * lim:min((i + 1) * lim, len(mods_to_download))]

        args = ['/home/fogserver/Steam/steamcmd.sh', f'+force_install_dir {workshop_dir}']

        if steamguard:
            args.append(f'+set_steam_guard_code {steamguard}')

        args.append(f'+login {login} {password}')

        for wid in batch:
            args.append(f'+workshop_download_item {appid} {int(wid)}')
        args.append("+quit")

        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        process_logger = Logger(name='process_output')

        stream_process_output(process, process_logger.log)

        if process.returncode != 0:
            download_logger.log(f"SteamCMD process for batch {batch} failed with return code {process.returncode}.")
            for wid in batch:
                errors[wid] = 1

        for wid in batch:
            folder_workaround(workshop_dir, appid, wid)
        delete_folder_workaround(workshop_dir)

    if errors:
        log(f"Some mods failed to download: {errors}")
    else:
        log("Presumably, all mods downloaded successfully.")

    return mod_paths