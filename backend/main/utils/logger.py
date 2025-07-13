import datetime
import os
from config import Config


class Logger:
    _instances = []
    
    def __init__(self, name: str):
        """Logger for the application.

        Args:
            name (str): The name of the function to log.
        """
        self.name = name
        self.log_messages = []
        self.logs_directory = Config.get('logger.logs_directory', '/logs')
        if not os.path.exists(self.logs_directory):
            os.makedirs(self.logs_directory)
        Logger._instances.append(self)
        
    def log(self, message: str):
        """Logs a message with a timestamp.

        Args:
            message (str): The message to log.
        """
        current_date_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.log_messages.append(f"{current_date_time} - {message}\n")
            
    def write_log_to_file(self, log_date: bool = True):
        """Writes the log messages to a file.
        """
        if log_date:
            log_file = f"{self.logs_directory}/log_{self.name}_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
        else:
            log_file = f"{self.logs_directory}/log_{self.name}.txt"
        with open(log_file, 'w') as f:
            for message in self.log_messages:
                f.write(message + "\n")
        self.log_messages.clear()  # clear the list after writing to file
        
    @classmethod
    def write_all_logs(cls, log_date: bool = True, one_directory: bool = False):
        
        for instance in cls._instances:
            if one_directory:
                instance.logs_directory = os.path.join(instance.logs_directory, datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S'))
                if not os.path.exists(instance.logs_directory):
                    os.makedirs(instance.logs_directory)
                log_date = False
            instance.write_log_to_file(log_date)