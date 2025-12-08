from abc import abstractmethod
from datetime import datetime


class BaseWorkflow(object):

    @property
    def display(self):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return '[{}] Running Task: {}'.format(timestamp, self.description)

    __slots__ = ['name', 'description', 'driver']

    @abstractmethod
    def __init__(self, name, description, driver=None):
        self.name = name
        self.description = description
        self.driver = driver

    @abstractmethod
    def action(self, extra=None):
        pass
    
    def cleanup(self):
        if self.driver is None:
            return
        self.driver.cleanup()