import os
from ..utils.config import config
from ..utils.operation_logging import LogCallback


def generate_server_config(username: str, password: str, arma_path: str, log_callback: LogCallback | None = None) -> str:
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
        maxPlayers = 32;
        motd[] = {{"Witaj na serwerze FOG użytkownika {username}!"}};
        BattlEye = 0;
        verifySignatures = 0;
        logFile = "server_{username}_console.log";
        allowedFilePatching = 0;
        steamProtocolMaxDataSize = 16384;
        forcedDifficulty = "Custom";
        disableVoN = 1;
        
        class DifficultyPresets
        {{
            class CustomDifficulty
            {{
                    class Options
                    {{
                            /* Simulation */

                            reducedDamage = 0;              // Reduced damage

                            /* Situational awareness */

                            groupIndicators = 0;    // Group indicators (0 = never, 1 = limited distance, 2 = always)
                            friendlyTags = 0;               // Friendly name tags (0 = never, 1 = limited distance, 2 = always)
                            enemyTags = 0;                  // Enemy name tags (0 = never, 1 = limited distance, 2 = always)
                            detectedMines = 0;              // Detected mines (0 = never, 1 = limited distance, 2 = always)
                            commands = 0;                   // Commands (0 = never, 1 = fade out, 2 = always)
                            waypoints = 0;                  // Waypoints (0 = never, 1 = fade out, 2 = always)
                            tacticalPing = 0;               // Tactical ping (0 = disabled, 1 = in 3D scene, 2 = on map, 3 = both)

                            /* Personal awareness */

                            weaponInfo = 2;                 // Weapon info (0 = never, 1 = fade out, 2 = always)
                            stanceIndicator = 2;    // Stance indicator (0 = never, 1 = fade out, 2 = always)
                            staminaBar = 0;                 // Stamina bar
                            weaponCrosshair = 0;    // Weapon crosshair
                            visionAid = 0;                  // Vision aid

                            /* View */

                            thirdPersonView = 0;    // 3rd person view (0 = disabled, 1 = enabled, 2 = enabled for vehicles only (Since  Arma 3 v1.99))
                            cameraShake = 1;                // Camera shake

                            /* Multiplayer */

                            scoreTable = 0;                 // Score table
                            deathMessages = 0;              // Killed by
                            vonID = 0;                              // VoN ID

                            /* Misc */

                            mapContent = 0;                 // Extended map content
                            autoReport = 0;                 // (former autoSpot) Automatic reporting of spotted enemies by players only. This doesn't have any effect on AIs.
                            multipleSaves = 0;              // Multiple saves
                    }};

                    // aiLevelPreset defines AI skill level and is counted from 0 and can have following values: 0 (Low), 1 (Normal), 2 (High), 3 (Custom).
                    // when 3 (Custom) is chosen, values of skill and precision are taken from the class CustomAILevel.
                    aiLevelPreset = 2;
            }};
        }};
    """
    config_path = f"{arma_path}/server_{username}.cfg"
    with open(config_path, 'w') as config_file:
        config_file.write(config_content)
    if log_callback:
        log_callback(f"Generated server config for {username} at {config_path}")
    return config_path

def generate_sh_file(name: str, port: int, username: str, mod_paths: list, mods_directory: str, arma_directory: str, log_callback: LogCallback | None = None, is_admin_instance: bool = False) -> str:
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
            mod_paths = [os.path.join(os.path.basename(mods_directory), os.path.basename(path)) for path in
                        mod_paths]
            mod_string = "-mod='" + "'\\;'".join(mod_paths) + "'"
            script_file.write(f" {mod_string}\n")
        else:
            script_file.write("\n")

    os.chmod(script_name, 0o755)  # Make the script executable
    if log_callback:
        log_callback(f"Generated script: {script_name} for {name} on port {port}")
    return script_name

def check_sh_file_exists(name: str, log_callback: LogCallback | None = None) -> bool:
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
