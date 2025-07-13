# Function to convert all files and directories within 'addons' or 'Addons' to lowercase
import os
from dotenv import load_dotenv
import datetime

load_dotenv('util/cred.env')

def log(message, doprint=True):
    current_date_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_messages.append(f"{current_date_time} - {message}\n")
    if doprint:
        print(f"{message}\n")

def write_log_to_file():
    log_file = f"logs/log_rename_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    with open(log_file, 'w') as f:
        for message in log_messages:
            f.write(message + "\n")
    log_messages.clear()  # clear the list after writing to file


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
        if __name__ == "__main__":
            log(f"No 'addons' or 'Addons' directory found for mod {wid}. Skipping.")
        else:
            return f"No 'addons' or 'Addons' directory found for mod {wid}. Skipping."

if __name__ == "__main__":
    log_messages = []
    workshop_id = input("Enter the workshop ID: ")
    lowercase_addons_directory(str(workshop_id))