from app.agent.task_planner import TaskPlanner
from app.agent.task_executor import TaskExecutor


class PlannerExecutor:

    def __init__(self):
        self.planner = TaskPlanner()
        self.executor = TaskExecutor()

    def run(self, command):

        print("========================================")
        print("        NR AI PLANNER → EXECUTOR")
        print("========================================")

        print(
            f"\n🧠 User command:\n{command}"
        )

        # -------------------------------
        # PLAN
        # -------------------------------

        print("\n----------------------------------------")
        print("🧠 CREATING TASK PLAN")
        print("----------------------------------------")

        plan = self.planner.plan(command)

        if not plan["success"]:

            print("\n🔴 PLANNING FAILED")

            print(
                f"Reason: {plan['message']}"
            )

            return False

        actions = plan["actions"]

        print("\n🟢 PLAN CREATED")

        print(
            f"📋 Actions: {len(actions)}"
        )

        for index, action in enumerate(
            actions,
            start=1
        ):

            print(
                f"\nACTION {index}"
            )

            print(
                f"  Type: {action.get('type')}"
            )

            print(
                f"  Target: "
                f"{action.get('target', '-')}"
            )

            print(
                f"  Region: "
                f"{action.get('region', '-')}"
            )

        # -------------------------------
        # EXECUTE
        # -------------------------------

        print("\n----------------------------------------")
        print("🤖 EXECUTING TASK")
        print("----------------------------------------")

        success = self.executor.execute(
            actions
        )

        # -------------------------------
        # RESULT
        # -------------------------------

        print("\n========================================")

        if success:

            print(
                "🟢 PLANNER → EXECUTOR SUCCESS"
            )

            print(
                "\n🎉 Task completed."
            )

        else:

            print(
                "🔴 PLANNER → EXECUTOR FAILED"
            )

        print("========================================")

        return success


if __name__ == "__main__":

    print("========================================")
    print("        NR AI PLANNER → EXECUTOR")
    print("========================================")

    command = input(
        "\nTask: "
    ).strip()

    system = PlannerExecutor()

    system.run(command)