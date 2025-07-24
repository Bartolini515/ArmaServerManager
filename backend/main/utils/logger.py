import datetime
import os
from .config import config


class Logger:
    _instances = {}
    _error_messages = []

    def __init__(self, user: str, name: str) -> None:
        """Logger for the application.

        Args:
            name (str): The name of the function to log.
            user (str): The user for whom the logger is created.
        """
        self.name = name
        self.user = user
        self.log_messages = []
        self.logs_directory = config.get('paths.logs_directory', '/logs')
        if not os.path.exists(self.logs_directory):
            os.makedirs(self.logs_directory)
        if user not in Logger._instances:
            Logger._instances[user] = []
        Logger._instances[user].append(self)
        
    def log(self, message: str) -> None:
        """Logs a message with a timestamp.

        Args:
            message (str): The message to log.
        """
        current_date_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.log_messages.append(f"{current_date_time} - {message}\n")
        
    def write_log_to_file(self, log_date: bool = True) -> None:
        """Writes the log messages to a file.
        
        Args:
            log_date (bool, optional): Whether to include the date in the log file name. Defaults to True.
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
    def error(cls, *args) -> None:
        """
        Logs an error message.
        Can be called on an instance (logger.error(msg)) or on the class (Logger.error(msg)).
        When called on an instance, it logs to the instance's log and the global error log.
        When called on the class, it only logs to the global error log.
        """
        # Check how the method was called
        if isinstance(args[0], cls):
            # Called on an instance: logger.error(message)
            # args will be (self, message)
            self, message = args
            name = self.name
            current_date_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            # Add to instance's own log
            self.log_messages.append(f"{current_date_time} - ERROR: {message}\n")
        else:
            # Called on the class: Logger.error(message)
            # args will be (message)
            message = args[0]
            name = "Global" # Generic name since not an instance

        current_date_time = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        cls._error_messages.append(f"{current_date_time} [{name}] - ERROR: {message}\n")
        
    @classmethod
    def write_errors_to_file(cls, log_date: bool = True, logs_directory: str = None) -> None:
        """Writes error messages to a file.

        Args:
            log_date (bool, optional): Whether to include the date in the log file name. Defaults to True.
            logs_directory (str, optional): The directory to save the log file. Defaults to None.

        Returns:
            _type_: _description_
        """
        if not cls._error_messages:
            return # Don't create an empty file

        logs_directory = logs_directory or config.get('paths.logs_directory', '/logs')
        if log_date:
            error_file = f"{logs_directory}/errors_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
        else:
            error_file = f"{logs_directory}/errors.txt"

        with open(error_file, 'w') as f:
            for message in cls._error_messages:
                f.write(message)
        cls._error_messages.clear()
        
    @classmethod
    def write_all_logs(cls, user: str = None, log_date: bool = True, one_directory: bool = False) -> None:
        """Writes all log messages from all instances to files.

        Args:
            user (str, optional): The user for whom to write logs. If None, writes logs for all users. Defaults to None.
            log_date (bool, optional): Whether to include the date in the log file names. Defaults to True.
            one_directory (bool, optional): Whether to write all logs to a single directory. Defaults to False.
        """
        logs_directory = None
        if one_directory:
            logs_directory = os.path.join(config.get('paths.logs_directory', '/logs'), f"{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}{'_user_' + user if user else ''}")
            log_date = False
            if not os.path.exists(logs_directory):
                os.makedirs(logs_directory)
        
        instances_to_process = []
        if user and user in cls._instances:
            instances_to_process.extend(cls._instances[user])
        elif not user:
            for user_instances in cls._instances.values():
                instances_to_process.extend(user_instances)

        for instance in instances_to_process:
            if one_directory:
                instance.logs_directory = logs_directory
            instance.write_log_to_file(log_date)
            
        cls.write_errors_to_file(log_date, logs_directory if one_directory else None)