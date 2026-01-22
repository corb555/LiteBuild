import subprocess
import platform

class SleepInhibitor:
    """
    Prevents the OS from going to sleep during long tasks.
    Supports macOS (caffeinate) and Linux (systemd-inhibit).
    """
    def __init__(self):
        self._process = None

    def start(self):
        """Starts the keep-awake process."""
        if self._process:
            return # Already running

        system = platform.system()

        if system == "Darwin":
            # macOS: Prevent idle sleep
            cmd = ["caffeinate", "-i"]
        elif system == "Linux":
            # Linux: Use systemd-inhibit to block idle sleep
            # We wrap 'sleep infinity' so the lock persists until we kill the process
            cmd = [
                "systemd-inhibit",
                "--what=idle",           # Block system sleep, allow screen off
                "--who=LiteBuild",       # Name of our app
                "--why=Building Maps",   # Reason shown in logs
                "--mode=block",
                "sleep", "infinity"      # The dummy command to keep running
            ]
        else:
            print(f"⚠️  Sleep inhibitor not supported on {system}")
            return

        try:
            # start_new_session=True ensures the inhibitor doesn't die
            # if the parent shell is weirdly intertwined (optional but safe)
            self._process = subprocess.Popen(cmd, start_new_session=True)
        except FileNotFoundError:
            print(f"⚠️  Sleep inhibitor tool not found (tried: {cmd[0]}). System may sleep.")
        except Exception as e:
            print(f"⚠️  Could not start sleep inhibitor: {e}")

    def stop(self):
        """Stops the keep-awake process."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=1)
            except Exception:
                # If it refuses to die, kill it forcefully
                if self._process:
                    self._process.kill()
            finally:
                self._process = None