import os
from ..utils.config import config


def generate_server_config(username: str, password: str, arma_path: str, log_callback: callable = None) -> str:
    """Generates a server configuration file for the given username and password.
    Args:
        username (str): The username of the user.
        password (str): The password of the user.
        arma_path (str): The path to the Arma 3 server directory.
        log_callback (callable, optional): The logging callback function. Defaults to None.

    Returns:
        str: The path to the generated server configuration file.
    """
    config_content = f"""
        passwordAdmin = "{password}";
        password = "{password}";
        hostname = "FOG - {username} Server";
        maxPlayers = 128;
        motd[] = {{"Witaj na serwerze FOG użytkownika {username}!"}};
        verifySignatures = 0;
        logFile = "server_{username}_console.log";
        BattlEye = 0;
        steamProtocolMaxDataSize = 12048;
        forcedDifficulty = "Custom";
        disableVoN = 1;
    """
    config_path = f"{arma_path}/server_{username}.cfg"
    with open(config_path, 'w') as config_file:
        config_file.write(config_content)
    if log_callback:
        log_callback(f"Generated server config for {username} at {config_path}")
    return config_path

def generate_sh_file(name: str, port: int, username: str, mod_paths: list, mods_directory: str, arma_directory: str, log_callback: callable = None, is_admin_instance: bool = False) -> str:
    """Generates a shell script to start the Arma 3 server.
    Args:
        name (str): The name of the server.
        port (int): The port number for the server.
        username (str): The username of the user.
        mod_paths (list): A list of paths to the mod files.
        mods_directory (str): The directory where the mod files are located.
        arma_directory (str): The directory where the Arma 3 server executable is located.
        log_callback (callable, optional): The logging callback function. Defaults to None.
        is_admin_instance (bool, optional): Whether the instance is an admin instance. Defaults to False.

    Returns:
        str: The path to the generated shell script.
    """
    script_name = f"{arma_directory}/start{name}.sh"

    with open(script_name, 'w') as script_file:
        script_file.write("#!/bin/bash\n")
        script_file.write(f'./arma3server_x64 -port={port} -config="server_{username if not is_admin_instance else name}.cfg"')

        if mod_paths:
            mod_paths = [os.path.join(mods_directory, os.path.basename(path)) for path in
                        mod_paths]
            mod_string = "-mod='" + "'\\;'".join(mod_paths) + "'"
            script_file.write(f" {mod_string}\n")
        else:
            script_file.write("\n")

    os.chmod(script_name, 0o755)  # Make the script executable
    if log_callback:
        log_callback(f"Generated script: {script_name} for {name} on port {port}")
    return script_name

def check_sh_file_exists(name: str, log_callback: callable = None) -> bool:
    """Checks if the shell script for the given instance name exists.
    Args:
        name (str): The name of the instance.
        log_callback (callable, optional): The logging callback function. Defaults to None.

    Returns:
        bool: True if the script exists, False otherwise.
    """
    arma_path = config.get("paths.arma3", "")
    script_name = f"{arma_path}/start{name}.sh"
    exists = os.path.exists(script_name)
    if log_callback:
        log_callback(f"Checked existence of script: {script_name} - Exists: {exists}")
    return exists