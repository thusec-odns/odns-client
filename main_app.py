import sys
import os
import platform
import logging
import threading # For CoreDNS output reading
import time # For potential delays

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QTabWidget, QMessageBox, QLabel, QSizePolicy,
    QSpacerItem
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QIcon, QPalette, QColor, QFont # For styling (optional)

# Import utility modules
from log_utils import setup_logging, QtLogHandler 
from dns_utils import DNSManager
from process_utils import CoreDNSProcessManager

APP_NAME = "ODNS Controller"
VERSION = "1.0.0" # Incremented version for fixes

# --- Global logger setup ---
logger = logging.getLogger(__name__)

# --- Worker for CoreDNS output ---
class CoreDNSOutputReader(QObject):
    new_log_message = pyqtSignal(str)
    process_finished = pyqtSignal(int)

    def __init__(self, process_handle):
        super().__init__()
        self.process_handle = process_handle
        self._is_running = True

    def run(self):
        try:
            if self.process_handle.stdout:
                for line in iter(self.process_handle.stdout.readline, ''):
                    if not self._is_running: break
                    if line:
                        self.new_log_message.emit(f"[CoreDNS] {line.strip()}")
                self.process_handle.stdout.close()

            if self.process_handle.stderr:
                for line in iter(self.process_handle.stderr.readline, ''):
                    if not self._is_running: break
                    if line:
                        self.new_log_message.emit(f"[CoreDNS-ERR] {line.strip()}")
                self.process_handle.stderr.close()
            
            return_code = self.process_handle.wait()
            if self._is_running: # Only emit if not stopped externally
                 self.process_finished.emit(return_code)
        except Exception as e:
            if self._is_running:
                self.new_log_message.emit(f"[AppWatcher] Error reading CoreDNS output: {e}")
                self.process_finished.emit(-1) # Indicate error
        finally:
            logger.debug("CoreDNSOutputReader finished.")

    def stop(self):
        self._is_running = False

# --- Worker for long operations (Start/Stop/Restart) ---
class OperationWorker(QObject):
    finished = pyqtSignal(bool, str)
    log_message = pyqtSignal(str) # For intermediate logging from worker if needed

    def __init__(self, operation_func, *args):
        super().__init__()
        self.operation_func = operation_func
        self.args = args

    def run(self):
        try:
            success, message = self.operation_func(*self.args)
            self.finished.emit(success, message)
        except Exception as e:
            logger.error(f"Error in operation worker: {e}", exc_info=True)
            self.finished.emit(False, str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - v{VERSION}")
        self.setGeometry(200, 200, 750, 600) # Adjusted size for new tab

        if getattr(sys, 'frozen', False):
            if hasattr(sys, '_MEIPASS'):
                self.base_path = sys._MEIPASS
            else:
                self.base_path = os.path.dirname(sys.executable)
        else:
            self.base_path = os.path.dirname(os.path.abspath(__file__))
        
        logger.info(f"Application base path determined as: {self.base_path}")

        self.dns_manager = DNSManager()
        
        coredns_exe_name = "coredns.exe" if platform.system().lower() == "windows" else "coredns"
        coredns_exe_path = os.path.join(self.base_path, coredns_exe_name)
        corefile_path = os.path.join(self.base_path, "corefile") # Used by process_manager and editor
        pid_file_path = os.path.join(self.base_path, "coredns.pid")

        self.process_manager = CoreDNSProcessManager(coredns_exe_path, corefile_path, pid_file_path)

        self.is_running = False
        self.operation_in_progress = False
        self.is_exiting = False 
        self.thread = None # Initialize thread attribute
        self.worker = None # Initialize worker attribute


        self._init_ui()
        self._setup_app_logging()

        self._perform_startup_checks()
        self.load_corefile_content_to_editor() # Load corefile on startup
        self.update_button_ui()


    def _init_ui(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)

        self.tabs = QTabWidget()
        self.layout.addWidget(self.tabs)

        # --- Control Tab ---
        self.control_tab = QWidget()
        self.control_layout = QVBoxLayout(self.control_tab)
        self.control_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.status_label = QLabel("状态: 未知")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 16px; margin-bottom: 10px;")
        self.control_layout.addWidget(self.status_label)
        
        self.toggle_button = QPushButton("点击开始")
        self.toggle_button.setMinimumSize(200, 70)
        self.toggle_button.setStyleSheet("""
            QPushButton {
                font-size: 18px;
                font-weight: bold;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        self.toggle_button.clicked.connect(self.handle_toggle_button)
        
        button_container_layout = QHBoxLayout()
        button_container_layout.addStretch()
        button_container_layout.addWidget(self.toggle_button)
        button_container_layout.addStretch()
        self.control_layout.addLayout(button_container_layout)

        self.control_layout.addStretch()
        self.tabs.addTab(self.control_tab, "主控制")

        # --- Corefile Editor Tab ---
        self.corefile_tab = QWidget()
        self.corefile_layout = QVBoxLayout(self.corefile_tab)

        self.corefile_text_edit = QTextEdit()
        self.corefile_text_edit.setFont(QFont("Courier New", 10)) # Monospaced font for config
        self.corefile_text_edit.setPlaceholderText("Corefile 内容将在此处显示和编辑...")
        self.corefile_layout.addWidget(self.corefile_text_edit)

        corefile_button_layout = QHBoxLayout()
        self.load_corefile_button = QPushButton("重新加载 Corefile")
        self.load_corefile_button.clicked.connect(self.load_corefile_content_to_editor)
        corefile_button_layout.addWidget(self.load_corefile_button)
        
        corefile_button_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.save_restart_button = QPushButton("保存并重启服务")
        self.save_restart_button.setStyleSheet("""
            QPushButton {
                background-color: #2196F3; /* Blueish */
                color: white; 
                font-weight: bold;
                border-radius: 5px; /* Standard radius */
                padding: 8px 15px;
            }
            QPushButton:hover {
                background-color: #1976D2; /* Darker blue on hover */
            }
            QPushButton:disabled {
                background-color: #B0BEC5; /* Greyish when disabled */
            }
        """)
        self.save_restart_button.clicked.connect(self.handle_save_and_restart)
        corefile_button_layout.addWidget(self.save_restart_button)
        
        self.corefile_layout.addLayout(corefile_button_layout)
        self.tabs.addTab(self.corefile_tab, "Corefile 编辑")


        # --- Log Tab ---
        self.log_tab = QWidget()
        self.log_layout = QVBoxLayout(self.log_tab)
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        self.log_layout.addWidget(self.log_text_edit)
        self.tabs.addTab(self.log_tab, "日志")
        
        self.update_button_ui()

    def _setup_app_logging(self):
        self.qt_log_handler = setup_logging()
        self.qt_log_handler.log_signal.connect(self.append_log_message)
        logger.info(f"{APP_NAME} logging initialized.")

    def _check_coredns_files_instance_method(self):
        coredns_exe_name = "coredns.exe" if platform.system().lower() == "windows" else "coredns"
        coredns_path = os.path.join(self.base_path, coredns_exe_name)
        errors = []
        if not os.path.exists(coredns_path):
            errors.append(f"CoreDNS 可执行文件未找到: {coredns_path}")
        if not os.path.exists(self.process_manager.corefile_path):
            errors.append(f"Corefile 配置文件未找到: {self.process_manager.corefile_path}")
        return errors

    def _perform_startup_checks(self):
        logger.info("Performing startup checks...")
        file_errors = self._check_coredns_files_instance_method()
        if file_errors:
            error_msg = "\n".join(file_errors)
            logger.critical(f"Startup file errors: {error_msg}")
            QMessageBox.critical(self, "启动错误", f"必要文件缺失:\n{error_msg}\n请确保 coredns 可执行文件和 corefile 在程序目录下 (或已正确打包)。")
            self.toggle_button.setEnabled(False)
            self.save_restart_button.setEnabled(False)


        if not self.dns_manager.is_privileged():
            priv_msg = "权限不足。请使用管理员权限 (Windows) 或 sudo (Linux/macOS) 运行本程序。\nDNS 修改功能可能无法正常工作。"
            logger.warning(priv_msg)
            QMessageBox.warning(self, "权限提示", priv_msg)
        else:
            logger.info("Sufficient privileges detected.")
        
        if self.process_manager.is_running():
            logger.warning("CoreDNS appears to be running from a previous session (PID file found and process active).")
            QMessageBox.information(self, "提示", "检测到 CoreDNS 可能已在运行 (基于PID文件)。\n如果需要，请先手动停止或通过本程序尝试停止。")


    def append_log_message(self, message):
        self.log_text_edit.append(message)
        self.log_text_edit.verticalScrollBar().setValue(self.log_text_edit.verticalScrollBar().maximum())

    def update_button_ui(self):
        # Main toggle button
        if self.is_running:
            self.toggle_button.setText("运行中\n点击停止")
            self.toggle_button.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50; color: white; 
                    font-size: 18px; font-weight: bold; 
                    border-radius: 10px; padding: 10px;
                }
                QPushButton:hover { background-color: #45a049; }
                QPushButton:disabled { background-color: #A5D6A7; }
            """)
            self.status_label.setText("状态: <font color='green'><b>正在运行</b></font>")
        else:
            self.toggle_button.setText("已停止\n点击开始")
            self.toggle_button.setStyleSheet("""
                QPushButton {
                    background-color: #f44336; color: white; 
                    font-size: 18px; font-weight: bold; 
                    border-radius: 10px; padding: 10px;
                }
                QPushButton:hover { background-color: #e53935; }
                QPushButton:disabled { background-color: #EF9A9A; }
            """)
            self.status_label.setText("状态: <font color='red'><b>已停止</b></font>")
        
        self.toggle_button.setEnabled(not self.operation_in_progress)
        # Corefile editor buttons
        self.load_corefile_button.setEnabled(not self.operation_in_progress)
        self.save_restart_button.setEnabled(not self.operation_in_progress)


    def _start_operation(self, operation_func, *args):
        if self.operation_in_progress:
            logger.warning("Operation already in progress.")
            return

        self.operation_in_progress = True
        self.update_button_ui() # Disables buttons

        self.thread = QThread()
        self.worker = OperationWorker(operation_func, *args)
        self.worker.moveToThread(self.thread)

        self.worker.log_message.connect(self.append_log_message)
        self.worker.finished.connect(self.on_operation_finished)
        
        self.thread.started.connect(self.worker.run)
        # self.thread.finished.connect(self.thread.deleteLater) # Removed: manage deletion in on_operation_finished
        
        # Worker signals thread to quit, then worker schedules its own deletion
        self.worker.finished.connect(self.thread.quit) 
        self.worker.finished.connect(self.worker.deleteLater) 

        self.thread.start()

    def load_corefile_content_to_editor(self):
        logger.info(f"Attempting to load corefile from: {self.process_manager.corefile_path}")
        try:
            if os.path.exists(self.process_manager.corefile_path):
                with open(self.process_manager.corefile_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    self.corefile_text_edit.setPlainText(content)
                logger.info("Corefile content loaded into editor.")
            else:
                logger.warning("Corefile not found at specified path. Editor will be empty.")
                self.corefile_text_edit.setPlainText("# Corefile 未找到或无法加载\n")
                QMessageBox.warning(self, "加载失败", f"Corefile 文件未找到于:\n{self.process_manager.corefile_path}")
        except Exception as e:
            logger.error(f"Error loading corefile: {e}", exc_info=True)
            self.corefile_text_edit.setPlainText(f"# 加载 Corefile 出错:\n# {e}\n")
            QMessageBox.critical(self, "加载错误", f"加载 Corefile 时发生错误:\n{e}")

    def save_corefile_from_editor(self):
        logger.info(f"Attempting to save editor content to corefile: {self.process_manager.corefile_path}")
        try:
            content = self.corefile_text_edit.toPlainText()
            with open(self.process_manager.corefile_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info("Corefile content saved from editor.")
            return True, "Corefile 已成功保存。"
        except Exception as e:
            logger.error(f"Error saving corefile: {e}", exc_info=True)
            return False, f"保存 Corefile 失败: {e}"

    def _execute_start_sequence(self):
        logger.info("Executing start sequence...")
        if not self.dns_manager.is_privileged():
            return False, "权限不足，无法启动。"

        success_get_dns, msg_get_dns = self.dns_manager.get_original_dns()
        if not success_get_dns:
            return False, f"获取原始DNS失败: {msg_get_dns}"

        success_set_dns, msg_set_dns = self.dns_manager.set_dns("127.0.0.1")
        if not success_set_dns:
            logger.error(f"设置DNS到127.0.0.1失败: {msg_set_dns}. 尝试恢复原始DNS...")
            self.dns_manager.restore_original_dns()
            return False, f"设置DNS到127.0.0.1失败: {msg_set_dns}"
        
        success_start_proc, msg_start_proc, proc_handle = self.process_manager.start()
        if not success_start_proc:
            logger.error(f"启动CoreDNS进程失败: {msg_start_proc}. 尝试恢复原始DNS...")
            self.dns_manager.restore_original_dns()
            return False, f"启动CoreDNS进程失败: {msg_start_proc}"

        self.is_running = True
        self._start_coredns_output_reader(proc_handle)
        return True, "服务已成功启动。"

    def _execute_stop_sequence(self):
        logger.info("Executing stop sequence...")
        
        self._stop_coredns_output_reader()

        success_stop_proc, msg_stop_proc = self.process_manager.stop()
        if not success_stop_proc:
            logger.warning(f"停止CoreDNS进程时遇到问题: {msg_stop_proc} (仍将尝试恢复DNS)")

        success_restore_dns, msg_restore_dns = self.dns_manager.restore_original_dns()
        if not success_restore_dns:
            self.is_running = False 
            return False, f"服务核心进程可能已停止，但恢复原始DNS失败: {msg_restore_dns}"
            
        self.is_running = False
        return True, f"服务已成功停止。({msg_stop_proc})"

    def _execute_save_and_restart_sequence(self):
        logger.info("Executing save and restart sequence...")
        
        success_save, msg_save = self.save_corefile_from_editor()
        if not success_save:
            return False, msg_save 

        if self.is_running:
            logger.info("Service is running, stopping before restart...")
            self._stop_coredns_output_reader() 
            stop_success, stop_msg = self.process_manager.stop()
            if not stop_success:
                logger.warning(f"Problem stopping CoreDNS during restart: {stop_msg}. Proceeding with start attempt.")
            self.is_running = False 
            time.sleep(0.5) 
        
        logger.info("Starting/Restarting CoreDNS service with new corefile...")
        
        if not self.is_running: 
            if not self.dns_manager.is_privileged():
                return False, "权限不足，无法设置DNS并启动服务。"
            success_get_dns, msg_get_dns = self.dns_manager.get_original_dns() 
            if not success_get_dns:
                 logger.warning(f"获取原始DNS失败 (重启前): {msg_get_dns}")

            success_set_dns, msg_set_dns = self.dns_manager.set_dns("127.0.0.1")
            if not success_set_dns:
                logger.error(f"设置DNS到127.0.0.1失败 (重启前): {msg_set_dns}.")
                self.dns_manager.restore_original_dns()
                return False, f"设置DNS到127.0.0.1失败 (重启前): {msg_set_dns}"

        success_start_proc, msg_start_proc, proc_handle = self.process_manager.start()
        if not success_start_proc:
            logger.error(f"重启CoreDNS进程失败: {msg_start_proc}.")
            return False, f"重启CoreDNS进程失败: {msg_start_proc}"

        self.is_running = True
        self._start_coredns_output_reader(proc_handle)
        return True, "Corefile 已保存，服务已成功重启。"


    def on_operation_finished(self, success, message):
        logger.info(f"Operation finished. Success: {success}, Message: {message}")
        
        # Thread and worker cleanup
        if self.thread is not None:
            if self.thread.isRunning(): # Should have been quit by worker.finished signal
                logger.debug("Waiting for operation thread to finish...")
                self.thread.wait(2000) # Wait a bit longer
            if self.thread.isRunning(): # Still running after wait
                 logger.warning("Operation thread did not finish cleanly after quit and wait.")
            self.thread.deleteLater() # Schedule QThread for deletion
            self.thread = None
        
        # Worker is already connected to deleteLater via its finished signal.
        # Setting self.worker to None helps prevent reuse of a potentially deleted object.
        self.worker = None 

        self.operation_in_progress = False
        self.update_button_ui() # Re-enable buttons

        if not success:
            QMessageBox.critical(self, "操作失败", message)
        else:
            if "重启" in message or "保存" in message : 
                 QMessageBox.information(self, "操作成功", message)
        
        if self.is_exiting: 
            logger.info("Operation finished during exit sequence. Closing window.")
            # Ensure we are not in a recursive close
            # self.is_exiting should ideally be reset if close is cancelled by user
            # For now, assume this is the final close.
            QTimer.singleShot(0, self.close) # Use QTimer to call close from event loop


    def handle_toggle_button(self):
        if self.operation_in_progress:
            QMessageBox.information(self, "请稍候", "当前有操作正在进行中。")
            return

        if self.is_running:
            logger.info("Stop button pressed.")
            self._start_operation(self._execute_stop_sequence)
        else:
            logger.info("Start button pressed.")
            self._start_operation(self._execute_start_sequence)

    def handle_save_and_restart(self):
        if self.operation_in_progress:
            QMessageBox.information(self, "请稍候", "当前有操作正在进行中。")
            return
        logger.info("Save and Restart button pressed.")
        self._start_operation(self._execute_save_and_restart_sequence)


    def _start_coredns_output_reader(self, process_handle):
        if process_handle is None:
            logger.error("Cannot start CoreDNS output reader: process handle is None.")
            return

        self.output_reader_thread = QThread()
        self.coredns_reader = CoreDNSOutputReader(process_handle)
        self.coredns_reader.moveToThread(self.output_reader_thread)

        self.coredns_reader.new_log_message.connect(self.append_log_message)
        self.coredns_reader.process_finished.connect(self.on_coredns_process_finished)
        
        self.output_reader_thread.started.connect(self.coredns_reader.run)
        self.output_reader_thread.finished.connect(self.output_reader_thread.deleteLater)

        self.output_reader_thread.start()
        logger.info("CoreDNS output reader thread started.")

    def _stop_coredns_output_reader(self):
        if hasattr(self, 'coredns_reader') and self.coredns_reader:
            self.coredns_reader.stop()
        if hasattr(self, 'output_reader_thread') and self.output_reader_thread and self.output_reader_thread.isRunning():
            self.output_reader_thread.quit()
            if not self.output_reader_thread.wait(1000):
                logger.warning("CoreDNS output reader thread did not quit gracefully. Terminating.")
                self.output_reader_thread.terminate()
            logger.info("CoreDNS output reader thread stopped.")
        self.coredns_reader = None
        self.output_reader_thread = None


    def on_coredns_process_finished(self, return_code):
        logger.info(f"CoreDNS process finished with return code: {return_code}")
        self._stop_coredns_output_reader()

        if self.is_running: 
            logger.warning("CoreDNS process exited unexpectedly!")
            self.is_running = False
            self.update_button_ui()
            QMessageBox.warning(self, "CoreDNS 意外停止", f"CoreDNS 进程已意外退出，返回码: {return_code}。\n可能需要手动检查DNS设置。")


    def closeEvent(self, event):
        logger.info(f"Close event triggered. is_exiting: {self.is_exiting}, operation_in_progress: {self.operation_in_progress}")

        if self.is_exiting and not self.operation_in_progress: 
            logger.info("Accepting programmatic close.")
            event.accept()
            return

        if self.operation_in_progress:
            # If an operation is in progress, and we are not already in a controlled exit,
            # ask the user if they want to cancel the exit.
            if not self.is_exiting:
                 reply = QMessageBox.question(self, '操作进行中',
                                             "当前有操作正在进行中。您确定要退出吗？\n(这可能导致未完成的操作被中断)",
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                             QMessageBox.StandardButton.No)
                 if reply == QMessageBox.StandardButton.No:
                     logger.info("User cancelled exit due to ongoing operation.")
                     event.ignore()
                     return
                 else: # User chose to exit despite ongoing operation
                     logger.warning("User chose to exit despite ongoing operation. Attempting to stop reader.")
                     self._stop_coredns_output_reader() # Best effort
                     event.accept() # Allow exit, OS will handle child processes
                     return 
            else: # Already in controlled exit, but op still running (shouldn't happen if logic is right)
                logger.warning("Operation still in progress during controlled exit. Ignoring close event for now.")
                event.ignore()
                return


        if self.is_running:
            reply = QMessageBox.question(self, '确认退出',
                                         "CoreDNS 正在运行。您想在退出前停止它并恢复DNS设置吗？",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                                         QMessageBox.StandardButton.Yes) 

            if reply == QMessageBox.StandardButton.Yes:
                logger.info("User chose to stop CoreDNS and exit. Initiating controlled shutdown.")
                self.is_exiting = True 
                # self.toggle_button.setEnabled(False) # update_button_ui will handle this
                self.operation_in_progress = True # Manually set to disable buttons immediately
                self.update_button_ui()
                self._start_operation(self._execute_stop_sequence) # This is threaded
                event.ignore() 
            elif reply == QMessageBox.StandardButton.No:
                logger.info("User chose to exit without stopping CoreDNS (DNS will not be restored by app).")
                self._stop_coredns_output_reader()
                event.accept()
            else: 
                logger.info("User cancelled exit.")
                event.ignore()
        else:
            logger.info("Exiting application (service was not running).")
            self._stop_coredns_output_reader()
            event.accept()


def main():
    app = QApplication(sys.argv)
    # app.setWindowIcon(QIcon(os.path.join(os.path.dirname(__file__), 'app_icon.png')))

    main_window = MainWindow()
    main_window.show()
    
    exit_code = app.exec()
    logger.info(f"Application exited with code {exit_code}.")
    sys.exit(exit_code)

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, 
                        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S',
                        handlers=[logging.StreamHandler(sys.stdout)])
    main()

