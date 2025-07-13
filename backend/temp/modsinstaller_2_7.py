import subprocess
import re
import os
import math
import shutil
from dotenv import load_dotenv
import datetime
from modsrenamer import lowercase_addons_directory

# Credit to Blackry for applying workaround fix for steamcmd

log_messages = []
load_dotenv('util/cred.env')


def log(message, doprint=True):
    current_date_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_messages.append(f"{current_date_time} - {message}\n")
    if doprint:
        print(f"{message}\n")


def write_log_to_file():
    log_file = f"logs/log_installer_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
    with open(log_file, 'w') as f:
        for message in log_messages:
            f.write(message + "\n")
    log_messages.clear()  # clear the list after writing to file


# Function to extract links from the HTML file
def extract_links(file_name):
    with open(file_name, 'r') as file:
        links = re.findall(r'<a href="(https?://[^"]+)"', file.read())
    log(f'Extracted links from html file: {links}', False)
    return links


# Function to delete all contents of directories in workshop
def delete_temp(downloads_dir, temp_dir):
    for directory in [downloads_dir, temp_dir]:
        for file in os.listdir(directory):
            file_path = os.path.join(directory, file)
            if os.path.isfile(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
    log('Deleted temp files.', False)


# Function to create a modpath
def modpath(workshop_dir, wid):
    log(f'Created a modpath for {wid}', False)
    return os.path.join(workshop_dir, str(wid))


# Function to extract Workshop IDs from the links
def extract_workshop_ids(links):
    workshop_ids = []
    for link in links:
        match = re.search(r'\?id=(\d+)', link)
        if match:
            workshop_ids.append(match.group(1))
    log(f'Extracted workshop ids from links: {workshop_ids}', False)
    return workshop_ids


# Workaround
def folder_workaround(workshop_dir, appid, wid):
    nested_path = os.path.join(workshop_dir, "steamapps", "workshop", "content", str(appid))
    nested_wid_path = os.path.join(nested_path, str(wid))
    target_path = os.path.join(workshop_dir, str(wid))

    message = lowercase_addons_directory(wid, nested_path)
    if message:
        log(message)

    if os.path.exists(nested_wid_path):
        log(f"Applying workaround fix for mod {wid}")
        shutil.move(nested_wid_path, target_path)
    else:
        log(f"Couldn't find the workaround folder for mod {wid}")

# Workaround deletion
def delete_folder_workaround(workshop_dir):
    steamapps_path = os.path.join(workshop_dir, "steamapps")
    if os.path.exists(steamapps_path):
        try:
            shutil.rmtree(steamapps_path)
            log(f"Deleted the workaround folder {steamapps_path}")
        except Exception as e:
            log(f"An Error occurred during deletion of workaround folders {e}")


# Function to download mods using steamcmd
def download_mods(workshop_ids, workshop_dir, lim=3):
    errors = {}
    mod_paths = []
    appid = 107410  # Arma 3 appid

    login = os.getenv('USERNAME')
    password = os.getenv('PASSWORD')
    steamguard = os.getenv('STEAMGUARD')
    log('Loaded login credentials.')

    mods_to_download = []
    for wid in workshop_ids:
        mod_dir = modpath(workshop_dir, wid)
        mod_paths.append(mod_dir)
        if os.path.exists(mod_dir):
            log(f"Mod {wid} already installed in {workshop_dir}. Skipping download.")
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

        while True:
            out = process.stdout.readline()
            if out == b'' and process.poll() is not None:
                break
            if out:
                log(out.decode('utf-8').strip())
                if out == 'FAILED (Invalid Login Auth Code)':
                    log('Steamguard authentication failed.')
                    exit(0)

        return_code = process.poll()
        if return_code != 0:
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


# Function to create the .sh file
def generate_sh_file(mod_paths, preset_name, server_type, armapath):
    port = server_type['port']
    name = server_type['name']
    script_name = f"{armapath}/start{name}{preset_name}.sh"

    with open(script_name, 'w') as script_file:
        script_file.write("#!/bin/bash\n")
        script_file.write(f'./arma3server_x64 -port={port} -config="server{name}.cfg"')

        if mod_paths:
            mod_paths = [os.path.join(os.getenv('WORKSHOP'), os.path.basename(path)) for path in
                         mod_paths]
            mod_string = "-mod='" + "'\\;'".join(mod_paths) + "'"
            script_file.write(f" {mod_string}\n")
        else:
            script_file.write("\n")

    os.chmod(script_name, 0o755)  # Make the script executable
    log(f"Generated script: {script_name} for {name} on port {port}")


def select_server_type(types_of_server):
    log("Select the server type:")
    for index, server_type in enumerate(types_of_server, start=1):
        log(f"{index}: {server_type['name']}")

    while True:
        try:
            choice = int(input("Enter the number of the server type you want to select: "))
            log(f'User chose: {choice}', False)
            if 1 <= choice <= len(types_of_server):
                return types_of_server[choice - 1]
            else:
                log("Invalid number, please try again.")
        except ValueError:
            log("Can't read a number, try again.")


# Function to create a primitive UI for selecting the HTML file
def select_html_file(presets_path):
    if not os.path.exists(presets_path):
        log(f"Presets directory not found: {presets_path}")
        return None

    html_files = [f for f in os.listdir(presets_path) if f.endswith('.html')]

    if not html_files:
        log("No HTML files found in the presets directory.")
        return None

    log("Select an HTML file:")
    for idx, file in enumerate(html_files, start=1):
        log(f"{idx}: {file}")

    while True:
        try:
            choice = int(input("Enter the number of the file you want to select: "))
            log(f'User chose: {choice}', False)
            if 1 <= choice <= len(html_files):
                return os.path.join(presets_path, html_files[choice - 1])
            else:
                log("Invalid number, please try again.")
        except ValueError:
            log("Can't read a number, try again.")


def main():
    types_of_server = [
        {"name": "main", "port": '2302'},
        {"name": "test", "port": '2322'},
        {"name": "training", "port": '2333'},
        {"name": "liberation", "port": '2344'}]

    armapath = os.getenv('ARMA')
    managerpath = os.getenv('MANAGER')
    workshop_dir = os.path.join(armapath, os.getenv('WORKSHOP'))
    downloads_dir = os.path.join(armapath, os.getenv('DOWNLOADS'))
    temp_dir = os.path.join(armapath, os.getenv('TEMP'))
    presets_path = os.path.join(managerpath, 'presets')

    delete_temp(downloads_dir, temp_dir)

    html_file = select_html_file(presets_path)
    if not html_file:
        return

    server_type = select_server_type(types_of_server)

    preset_name = os.path.splitext(os.path.basename(html_file))[0]

    links = extract_links(html_file)

    workshop_ids = extract_workshop_ids(links)

    mod_paths = download_mods(workshop_ids, workshop_dir)

    generate_sh_file(mod_paths, preset_name, server_type, armapath)

    log('Running download_mods second time to verify all files.')
    delete_temp(downloads_dir, temp_dir)
    download_mods(workshop_ids, workshop_dir)

    write_log_to_file()


if __name__ == '__main__':
    main()
