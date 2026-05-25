import os
import subprocess


class SSHDeployer:
    """Uploads a placement .txt to the Custom Compiler server via pscp."""

    def __init__(self):
        self.host = os.getenv("CC_SSH_HOST", "")
        self.user = os.getenv("CC_SSH_USER", "")
        self.password = os.getenv("CC_SSH_PASSWORD", "")
        self.remote_dir = os.getenv("CC_REMOTE_DIR", "")

    def is_configured(self) -> bool:
        return bool(self.host and self.user and self.remote_dir)

    def upload(self, local_path: str) -> tuple[bool, str]:
        """Upload local_path to remote_dir via pscp. Returns (ok, remote_path_or_error)."""
        filename = os.path.basename(local_path)
        remote_target = f"{self.remote_dir}/{filename}"

        cmd = ["pscp"]
        if self.password:
            cmd += ["-pw", self.password]
        cmd += [local_path, f"{self.user}@{self.host}:{remote_target}"]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return True, remote_target
            return False, (result.stderr or result.stdout).strip()
        except FileNotFoundError:
            return False, "pscp not found — install PuTTY tools and add to PATH"
        except subprocess.TimeoutExpired:
            return False, "Upload timed out after 30s"
        except Exception as e:
            return False, str(e)
