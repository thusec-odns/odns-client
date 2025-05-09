import subprocess
import platform
import os
import signal
import logging
import time
import psutil # For checking if process is running by PID

logger = logging.getLogger(__name__)

class CoreDNSProcessManager:
    """
    Manages the CoreDNS process.
    """
    def __init__(self, coredns_exe_path, corefile_path, pid_file_path):
        self.coredns_exe_path = coredns_exe_path
        self.corefile_path = corefile_path
        self.pid_file_path = pid_file_path
        self.process = None # Stores the subprocess.Popen object

    def is_running(self):
        """Checks if CoreDNS might be running by checking the PID file and process list."""
        if self.process and self.process.poll() is None:
            return True # Our own Popen object is running
        
        pid = self._read_pid_from_file()
        if pid:
            try:
                proc = psutil.Process(pid)
                # Check if the process name matches (heuristic)
                # This might need adjustment based on how CoreDNS names its process
                if "coredns" in proc.name().lower():
                    return proc.is_running()
            except psutil.NoSuchProcess:
                return False # PID in file but process doesn't exist
            except psutil.AccessDenied:
                return True # Cannot access, but process with PID exists
            except Exception as e:
                logger.warning(f"Error checking process status for PID {pid}: {e}")
                return False # Unclear, assume not running for safety
        return False

    def _read_pid_from_file(self):
        """Reads PID from the PID file."""
        try:
            if os.path.exists(self.pid_file_path):
                with open(self.pid_file_path, 'r') as f:
                    pid_str = f.read().strip()
                    if pid_str:
                        return int(pid_str)
        except Exception as e:
            logger.error(f"Error reading PID file {self.pid_file_path}: {e}")
        return None

    def _write_pid_to_file(self, pid):
        """Writes PID to the PID file."""
        try:
            with open(self.pid_file_path, 'w') as f:
                f.write(str(pid))
            logger.info(f"PID {pid} written to {self.pid_file_path}")
        except Exception as e:
            logger.error(f"Error writing PID to file {self.pid_file_path}: {e}")
            
    def _remove_pid_file(self):
        """Removes the PID file if it exists."""
        try:
            if os.path.exists(self.pid_file_path):
                os.remove(self.pid_file_path)
                logger.info(f"PID file {self.pid_file_path} removed.")
        except Exception as e:
            logger.error(f"Error removing PID file {self.pid_file_path}: {e}")

    def start(self):
        """
        Starts the CoreDNS process.
        Returns:
            tuple: (success: bool, message: str, process_handle: Popen or None)
        """
        if self.is_running():
            logger.warning("CoreDNS process is already running or PID file exists.")
            # It's better to try stopping first if this is unexpected.
            # For now, just report.
            return False, "CoreDNS 似乎已在运行。", None

        if not os.path.exists(self.coredns_exe_path):
            return False, f"CoreDNS 可执行文件未找到: {self.coredns_exe_path}", None
        if not os.path.exists(self.corefile_path):
            return False, f"Corefile 未找到: {self.corefile_path}", None

        command = [
            self.coredns_exe_path,
            "-conf", self.corefile_path,
            "--quiet", # As per original Go Fyne app
            "-pidfile", self.pid_file_path
        ]
        
        logger.info(f"Starting CoreDNS with command: {' '.join(command)}")
        
        creationflags = 0
        if platform.system().lower() == "windows":
            creationflags = subprocess.CREATE_NO_WINDOW # Hide console window on Windows

        try:
            # Start CoreDNS in its own directory to resolve relative paths in Corefile if any
            coredns_dir = os.path.dirname(self.coredns_exe_path)
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
                cwd=coredns_dir 
            )
            # PID is written by CoreDNS itself due to -pidfile argument.
            # We can optionally double-check or write it if CoreDNS fails to.
            # For now, rely on CoreDNS.
            logger.info(f"CoreDNS process started with PID: {self.process.pid}. Waiting for PID file.")
            # Give CoreDNS a moment to write the PID file
            time.sleep(0.5) 
            actual_pid = self._read_pid_from_file()
            if actual_pid != self.process.pid:
                logger.warning(f"PID in file ({actual_pid}) does not match process PID ({self.process.pid}). Using process PID.")
                # self._write_pid_to_file(self.process.pid) # Optionally force write our PID

            return True, "CoreDNS 进程已启动。", self.process
        except Exception as e:
            logger.error(f"Failed to start CoreDNS: {e}")
            self.process = None
            return False, f"启动 CoreDNS 失败: {e}", None

    def stop(self):
        """Stops the CoreDNS process."""
        logger.info("Attempting to stop CoreDNS process...")
        pid = self._read_pid_from_file()

        if self.process and self.process.poll() is None: # If we have an active Popen object
            logger.info(f"Stopping CoreDNS process (PID: {self.process.pid}) using internal handle.")
            try:
                if platform.system().lower() == "windows":
                    # Terminate might be cleaner than kill on Windows for console apps
                    self.process.terminate() 
                    try:
                        self.process.wait(timeout=5) # Wait for termination
                    except subprocess.TimeoutExpired:
                        logger.warning("CoreDNS did not terminate gracefully, killing.")
                        self.process.kill()
                else:
                    # Send SIGTERM first, then SIGKILL if necessary
                    self.process.send_signal(signal.SIGTERM)
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                         logger.warning("CoreDNS did not terminate with SIGTERM, sending SIGKILL.")
                         self.process.send_signal(signal.SIGKILL) # or self.process.kill()
                
                logger.info(f"CoreDNS process (PID: {self.process.pid}) stopped.")
            except Exception as e:
                logger.error(f"Error stopping CoreDNS process (PID: {self.process.pid}): {e}")
                # Fallback to PID file if internal handle failed
                if pid:
                    self._stop_by_pid(pid)
            finally:
                self.process = None # Clear internal handle
                self._remove_pid_file()
            return True, "CoreDNS 进程已停止。"

        elif pid: # No internal handle, but PID file exists
            logger.info(f"Stopping CoreDNS process (PID: {pid}) using PID file.")
            stopped = self._stop_by_pid(pid)
            self._remove_pid_file()
            if stopped:
                return True, "CoreDNS 进程已停止。"
            else:
                return False, f"尝试停止 PID {pid} 失败 (可能已停止)。"
        else:
            logger.info("CoreDNS process not running or PID file not found.")
            self._remove_pid_file() # Clean up if it exists but was empty/invalid
            return True, "CoreDNS 进程未运行。" # Considered success as it's not running

    def _stop_by_pid(self, pid):
        """Helper to stop a process by its PID."""
        try:
            if platform.system().lower() == "windows":
                # Using taskkill is more robust for external processes on Windows
                # /F for force, /PID for process ID, /T to kill child processes (if any)
                result = subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"], capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    logger.info(f"Successfully killed process with PID {pid} using taskkill.")
                    return True
                else:
                    # Return code 128 means process not found, which is fine.
                    if "could not be found" in result.stderr.lower() or "no running instance" in result.stderr.lower():
                        logger.info(f"Process with PID {pid} not found by taskkill (already stopped).")
                        return True
                    logger.error(f"Failed to kill process with PID {pid} using taskkill: {result.stderr}")
                    return False
            else: # Linux and macOS
                os.kill(pid, signal.SIGTERM) # Try SIGTERM first
                time.sleep(0.5) # Give it a moment
                try:
                    os.kill(pid, 0) # Check if process still exists
                    # If it exists, it didn't terminate with SIGTERM
                    logger.warning(f"Process {pid} did not terminate with SIGTERM, sending SIGKILL.")
                    os.kill(pid, signal.SIGKILL)
                except OSError: # Process already gone
                    pass 
                logger.info(f"Sent termination signals to process with PID {pid}.")
            return True
        except OSError as e: # Process already exited or permission issue
            logger.warning(f"Error sending signal to PID {pid} (likely already stopped or permission issue): {e}")
            return True # Assume stopped if OS error (like no such process)
        except Exception as e:
            logger.error(f"General error stopping process with PID {pid}: {e}")
            return False
