import platform
import os
import shutil


class SystemCommand:
    """Provides information about the computer running NR AI."""

    @staticmethod
    def get_os():
        return f"{platform.system()} {platform.release()}"

    @staticmethod
    def get_machine():
        return platform.machine()

    @staticmethod
    def get_processor():
        processor = platform.processor()
        return processor if processor else "Processor information unavailable."

    @staticmethod
    def get_python_version():
        return platform.python_version()

    @staticmethod
    def get_memory():
        try:
            import psutil

            memory = psutil.virtual_memory()
            total_gb = memory.total / (1024 ** 3)
            available_gb = memory.available / (1024 ** 3)

            return (
                f"{total_gb:.1f} GB total RAM, "
                f"{available_gb:.1f} GB available."
            )

        except ImportError:
            return "RAM information is currently unavailable."

    @staticmethod
    def get_disk():
        try:
            disk = shutil.disk_usage("C:\\")
            total_gb = disk.total / (1024 ** 3)
            free_gb = disk.free / (1024 ** 3)

            return (
                f"{total_gb:.1f} GB total storage, "
                f"{free_gb:.1f} GB free on the C drive."
            )

        except Exception:
            return "Storage information is currently unavailable."

    def execute(self, command):
        command = command.lower().strip()
        import re

        if "operating system" in command or command == "os":
            return f"You are running {self.get_os()}."

        if "processor" in command or re.search(r"\bcpu\b", command):
            return f"Your processor is {self.get_processor()}."

        if re.search(r"\bram\b", command) or re.search(r"\bmemory\b", command):
            return f"You have {self.get_memory()}"

        if "storage" in command or re.search(r"\bdisk\b", command):
            return self.get_disk()

        if re.search(r"\bcomputer\b", command) or "system information" in command:
            return (
                f"Operating system: {self.get_os()}. "
                f"Processor: {self.get_processor()}. "
                f"Python: {self.get_python_version()}. "
                f"RAM: {self.get_memory()}"
            )

        return None


if __name__ == "__main__":
    command = SystemCommand()

    print("================================")
    print("     NR AI SYSTEM COMMAND")
    print("================================")

    print("\nOS:")
    print(command.execute("what operating system am I using"))

    print("\nCPU:")
    print(command.execute("what processor do I have"))

    print("\nRAM:")
    print(command.execute("how much RAM do I have"))

    print("\nSystem:")
    print(command.execute("give me system information"))