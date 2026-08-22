from app.agent.action_dispatcher import ActionDispatcher


class TaskExecutor:
    """
    General-purpose task executor.

    Planner
        ↓
    Structured actions
        ↓
    ActionDispatcher
        ↓
    Computer
    """

    def __init__(self):
        self.dispatcher = ActionDispatcher()

    def execute(
        self,
        actions,
        application="Visual Studio Code"
    ):
        print("========================================")
        print("        NR AI TASK EXECUTOR")
        print("========================================")

        print(
            f"\n📋 Total actions: {len(actions)}"
        )

        for index, action in enumerate(
            actions,
            start=1
        ):
            print("\n----------------------------------------")
            print(
                f"▶️ ACTION {index}/{len(actions)}"
            )
            print("----------------------------------------")

            print(
                f"🔧 Type: {action.get('type')}"
            )

            if "target" in action:
                print(
                    f"🎯 Target: {action['target']}"
                )

            if "region" in action:
                print(
                    f"📦 Region: {action['region']}"
                )

            result = self.dispatcher.execute(
                action,
                application=application
            )

            if not result["success"]:

                print("\n========================================")
                print("🔴 TASK FAILED")
                print("========================================")

                print(
                    f"Failed action: {index}"
                )

                print(
                    f"Reason: "
                    f"{result.get('message', 'Unknown error')}"
                )

                return False

            print(
                f"\n🟢 Action {index} executed."
            )

        print("\n========================================")
        print("🟢 TASK EXECUTED")
        print("========================================")

        print(
            f"All {len(actions)} actions executed."
        )

        return True


if __name__ == "__main__":

    print("========================================")
    print("        NR AI TASK EXECUTOR")
    print("========================================")

    executor = TaskExecutor()

    actions = [
        {
            "type": "wait",
            "seconds": 0.5
        }
    ]

    executor.execute(actions)