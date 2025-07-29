from celery import shared_task
from .models import Instances
from .steamcmd.mods_download import download_mods
from .modpreset.preset_extraction import preset_parser
from .modpreset.modpathing import check_installed
from .serverhandling.start_server import start_server
from .utils.logger import Logger
import psutil
from django.core.cache import cache
from servermanager.celery import app as celery_app

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
        
        self.update_state(state='PROGRESS', meta={'status': 'Zatrzymywanie serwera...'})
        
        pids_to_kill = set()
        if pid and psutil.pid_exists(pid):
            pids_to_kill.add(pid)
            
        try:
            for conn in psutil.net_connections(kind='udp'):
                if conn.laddr.port == instance.port and conn.pid:
                    pids_to_kill.add(conn.pid)
        except Exception:
                pass

        if not pids_to_kill:
            instance.pid = None
            instance.is_running = False
            instance.save()
            raise Exception(f'Nie udało się odnaleźć procesu serwera.')

        for process_pid in pids_to_kill:
            try:
                process = psutil.Process(process_pid)
                # Terminate children first
                for child in process.children(recursive=True):
                    try:
                        child.terminate()
                        child.wait(timeout=30)
                    except psutil.TimeoutExpired:
                        child.kill()
                    except psutil.NoSuchProcess:
                        continue
                
                # Terminate the main process
                try:
                    process.terminate()
                    process.wait(timeout=30)
                except psutil.TimeoutExpired:
                    process.kill()
                except psutil.NoSuchProcess:
                    continue

            except psutil.NoSuchProcess:
                # Process might have already been terminated
                continue

        instance.pid = None
        instance.is_running = False
        instance.save()
        
        cache_key = f"terminate_task_{instance.id}"
        old_terminate_task = cache.get(cache_key)
        if old_terminate_task:
                celery_app.control.revoke(old_terminate_task, terminate=True)

        self.update_state(state='SUCCESS', meta={'status': 'Zatrzymywanie serwera zakończone pomyślnie!'})
        return {'status': 'Serwer został zatrzymany pomyślnie.'}
    except Exception as e:
        raise e
    
@shared_task()
def check_all_servers_status_task():
    """Checks the status of all servers and updates their state."""
    instances = Instances.objects.all()
    for instance in instances:
        if instance.pid and psutil.pid_exists(instance.pid):
            instance.is_running = True
        else:
            instance.is_running = False
            instance.pid = None
        instance.save()
    return {'status': 'Status wszystkich serwerów został zaktualizowany.'}

@shared_task()
def instance_timeout_task(instance_id: int):
    """Handles the timeout for a specific instance."""
    try:
        instance = Instances.objects.get(id=instance_id)
        if instance.is_running:
            stop_server_task.delay(instance_id)
    except Instances.DoesNotExist:
        pass