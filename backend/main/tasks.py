from celery import shared_task
from .models import Instances
from .steamcmd.mods_download import download_mods
from .modpreset.preset_extraction import preset_parser
from .modpreset.modpathing import check_installed
from .serverhandling.start_server import start_server
from .utils.logger import Logger
import psutil

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
    
@shared_task(bind=True)
def start_server_task(self, instance_id: int, arma3_dir: str) -> dict:
    """Starts the Arma 3 server and saves its PID.
    
    Args:
        instance_id (int): The ID of the instance to start.
        arma3_dir (str): The directory where Arma 3 is located.
        
    Returns:
        dict: The result of the start operation.
    """
    try:
        instance = Instances.objects.get(id=instance_id)
        
        # Check if a server is already running for this instance
        if instance.pid and psutil.pid_exists(instance.pid):
            instance.is_running = True
            instance.save()
            raise Exception(f'Nie udało się uruchomić serwera, ponieważ jest już uruchomiony.')

        self.update_state(state='PROGRESS', meta={'status': 'Uruchamianie serwera...'})
        
        log_file_path = instance.log_file.path
        start_file_path = instance.start_file_path

        process = start_server(arma3_dir, start_file_path, log_file_path)
        
        instance.is_running = True
        instance.pid = process.pid
        instance.save()

        self.update_state(state='SUCCESS', meta={'status': 'Pobieranie zakończone pomyślnie!'})
        return {'status': 'Serwer został uruchomiony pomyślnie.'}
    except Instances.DoesNotExist:
        raise Exception(f"Instance with id {instance_id} not found.")
    except Exception as e:
        raise e

@shared_task(bind=True)
def stop_server_task(self, instance_id: int) -> dict:
    """Stops the Arma 3 server by terminating its process.
    
    Args:
        instance_id (int): The ID of the instance to stop.
        
    Returns:
        dict: The result of the stop operation.
    """
    try:
        instance = Instances.objects.get(id=instance_id)
        pid = instance.pid

        if not pid or not psutil.pid_exists(pid):
            instance.pid = None
            instance.is_running = False
            instance.save()
            raise Exception(f'Nie udało się odnaleźć procesu serwera.')

        self.update_state(state='PROGRESS', meta={'status': 'Zatrzymywanie serwera...'})
        process = psutil.Process(pid)
        process.terminate() # Sends SIGTERM, a graceful shutdown signal
        
        # Wait a bit for graceful shutdown
        try:
            process.wait(timeout=30)
        except psutil.TimeoutExpired:
            # If it doesn't terminate, force kill it
            process.kill() # Sends SIGKILL

        instance.pid = None
        instance.save()

        self.update_state(state='SUCCESS', meta={'status': 'Zatrzymywanie serwera zakończone pomyślnie!'})
        return {'status': 'Serwer został zatrzymany pomyślnie.'}
    except Exception as e:
        raise e