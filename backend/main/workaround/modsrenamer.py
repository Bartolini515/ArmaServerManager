import os

# TODO: Refractor and integrate
def lowercase_addons_directory(wid, workshop_dir=None):
    if not workshop_dir:
        workshop_dir = os.path.join(os.getenv('ARMA'), os.getenv('WORKSHOP'))
    addons_path_lower = os.path.join(workshop_dir, str(wid), 'addons')
    addons_path_upper = os.path.join(workshop_dir, str(wid), 'Addons')

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
    else:
            return f"No 'addons' or 'Addons' directory found for mod {wid}. Skipping."