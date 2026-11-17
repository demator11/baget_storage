import asyncio
import smtplib
import ssl
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from urllib.parse import urlparse

from baget_storage.domain.entities.email import EmailDeliveryError
from baget_storage.schemas.email import EmailJob


class SmtpEmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        from_address: str,
        frontend_base_url: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        use_tls: bool = False,
        timeout_seconds: float = 10,
    ) -> None:
        if starttls and use_tls:
            raise ValueError("Use either SMTP TLS or STARTTLS, not both")
        if timeout_seconds <= 0 or not 1 <= port <= 65535:
            raise ValueError("Invalid SMTP connection settings")
        self._host = host
        self._port = port
        self._from_address = from_address
        self._frontend_base_url = frontend_base_url.rstrip("/")
        self._username = username
        self._password = password
        self._starttls = starttls
        self._use_tls = use_tls
        self._timeout = timeout_seconds

    def _message(self, job: EmailJob) -> EmailMessage:
        data = job.payload
        if job.template == "notification":
            subject = data["title"]
            body = data["body"]
            resource = data.get("resource_type")
            resource_id = data.get("resource_id")
            paths = {
                "invoice": "payments",
                "document": "documents",
                "ticket": "tickets",
                "lease": "leases",
                "meter": "meters",
                "reading": "readings",
            }
            if resource in paths and resource_id:
                body += f"\n\n{self._frontend_base_url}/{paths[resource]}/{resource_id}"
        else:
            url = data["url"]
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or "\n" in url
                or "\r" in url
            ):
                raise EmailDeliveryError("invalid_email_link", permanent=True)
            if job.template == "verify_email":
                subject = "Подтверждение электронной почты СкладПро"
                body = f"Подтвердите адрес электронной почты, открыв ссылку:\n\n{url}"
            else:
                subject = "Восстановление пароля СкладПро"
                body = (
                    f"Чтобы задать новый пароль, откройте ссылку:\n\n{url}\n\n"
                    "Если вы не запрашивали восстановление, проигнорируйте это письмо."
                )
        message = EmailMessage()
        message["From"] = self._from_address
        message["To"] = str(job.recipient)
        message["Subject"] = subject.replace("\r", " ").replace("\n", " ")
        message["Date"] = format_datetime(datetime.now(UTC))
        message["Message-ID"] = f"<baget-{job.id}@{self._from_address.rsplit('@', 1)[-1]}>"
        message.set_content(body)
        return message

    def _send(self, job: EmailJob) -> None:
        try:
            message = self._message(job)
            tls = ssl.create_default_context()
            if self._use_tls:
                connection = smtplib.SMTP_SSL(
                    self._host, self._port, timeout=self._timeout, context=tls
                )
            else:
                connection = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
            with connection:
                connection.ehlo()
                if self._starttls:
                    connection.starttls(context=tls)
                    connection.ehlo()
                if self._username:
                    connection.login(self._username, self._password or "")
                refused = connection.send_message(message)
                if refused:
                    raise EmailDeliveryError("recipient_refused", permanent=True)
        except EmailDeliveryError:
            raise
        except smtplib.SMTPResponseException as exc:
            raise EmailDeliveryError(
                f"smtp_{exc.smtp_code}", permanent=500 <= exc.smtp_code < 600
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [details[0] for details in exc.recipients.values()]
            raise EmailDeliveryError(
                "recipient_refused", permanent=bool(codes) and all(code >= 500 for code in codes)
            ) from exc
        except (KeyError, ValueError) as exc:
            raise EmailDeliveryError("invalid_email_payload", permanent=True) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailDeliveryError("smtp_unavailable") from exc

    async def send(self, job: EmailJob) -> None:
        await asyncio.to_thread(self._send, job)
