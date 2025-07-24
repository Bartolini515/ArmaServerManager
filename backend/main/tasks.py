from celery import shared_task

from .models import Instances
from .steamcmd.mods_download import download_mods
from .modpreset.preset_extraction import preset_parser
from .modpreset.modpathing import check_installed
from .utils.logger import Logger

@shared_task(bind=True)
def download_mods_task(self, instance_id: int, name: str, user: str, file_path: str, mods_directory: str) -> dict:
    """Downloads mods based on the provided file path and directory.

    Args:
        instance: The object of an instance.
        name (str): The name of the instance.
        file_path (str): The path to the file containing the mod list.
        mods_directory (str): The directory where mods are located.

    Returns:
        dict: The result of the download operation.
    """
    try:
        self.update_state(state='PROGRESS', meta={'status': 'Rozpoczynanie pobierania...'})
        
        operations_logger = Logger(name="operations", user=user)
        download_logger = Logger(name="download", user=user)
        workshop_ids = preset_parser(file_path, log_callback=operations_logger.log)
        mods_to_download = check_installed(wids=workshop_ids, mods_dir=mods_directory, log_callback=operations_logger.log)[1]
        if mods_to_download:
            self.update_state(state='PROGRESS', meta={'status': 'Pobieranie modów...'})
            failed_mods = download_mods(mods_to_download=mods_to_download, name=name, logger=download_logger)

            if failed_mods:
                raise Exception(f'Nie udało się pobrać modów: {failed_mods}')

        instance = Instances.objects.get(id=instance_id)
        instance.is_ready = True
        instance.save()

        self.update_state(state='SUCCESS', meta={'status': 'Pobieranie zakończone pomyślnie!'})
        Logger.write_all_logs(one_directory=True, user=user)
        return {'status': 'Pobieranie zakończone pomyślnie!'}
    except Exception as e:
        Logger.write_all_logs(one_directory=True, user=user)
        raise e