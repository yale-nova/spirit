import os
import logging

class Logger:
    def prepare_logger(self, logger_name: str):
        # check directory for logging and create one if it does not exist
        log_dir = './logs'
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        else:
            # remove any existing log files
            for file in os.listdir(log_dir):
                if file.startswith(logger_name):
                    os.remove(os.path.join(log_dir, file))
        # initiate logger instance
        logger = logging.getLogger('{}'.format(logger_name))
        handler = logging.FileHandler('{}/{}.log'.format(log_dir, logger_name))
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        self.logger = logger

    def log_msg(self, msg: str, level: str = 'info'):
        if self.logger is None:
            raise Exception("Logger is not initialized.")
        if level == 'warning':
            self.logger.warning(msg)
        elif level == 'error':
            self.log_err(msg)
        else:
            self.logger.info(msg)

    def log_err(self, msg: str):
        if self.logger is None:
            raise Exception("Logger is not initialized.")
        self.logger.error(msg)

    def close(self):
        if self.logger is None:
            return
        handlers = self.logger.handlers[:]
        for handler in handlers:
            self.logger.removeHandler(handler)
            handler.close()
