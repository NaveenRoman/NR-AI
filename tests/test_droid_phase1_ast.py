"""
Tests for Droid Phase 1: Structured Kotlin and Java AST Intelligence Subsystem
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.agent.android_ast import (
    AndroidASTEngine,
    ClassEntry,
    CodeSanitizer,
    ImportEntry,
    MethodEntry,
    PropertyEntry,
    SourceASTReport,
)
from app.agent.android_safety import AndroidSafetyError, AndroidSafetyGate

SAMPLE_KOTLIN = """package com.example.app

import androidx.appcompat.app.AppCompatActivity
import android.os.Bundle
import androidx.compose.runtime.Composable
import androidx.compose.material3.Text

class MainActivity : AppCompatActivity() {
    private val tag: String = "MainActivity"
    private var counter: Int = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val testStr = "String with { false brace }" // comment with { brace }
    }

    fun incrementCounter() {
        counter++
    }
}

@Composable
fun Greeting(name: String) {
    Text(text = "Hello, $name!")
}
"""

SAMPLE_JAVA = """package com.example.app;

import java.util.List;
import android.content.Context;

public class DataRepository {
    private final String endpoint;
    public int cacheSize;

    public DataRepository(Context context) {
        this.endpoint = "https://api.example.com";
        this.cacheSize = 100;
    }

    public String fetchStatus() {
        return "OK";
    }
}
"""


class TestDroidPhase1AST(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.safety = AndroidSafetyGate()
        self.engine = AndroidASTEngine(safety_gate=self.safety)
        self.kt_file = Path(self.tmp_dir) / "MainActivity.kt"
        self.kt_file.write_bytes(SAMPLE_KOTLIN.encode("utf-8"))
        self.java_file = Path(self.tmp_dir) / "DataRepository.java"
        self.java_file.write_bytes(SAMPLE_JAVA.encode("utf-8"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_code_sanitizer_masks_strings_and_comments(self):
        """Verify strings and comments containing braces are masked so braces are not counted."""
        code = 'val s = "foo { bar }"; // comment { test\nfun test() {}'
        masked = CodeSanitizer.mask_code(code)
        self.assertNotIn('"', masked)
        self.assertNotIn("//", masked)
        # Check that only 2 braces remain (the ones for fun test)
        self.assertEqual(masked.count("{"), 1)
        self.assertEqual(masked.count("}"), 1)

    def test_02_parse_kotlin_ast(self):
        """Verify Kotlin parsing extracts package, imports, classes, methods, and composables."""
        report = self.engine.parse_file(self.kt_file)
        self.assertIsInstance(report, SourceASTReport)
        self.assertEqual(report.language, "kotlin")
        self.assertEqual(report.package_name, "com.example.app")
        self.assertEqual(len(report.imports), 4)

        self.assertIn("MainActivity", report.classes)
        main_cls = report.classes["MainActivity"]
        self.assertEqual(main_cls.kind, "class")
        self.assertIn("AppCompatActivity", main_cls.supertypes)

        # Properties
        self.assertIn("tag", main_cls.properties)
        self.assertFalse(main_cls.properties["tag"].is_mutable)
        self.assertIn("counter", main_cls.properties)
        self.assertTrue(main_cls.properties["counter"].is_mutable)

        # Methods
        self.assertIn("onCreate", main_cls.methods)
        self.assertIn("incrementCounter", main_cls.methods)

        # Top level composable
        self.assertIn("Greeting", report.top_level_methods)
        greeting = report.top_level_methods["Greeting"]
        self.assertTrue(greeting.is_composable)
        self.assertIn("@Composable", greeting.annotations)

    def test_03_parse_java_ast(self):
        """Verify Java parsing extracts class, methods, properties, and constructors."""
        report = self.engine.parse_file(self.java_file)
        self.assertEqual(report.language, "java")
        self.assertEqual(report.package_name, "com.example.app")
        self.assertEqual(len(report.imports), 2)

        self.assertIn("DataRepository", report.classes)
        repo_cls = report.classes["DataRepository"]
        self.assertEqual(repo_cls.visibility, "public")
        self.assertIn("fetchStatus", repo_cls.methods)

    def test_04_add_and_remove_import(self):
        """Verify safe import addition and removal."""
        res_add = self.engine.add_import(self.kt_file, "androidx.lifecycle.ViewModel")
        self.assertTrue(res_add["success"])
        self.assertTrue(Path(res_add["backup_file"]).exists())

        report = self.engine.parse_file(self.kt_file)
        import_targets = [i.target for i in report.imports]
        self.assertIn("androidx.lifecycle.ViewModel", import_targets)

        # Idempotence
        res_dup = self.engine.add_import(self.kt_file, "androidx.lifecycle.ViewModel")
        self.assertEqual(res_dup["action"], "already_present")

        # Remove
        res_rem = self.engine.remove_import(self.kt_file, "androidx.lifecycle.ViewModel")
        self.assertTrue(res_rem["success"])
        report_after = self.engine.parse_file(self.kt_file)
        self.assertNotIn("androidx.lifecycle.ViewModel", [i.target for i in report_after.imports])

    def test_05_add_method_to_class(self):
        """Verify method injection inside a class."""
        new_method = """fun resetCounter() {
    counter = 0
}"""
        res = self.engine.add_method(self.kt_file, "MainActivity", new_method)
        self.assertTrue(res["success"])

        report = self.engine.parse_file(self.kt_file)
        self.assertIn("resetCounter", report.classes["MainActivity"].methods)

    def test_06_replace_method_body(self):
        """Verify replacing the internal block body of an existing method."""
        new_body = """counter += 5
println("Counter is now $counter")"""
        res = self.engine.replace_method_body(
            self.kt_file,
            method_name="incrementCounter",
            new_body=new_body,
            class_name="MainActivity",
        )
        self.assertTrue(res["success"])

        content = self.kt_file.read_text(encoding="utf-8")
        self.assertIn("counter += 5", content)
        self.assertIn("Counter is now $counter", content)

    def test_07_add_annotation(self):
        """Verify annotation injection."""
        res = self.engine.add_annotation(
            self.kt_file,
            target_name="MainActivity",
            annotation_code="@AndroidEntryPoint",
            target_type="class",
        )
        self.assertTrue(res["success"])
        content = self.kt_file.read_text(encoding="utf-8")
        self.assertIn("@AndroidEntryPoint\nclass MainActivity", content)

    def test_08_rename_symbol(self):
        """Verify word-boundary safe symbol renaming."""
        res = self.engine.rename_symbol(
            self.kt_file,
            old_name="counter",
            new_name="mCounter",
        )
        self.assertTrue(res["success"])
        content = self.kt_file.read_text(encoding="utf-8")
        self.assertIn("private var mCounter: Int = 0", content)
        self.assertIn("mCounter++", content)

    def test_09_stale_target_protection_and_rollback(self):
        """Verify stale hash rejection and clean rollback capability."""
        with self.assertRaises(AndroidSafetyError):
            self.engine.add_import(self.kt_file, "foo.bar.Baz", expected_sha256="stale_hash_xyz")

        # Now do a valid mutation and roll back
        report_orig = self.engine.parse_file(self.kt_file)
        res = self.engine.add_import(self.kt_file, "foo.bar.Baz")
        self.assertTrue(res["success"])

        # Rollback
        restored = self.engine.rollback(self.kt_file, res["backup_file"])
        self.assertTrue(restored)

        report_after = self.engine.parse_file(self.kt_file)
        self.assertEqual(report_after.sha256, report_orig.sha256)

    def test_10_oversized_file_rejection(self):
        """Verify files over 100 KB are rejected."""
        oversized = Path(self.tmp_dir) / "Huge.kt"
        oversized.write_bytes(b"// " + b"k" * 105000)
        with self.assertRaises(ValueError) as ctx:
            self.engine.parse_file(oversized)
        self.assertIn("exceeds 100 KB", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
