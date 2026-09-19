"""
Tests for Droid Phase 4 Component 7: Test Failure -> Source -> Repair Intelligence.
"""
import unittest
from pathlib import Path
from app.agent.android_test_intelligence import (
    AndroidTestIntelligenceEngine,
    TestFailureKind,
    TestFailureEvidence,
)

class TestDroidPhase4TestIntelligence(unittest.TestCase):
    def setUp(self):
        self.engine = AndroidTestIntelligenceEngine()
        self.project_path = Path(r"C:\NR-AI\nr_android_test")

    def test_parse_console_output(self):
        sample_output = """
com.nrai.test.ExampleUnitTest > addition_isCorrect FAILED
    java.lang.AssertionError: expected:<4> but was:<5>
        at org.junit.Assert.fail(Assert.java:89)
        at com.nrai.test.ExampleUnitTest.addition_isCorrect(ExampleUnitTest.kt:15)
"""
        failures = self.engine.parse_console_output(sample_output)
        self.assertEqual(len(failures), 1)
        f = failures[0]
        self.assertEqual(f.test_class, "com.nrai.test.ExampleUnitTest")
        self.assertEqual(f.test_method, "addition_isCorrect")
        self.assertEqual(f.failure_kind, TestFailureKind.DIRECT_TEST_FAILURE)
        self.assertIn("ExampleUnitTest.kt", f.target_source_file or "")

    def test_runtime_failure_classification(self):
        sample_output = """
com.nrai.test.CrashTest > testDivide FAILED
    java.lang.ArithmeticException: / by zero
        at com.nrai.test.Calculator.divide(Calculator.kt:25)
        at com.nrai.test.CrashTest.testDivide(CrashTest.kt:12)
"""
        failures = self.engine.parse_console_output(sample_output)
        self.assertEqual(len(failures), 1)
        f = failures[0]
        self.assertEqual(f.failure_kind, TestFailureKind.RUNTIME_FAILURE)
        self.assertEqual(f.target_source_file, "Calculator.kt")
        self.assertEqual(f.target_line, 25)
