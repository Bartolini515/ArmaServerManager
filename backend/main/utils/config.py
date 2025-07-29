import json
import os

class Config:
    def __init__(self, file_path: str):
        """Initializes the Config object and loads the configuration from a JSON file.

        Args:
            file_path (str): The path to the configuration file.
        """
        if not os.path.isabs(file_path):
            # Construct an absolute path relative to this script's location
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.file_path = os.path.join(script_dir, file_path)
        else:
            # Use the absolute path directly
            self.file_path = file_path
            
        self.config = {}
        if not os.path.exists(self.file_path):
            self._create_blank_config()
        else:
            self._load()

    def _create_blank_config(self):
        """Creates a blank configuration file"""
        default_config = {
            "paths": {
                "steamcmd": "",
                "arma3": "",
                "mods_directory": "",
                "logs_directory": "",
                "download_directory": ""
            },
            "steam_auth": {
                "username": "",
                "password": "",
                "shared_secret": ""
            }
        }

        with open(self.file_path, 'w') as file:
            json.dump(default_config, file, indent=2, ensure_ascii=False)
        self.config = default_config

    def _load(self):
        with open(self.file_path, 'r') as file:
            self.config = json.load(file)
            
    def _deep_update(self, source: dict, overrides: dict):
        """Recursively updates a dictionary with values from another dictionary.

        Args:
            source (dict): The original dictionary to update.
            overrides (dict): A dictionary with new values to merge into the original.
        """
        for key, value in overrides.items():
            if isinstance(value, dict) and key in source and isinstance(source.get(key), dict):
                self._deep_update(source[key], value)
            else:
                source[key] = value
    
    def get(self, key_path: str, default=None):
        """Retrieves a value from the config using a dot-separated path.

        Args:
            key_path (str): The dot-separated path to the desired value.
            default (_type_, optional): The value to return if the key is not found. Defaults to None.

        Returns:
            _type_: _description_
        """
        
        keys = key_path.split('.')
        value = self.config
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def update(self, new_config: dict):
        """Updates the config with a new dictionary, merging it deeply.

        Args:
            new_config (dict): A dictionary with new values to merge into the original.
        """
        self._deep_update(self.config, new_config)
        with open(self.file_path, 'w') as file:
            json.dump(self.config, file, indent=2, ensure_ascii=False)
        self._load()
            
# Create a single instance of Config to be used throughout the application
config = Config('../../data/config.json')