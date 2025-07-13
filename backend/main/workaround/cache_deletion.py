import os
import shutil


# TODO: Refractor and integrate
def delete_steamcmd_appcache(source_dir):
    appcache_path = os.path.join(source_dir, 'appcache')
    if os.path.isdir(appcache_path):
        shutil.rmtree(appcache_path)
        log('Deleted steamcmd appcache.', False)