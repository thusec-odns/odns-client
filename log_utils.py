import logging
from PyQt6.QtCore import QObject, pyqtSignal

class QtLogHandler(logging.Handler, QObject):
    """
    A custom logging handler that emits a PyQt signal for each log record.
    This allows log messages from any thread to be safely displayed in a PyQt widget.
    """
    log_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__()
        QObject.__init__(self, parent) # Initialize QObject part

    def emit(self, record):
        """
        Emits a log record by formatting it and sending it via a PyQt signal.
        """
        try:
            msg = self.format(record)
            self.log_signal.emit(msg)
        except Exception:
            self.handleError(record)

def setup_logging(): # Removed unused parameter
    """
    Configures the root logger to use the QtLogHandler.
    The returned handler's log_signal should be connected to a slot in the UI.
    """
    logger = logging.getLogger() # Get the root logger
    logger.setLevel(logging.DEBUG) # Set the desired logging level

    # Remove any existing handlers from the root logger to avoid duplicate logs
    # if this function is called multiple times or if basicConfig was called.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    qt_handler = QtLogHandler()

    # Set a formatter for the logs
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    qt_handler.setFormatter(formatter)
    
    logger.addHandler(qt_handler) # Add the Qt handler to the root logger
    
    return qt_handler # Return the handler so its signal can be connected externally

