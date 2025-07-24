import os
import shutil


class GhostFolder:
    def __init__(self, name: str, path: str, log_callback: callable = None) -> None:
        """Represents a ghost folder for temporarily storing downloaded files.

        Args:
            name (str): The name of the ghost folder.
            path (str): The base path where the ghost folder will be created.
            log_callback (callable, optional): A callback function for logging messages. Defaults to None.
        """
        self.name = name
        self.base_path = path
        self.ghost_folder_path = os.path.join(self.base_path, f"{self.name}_download_folder")
        self.log_callback = log_callback
        self._create_folder()

    def _create_folder(self) -> None:
        """Creates the ghost folder for temporarily storing downloaded files.
        """
        os.makedirs(self.ghost_folder_path, exist_ok=True)
        if self.log_callback:
            self.log_callback(f"Created ghost folder: {self.ghost_folder_path}")

    def move_files(self, destination_path: str, internal_path: str = None) -> None:
        """Moves files from the ghost folder to a specified destination.

        Args:
            destination_path (str): The path to the destination folder.
            internal_path (str, optional): The internal path within the ghost folder. Defaults to None.
        """
        if internal_path is None:
            internal_path = self.ghost_folder_path
        else:
            internal_path = os.path.join(self.ghost_folder_path, internal_path)
        os.makedirs(destination_path, exist_ok=True)
        for item_name in os.listdir(internal_path):
            source_item = os.path.join(internal_path, item_name)
            destination_item = os.path.join(destination_path, item_name)
            shutil.move(source_item, destination_item)
            if self.log_callback:
                self.log_callback(f"Moved {source_item} to {destination_item}")

    def cleanup(self) -> None:
        """Deletes the ghost folder and all its contents.
        """
        shutil.rmtree(self.ghost_folder_path, ignore_errors=True)
        if self.log_callback:
            self.log_callback(f"Deleted ghost folder: {self.ghost_folder_path}")
