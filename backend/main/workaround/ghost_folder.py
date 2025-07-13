import os
import shutil


class GhostFolder:
    def __init__(self, name: str, path: str):
        """Represents a ghost folder for temporarily storing downloaded files.

        Args:
            name (str): The name of the ghost folder.
            path (str): The base path where the ghost folder will be created.
        """
        self.name = name
        self.base_path = path
        self.ghost_folder_path = os.path.join(self.base_path, f"{self.name}_download_folder")
        self._create_folder()

    def _create_folder(self):
        """Creates the ghost folder for temporarily storing downloaded files.
        """
        os.makedirs(self.ghost_folder_path, exist_ok=True)

    def move_files(self, destination_path: str, internal_path: str = None):
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

    def delete_folder(self):
        """Deletes the ghost folder and all its contents.
        """
        if os.path.exists(self.ghost_folder_path):
            shutil.rmtree(self.ghost_folder_path)
