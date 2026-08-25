from __future__ import annotations

import os
import posixpath
import shutil
import socket
from pathlib import Path

try:
    import paramiko
except ImportError:  # Local-only delivery remains available without the SFTP extra.
    paramiko = None

from .config import Config


class DeliveryError(RuntimeError):
    pass


class Delivery:
    def __init__(self, config: Config):
        self.config = config

    @property
    def configured(self) -> bool:
        return bool(
            self.config.local_output_dir
            or (
                self.config.sftp_host
                and self.config.sftp_user
                and self.config.sftp_key
                and self.config.sftp_dir
            )
        )

    def health(self) -> dict[str, object]:
        if self.config.local_output_dir:
            return {
                "configured": True,
                "kind": "local",
                "healthy": self.config.local_output_dir.is_dir(),
                "destination": str(self.config.local_output_dir),
            }
        if not self.config.sftp_host:
            return {"configured": False, "kind": "sftp", "healthy": False, "detail": "not configured"}
        try:
            with socket.create_connection((self.config.sftp_host, self.config.sftp_port), timeout=2):
                return {
                    "configured": self.configured,
                    "kind": "sftp",
                    "healthy": self.configured,
                    "destination": f"{self.config.sftp_user}@{self.config.sftp_host}:{self.config.sftp_dir}",
                }
        except OSError as error:
            return {
                "configured": self.configured,
                "kind": "sftp",
                "healthy": False,
                "destination": self.config.sftp_host,
                "detail": str(error),
            }

    def send(self, job_id: str, result_dir: Path) -> str:
        if self.config.local_output_dir:
            destination = self.config.local_output_dir / job_id
            partial = destination.with_name(f"{destination.name}.partial")
            if destination.exists():
                return str(destination)
            shutil.rmtree(partial, ignore_errors=True)
            shutil.copytree(result_dir, partial)
            os.replace(partial, destination)
            return str(destination)
        if not self.configured:
            raise DeliveryError("Output destination is not configured")
        return self._send_sftp(job_id, result_dir)

    def _send_sftp(self, job_id: str, result_dir: Path) -> str:
        if paramiko is None:
            raise DeliveryError("Paramiko is required for SFTP delivery")
        key_path = self.config.sftp_key
        if key_path is None or not key_path.is_file():
            raise DeliveryError("Configured SFTP private key does not exist")
        transport: paramiko.Transport | None = None
        try:
            private_key = _load_private_key(key_path)
            transport = paramiko.Transport((self.config.sftp_host, self.config.sftp_port))
            transport.banner_timeout = 15
            transport.auth_timeout = 15
            transport.connect(username=self.config.sftp_user, pkey=private_key)
            with paramiko.SFTPClient.from_transport(transport) as sftp:
                root = self.config.sftp_dir.rstrip("/")
                partial_dir = posixpath.join(root, f"{job_id}.partial")
                final_dir = posixpath.join(root, job_id)
                _mkdirs(sftp, root)
                try:
                    sftp.stat(final_dir)
                    return f"sftp://{self.config.sftp_host}{final_dir}"
                except OSError:
                    pass
                _mkdirs(sftp, partial_dir)
                for local_path in sorted(path for path in result_dir.rglob("*") if path.is_file()):
                    relative = local_path.relative_to(result_dir).as_posix()
                    remote_path = posixpath.join(partial_dir, relative)
                    _mkdirs(sftp, posixpath.dirname(remote_path))
                    sftp.put(str(local_path), remote_path, confirm=True)
                    if sftp.stat(remote_path).st_size != local_path.stat().st_size:
                        raise DeliveryError(f"SFTP size verification failed for {relative}")
                sftp.rename(partial_dir, final_dir)
                return f"sftp://{self.config.sftp_host}{final_dir}"
        except (OSError, paramiko.SSHException) as error:
            raise DeliveryError(f"SFTP delivery failed: {error}") from error
        finally:
            if transport is not None:
                transport.close()


def _load_private_key(path: Path) -> paramiko.PKey:
    if paramiko is None:
        raise DeliveryError("Paramiko is required for SFTP delivery")
    last_error: Exception | None = None
    for key_type in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return key_type.from_private_key_file(str(path))
        except Exception as error:
            last_error = error
    raise DeliveryError(f"Could not load SFTP key {path}: {last_error}")


def _mkdirs(sftp: paramiko.SFTPClient, path: str) -> None:
    if not path or path == "/":
        return
    prefix = "/" if path.startswith("/") else ""
    current = prefix
    for part in [segment for segment in path.split("/") if segment]:
        current = posixpath.join(current, part)
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)
