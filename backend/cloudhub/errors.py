class HubError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status

class Conflict(Exception):
    """Compare-and-swap failed: reread all records before retry."""
