import threading
from flask_mail import Mail, Message

class EmailBackend:
    def __init__(self, app):
        """Initialize Flask-Mail with the Flask app"""
        self.app = app
        self.mail = Mail(app)
        self.sender_name = app.config.get('MAIL_DEFAULT_SENDER', ('BookVerse', None))

    def _send_async_email(self, app, msg):
        """Send email in background thread"""
        with app.app_context():
            self.mail.send(msg)

    def send_email(self, subject, recipients, body=None, html=None):
        """
        Send an email asynchronously using threading.
        """
        try:
            msg = Message(
                subject=subject,
                recipients=recipients,
                sender=self.sender_name,
                body=body,
                html=html
            )
            thread = threading.Thread(target=self._send_async_email, args=(self.app, msg))
            thread.start()
            return True, "Email is being sent in background."
        except Exception as e:
            return False, str(e)
