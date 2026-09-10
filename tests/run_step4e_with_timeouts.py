import concurrent.futures
import os
from pathlib import Path
import sys
import time
import traceback
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.test_step4e_unified_agent import TestStep4EUnifiedComputerAgent

PER_TEST_TIMEOUT_SECONDS = 15.0


def run_test_with_timeout(test_name: str, timeout: float = PER_TEST_TIMEOUT_SECONDS):
    case = TestStep4EUnifiedComputerAgent(test_name)
    test_method = getattr(case, test_name)

    start_t = time.time()
    passed = False
    error_msg = None

    try:
        case.setUp()
    except Exception as e:
        return False, time.time() - start_t, f"setUp failed: {e}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(test_method)
        try:
            future.result(timeout=timeout)
            passed = True
        except concurrent.futures.TimeoutError:
            passed = False
            error_msg = f"TEST TIMED OUT (exceeded {timeout:.1f}s limit)"
        except Exception as ex:
            passed = False
            error_msg = f"{type(ex).__name__}: {ex}"

    try:
        case.tearDown()
    except Exception as e:
        if passed:
            passed = False
            error_msg = f"tearDown failed: {e}"

    duration = time.time() - start_t
    return passed, duration, error_msg


def main():
    print("=" * 70)
    print("      NR-AI STEP 4E VERIFICATION RUNNER (WITH PER-TEST TIMEOUTS)")
    print(f"      Per-Test Timeout: {PER_TEST_TIMEOUT_SECONDS:.1f} seconds")
    print("=" * 70)

    # Collect all test methods in order
    test_methods = [
        m for m in dir(TestStep4EUnifiedComputerAgent)
        if m.startswith("test_") and callable(getattr(TestStep4EUnifiedComputerAgent, m))
    ]
    test_methods.sort()

    total = len(test_methods)
    passed_count = 0
    failed_count = 0
    results = []

    suite_start = time.time()

    for idx, test_name in enumerate(test_methods, 1):
        passed, duration, err = run_test_with_timeout(test_name, timeout=PER_TEST_TIMEOUT_SECONDS)
        status_tag = "PASS" if passed else "FAIL"
        if passed:
            passed_count += 1
            print(f"[{status_tag}] ({duration:5.2f}s) ({idx:2d}/{total}) {test_name}")
        else:
            failed_count += 1
            print(f"[{status_tag}] ({duration:5.2f}s) ({idx:2d}/{total}) {test_name} -> {err}")

        results.append({
            "test": test_name,
            "passed": passed,
            "duration": duration,
            "error": err,
        })

    suite_duration = time.time() - suite_start

    print("\n" + "=" * 70)
    print("                      STEP 4E VERIFICATION SUMMARY")
    print("=" * 70)
    print(f"Total Tests Executed : {total}")
    print(f"Passed               : {passed_count}")
    print(f"Failed               : {failed_count}")
    print(f"Total Suite Duration : {suite_duration:.2f} seconds")
    print("=" * 70)

    if failed_count == 0:
        print(">>> OVERALL STATUS: ALL STEP 4E TESTS PASSED (OK) <<<")
    else:
        print(f">>> OVERALL STATUS: {failed_count} TEST(S) FAILED <<<")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
