import threading


def reader_thread(pipe, log_callback):
    try:
        for line in iter(pipe.readline, b''):
            log_callback(line.decode('utf-8', errors='ignore').strip())
    finally:
        pipe.close()


def stream_process_output(process, log_callback):
    stdout_thread = threading.Thread(target=reader_thread, args=(process.stdout, log_callback))
    stderr_thread = threading.Thread(target=reader_thread, args=(process.stderr, log_callback))

    stdout_thread.start()
    stderr_thread.start()

    stdout_thread.join()
    stderr_thread.join()

    process.wait()