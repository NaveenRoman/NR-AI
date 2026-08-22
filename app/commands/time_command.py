from datetime import datetime


class TimeCommand:
    """Handles time and date related commands."""

    @staticmethod
    def get_time():
        return datetime.now().strftime("%I:%M %p")

    @staticmethod
    def get_date():
        return datetime.now().strftime("%A, %B %d, %Y")

    def execute(self, command):
        command = command.lower().strip()
        import re

        if re.search(r"\btime\b", command):
            current_time = self.get_time()
            return f"The current time is {current_time}."

        if re.search(r"\bdate\b", command) or re.search(r"\btoday\b", command):
            current_date = self.get_date()
            return f"Today is {current_date}."

        return None


if __name__ == "__main__":
    command = TimeCommand()

    print("================================")
    print("      NR AI TIME COMMAND")
    print("================================")

    print(command.execute("what is the time"))
    print(command.execute("what is today's date"))