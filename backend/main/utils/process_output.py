import threading


def reader_thread(pipe, log_callback: callable = None) -> None:
    try:
        while True:
            line = pipe.readline()
            if not line:
                break
            
            processed_line = ""
            if isinstance(line, bytes):
                processed_line = line.decode('utf-8', errors='ignore').strip() + '\n'
            else: # It's a string
                processed_line = line.strip() + '\n'

            if log_callback:
                log_callback(processed_line)
            else:
                print(processed_line)
    finally:
        pipe.close()


def stream_process_output(process, log_callback: callable = None) -> None:
    stdout_thread = threading.Thread(target=reader_thread, args=(process.stdout, log_callback))
    stderr_thread = threading.Thread(target=reader_thread, args=(process.stderr, log_callback))

    stdout_thread.start()
    stderr_thread.start()

    stdout_thread.join()
    stderr_thread.join()

    process.wait()