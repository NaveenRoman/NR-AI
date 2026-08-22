import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agent.code_writer import CodeWriter


class FlutterProjectDetector:
    """
    Detects whether a directory contains a Flutter / Dart project and identifies
    its configuration, platform targets, and project type.
    """

    @staticmethod
    def detect(project_dir: str | Path) -> Dict[str, Any]:
        p = Path(project_dir).resolve()
        if not p.exists() or not p.is_dir():
            return {"is_flutter": False, "reason": "Directory does not exist"}

        has_pubspec = (p / "pubspec.yaml").exists()
        has_lib = (p / "lib").exists() and (p / "lib").is_dir()
        has_main_dart = (p / "lib" / "main.dart").exists()

        if not has_pubspec and not has_main_dart:
            return {"is_flutter": False, "reason": "No pubspec.yaml or lib/main.dart found."}

        is_flutter = False
        uses_flutter_sdk = False
        project_name = p.name

        if has_pubspec:
            try:
                content = (p / "pubspec.yaml").read_text(encoding="utf-8", errors="ignore")
                if "sdk: flutter" in content or "flutter:" in content or "flutter_test:" in content:
                    uses_flutter_sdk = True
                    is_flutter = True
                name_match = re.search(r"^name:\s*([a-zA-Z0-9_]+)", content, re.MULTILINE)
                if name_match:
                    project_name = name_match.group(1)
            except Exception:
                pass

        if not is_flutter and (has_lib or has_main_dart):
            # Pure Dart project or basic Flutter app
            is_flutter = True

        # Detect supported platforms
        platforms = []
        for plat in ("android", "ios", "web", "windows", "macos", "linux"):
            if (p / plat).exists() and (p / plat).is_dir():
                platforms.append(plat)

        has_tests = (p / "test").exists() and (p / "test").is_dir()

        return {
            "is_flutter": is_flutter,
            "project_name": project_name,
            "project_path": str(p),
            "uses_flutter_sdk": uses_flutter_sdk,
            "has_main_dart": has_main_dart,
            "platforms": platforms,
            "has_tests": has_tests,
        }


class FlutterProjectInspector:
    """
    Deeply inspects a Flutter project: Dart files, dependencies, routes,
    widgets, models, services, and tests.
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()

    def inspect(self) -> Dict[str, Any]:
        detection = FlutterProjectDetector.detect(self.project_dir)
        if not detection.get("is_flutter"):
            return {
                "success": False,
                "is_flutter": False,
                "message": detection.get("reason", "Not a Flutter project"),
            }

        dependencies, dev_dependencies = self._parse_dependencies()
        dart_files = self._list_dart_files()
        screens = [f for f in dart_files if "screen" in f.lower() or "page" in f.lower() or "view" in f.lower()]
        models = [f for f in dart_files if "model" in f.lower()]
        services = [f for f in dart_files if "service" in f.lower() or "api" in f.lower()]
        tests = [f for f in dart_files if f.startswith("test/")]

        return {
            "success": True,
            "is_flutter": True,
            "project_name": detection["project_name"],
            "project_path": str(self.project_dir),
            "platforms": detection["platforms"],
            "dependencies_count": len(dependencies),
            "dependencies": dependencies,
            "dev_dependencies": dev_dependencies,
            "total_dart_files": len(dart_files),
            "screens": screens,
            "models": models,
            "services": services,
            "tests": tests,
            "dart_files": dart_files[:25],
        }

    def _parse_dependencies(self) -> Tuple[List[str], List[str]]:
        pubspec = self.project_dir / "pubspec.yaml"
        if not pubspec.exists():
            return [], []

        text = pubspec.read_text(encoding="utf-8", errors="ignore")
        deps = []
        dev_deps = []

        in_deps = False
        in_dev_deps = False

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            is_root = not line.startswith(" ") and not line.startswith("\t")

            if is_root:
                if stripped.startswith("dependencies:"):
                    in_deps = True
                    in_dev_deps = False
                elif stripped.startswith("dev_dependencies:"):
                    in_deps = False
                    in_dev_deps = True
                else:
                    in_deps = False
                    in_dev_deps = False
                continue

            if in_deps:
                pkg = stripped.split(":")[0].strip()
                if pkg and pkg not in {"flutter", "sdk"}:
                    deps.append(pkg)
            elif in_dev_deps:
                pkg = stripped.split(":")[0].strip()
                if pkg and pkg not in {"flutter_test", "sdk"}:
                    dev_deps.append(pkg)

        return deps, dev_deps

    def _list_dart_files(self) -> List[str]:
        files = []
        for f in self.project_dir.glob("**/*.dart"):
            if ".dart_tool" not in f.parts and "build" not in f.parts:
                files.append(str(f.relative_to(self.project_dir)).replace("\\", "/"))
        return sorted(files)


class FlutterErrorAnalyzer:
    """
    Parses and categorizes Dart / Flutter analyzer logs, compilation errors,
    and pubspec syntax errors.
    """

    @staticmethod
    def analyze(log: str) -> Dict[str, Any]:
        text = str(log or "")

        # 1. Dart Analyzer message format:
        # e.g., error • Undefined name 'xyz' • lib/main.dart:15:10 • undefined_identifier
        # e.g., [error] The method 'foo' isn't defined for the class 'Bar' (lib/screens/home.dart:22:5)
        analyzer_match = re.search(
            r"(?:error|warning|info)\s+•\s+([^\r\n•]+)\s+•\s+([^\r\n:]+):(\d+):(\d+)(?:\s+•\s+([^\r\n]+))?",
            text,
            re.IGNORECASE,
        )
        if analyzer_match:
            msg, filepath, line_no, col_no, rule = analyzer_match.groups()
            return {
                "category": "dart_analyzer",
                "file": filepath.strip(),
                "line": int(line_no),
                "column": int(col_no),
                "error": msg.strip(),
                "rule": (rule or "").strip(),
                "diagnosis": f"Dart static analysis error: {msg.strip()}",
                "suggestion": "Fix undefined identifiers, missing imports, or type mismatches in Dart source.",
            }

        # 2. Dart / Flutter compilation error format:
        # e.g., lib/main.dart:15:10: Error: Undefined name 'xyz'.
        compile_match = re.search(r"([^\r\n:]+\.dart):(\d+):(\d+):\s+Error:\s+([^\r\n]+)", text)
        if compile_match:
            filepath, line_no, col_no, error_desc = compile_match.groups()
            return {
                "category": "dart_compiler",
                "file": filepath.strip(),
                "line": int(line_no),
                "column": int(col_no),
                "error": error_desc.strip(),
                "diagnosis": f"Dart compiler error: {error_desc.strip()}",
                "suggestion": "Check variable declarations, method signatures, or class definitions.",
            }

        # 3. Pubspec resolution error:
        if "pubspec.yaml" in text and ("Error on line" in text or "version solving failed" in text or "Could not resolve" in text):
            return {
                "category": "flutter_pubspec",
                "file": "pubspec.yaml",
                "error": "Pub package resolution or YAML syntax error.",
                "diagnosis": "A dependency in pubspec.yaml has incompatible version constraints or invalid YAML formatting.",
                "suggestion": "Check package versions and indentation in pubspec.yaml.",
            }

        # 4. Flutter Test failure:
        if "Test failed" in text or "══╡ EXCEPTION CAUGHT BY FLUTTER TEST FRAMEWORK ╞═" in text:
            return {
                "category": "flutter_test_failure",
                "file": "test",
                "error": "Flutter widget or unit test expectation failed.",
                "diagnosis": "A widget tester or unit assertion did not match the expected UI/state condition.",
                "suggestion": "Update widget tree pump or assertion expectations in test/*.dart.",
            }

        return {
            "category": "unknown_flutter_error",
            "file": "lib",
            "error": text[:300].strip(),
            "diagnosis": "Flutter / Dart toolchain command failed.",
            "suggestion": "Run 'flutter analyze' or 'dart analyze' for detailed diagnostic output.",
        }


class FlutterToolchain:
    """
    Flutter & Dart Toolchain Manager for NR AI.

    Manages:
    - Scaffolding complete modern Flutter applications (MaterialApp, Theme, Screens, Models, Services, Tests)
    - Adding screens, widgets, models, services, and tests
    - Injecting dependencies (Firebase, HTTP, Provider, Shared Preferences)
    - Executing 'dart analyze', 'dart test', 'flutter pub get', and 'flutter test'
    - Probing connected Flutter/ADB devices
    """

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.writer = CodeWriter(workspace=str(self.workspace))

    def scaffold_project(
        self,
        project_name: str = "flutter_app",
        target_dir: Optional[str] = None,
        title: str = "NR AI Flutter App",
    ) -> Dict[str, Any]:
        """Scaffolds a complete, production-grade Flutter application architecture."""
        safe_name = re.sub(r"[^a-z0-9_]", "_", project_name.lower())
        root = (self.workspace / (target_dir or f"data/{safe_name}")).resolve()
        root.mkdir(parents=True, exist_ok=True)

        # 1. pubspec.yaml
        pubspec_yaml = (
            f"name: {safe_name}\n"
            f"description: A modern Flutter application built by NR AI.\n"
            "publish_to: 'none'\n"
            "version: 1.0.0+1\n\n"
            "environment:\n"
            "  sdk: '>=3.0.0 <4.0.0'\n\n"
            "dependencies:\n"
            "  flutter:\n"
            "    sdk: flutter\n"
            "  http: ^1.2.0\n"
            "  cupertino_icons: ^1.0.6\n\n"
            "dev_dependencies:\n"
            "  flutter_test:\n"
            "    sdk: flutter\n"
            "  flutter_lints: ^3.0.0\n"
            "  test: ^1.24.0\n\n"
            "flutter:\n"
            "  uses-material-design: true\n"
        )

        # 2. analysis_options.yaml
        analysis_options = (
            "include: package:flutter_lints/flutter.yaml\n\n"
            "linter:\n"
            "  rules:\n"
            "    prefer_const_constructors: true\n"
            "    avoid_print: false\n"
        )

        # 3. lib/theme/app_theme.dart
        app_theme_dart = (
            "import 'package:flutter/material.dart';\n\n"
            "class AppTheme {\n"
            "  static const Color primaryColor = Color(0xFF6200EE);\n"
            "  static const Color secondaryColor = Color(0xFF03DAC6);\n"
            "  static const Color backgroundColor = Color(0xFFF5F5F7);\n\n"
            "  static ThemeData get lightTheme {\n"
            "    return ThemeData(\n"
            "      useMaterial3: true,\n"
            "      colorScheme: ColorScheme.fromSeed(\n"
            "        seedColor: primaryColor,\n"
            "        brightness: Brightness.light,\n"
            "      ),\n"
            "      scaffoldBackgroundColor: backgroundColor,\n"
            "      appBarTheme: const AppBarTheme(\n"
            "        elevation: 0,\n"
            "        centerTitle: true,\n"
            "      ),\n"
            "      elevatedButtonTheme: ElevatedButtonThemeData(\n"
            "        style: ElevatedButton.styleFrom(\n"
            "          backgroundColor: primaryColor,\n"
            "          foregroundColor: Colors.white,\n"
            "          shape: RoundedRectangleBorder(\n"
            "            borderRadius: BorderRadius.circular(12),\n"
            "          ),\n"
            "        ),\n"
            "      ),\n"
            "    );\n"
            "  }\n"
            "}\n"
        )

        # 4. lib/models/task_model.dart
        task_model_dart = (
            "class TaskModel {\n"
            "  final int id;\n"
            "  final String title;\n"
            "  final String status;\n"
            "  final String priority;\n\n"
            "  const TaskModel({\n"
            "    required this.id,\n"
            "    required this.title,\n"
            "    this.status = 'pending',\n"
            "    this.priority = 'medium',\n"
            "  });\n\n"
            "  factory TaskModel.fromJson(Map<String, dynamic> json) {\n"
            "    return TaskModel(\n"
            "      id: json['id'] as int,\n"
            "      title: json['title'] as String,\n"
            "      status: json['status'] as String? ?? 'pending',\n"
            "      priority: json['priority'] as String? ?? 'medium',\n"
            "    );\n"
            "  }\n\n"
            "  Map<String, dynamic> toJson() {\n"
            "    return {\n"
            "      'id': id,\n"
            "      'title': title,\n"
            "      'status': status,\n"
            "      'priority': priority,\n"
            "    };\n"
            "  }\n"
            "}\n"
        )

        # 5. lib/services/api_service.dart
        api_service_dart = (
            "import 'dart:convert';\n"
            "import '../models/task_model.dart';\n\n"
            "class ApiService {\n"
            "  final String baseUrl;\n\n"
            "  ApiService({this.baseUrl = 'http://127.0.0.1:8088'});\n\n"
            "  Future<List<TaskModel>> fetchTasks() async {\n"
            "    // Mock / fallback async fetch\n"
            "    await Future.delayed(const Duration(milliseconds: 50));\n"
            "    return [\n"
            "      const TaskModel(id: 1, title: 'Explore NR AI Architecture', status: 'completed'),\n"
            "      const TaskModel(id: 2, title: 'Build Flutter Application', status: 'in_progress'),\n"
            "    ];\n"
            "  }\n\n"
            "  Future<TaskModel> createTask(String title) async {\n"
            "    await Future.delayed(const Duration(milliseconds: 50));\n"
            "    return TaskModel(id: DateTime.now().millisecondsSinceEpoch, title: title);\n"
            "  }\n"
            "}\n"
        )

        # 6. lib/screens/home_screen.dart
        home_screen_dart = (
            "import 'package:flutter/material.dart';\n"
            "import '../models/task_model.dart';\n"
            "import '../services/api_service.dart';\n\n"
            "class HomeScreen extends StatefulWidget {\n"
            "  final String title;\n\n"
            "  const HomeScreen({super.key, required this.title});\n\n"
            "  @override\n"
            "  State<HomeScreen> createState() => _HomeScreenState();\n"
            "}\n\n"
            "class _HomeScreenState extends State<HomeScreen> {\n"
            "  final ApiService _api = ApiService();\n"
            "  List<TaskModel> _tasks = [];\n"
            "  bool _loading = true;\n\n"
            "  @override\n"
            "  void initState() {\n"
            "    super.initState();\n"
            "    _loadTasks();\n"
            "  }\n\n"
            "  Future<void> _loadTasks() async {\n"
            "    final tasks = await _api.fetchTasks();\n"
            "    setState(() {\n"
            "      _tasks = tasks;\n"
            "      _loading = false;\n"
            "    });\n"
            "  }\n\n"
            "  @override\n"
            "  Widget build(BuildContext context) {\n"
            "    return Scaffold({\n"
            "      appBar: AppBar(title: Text(widget.title)),\n"
            "      body: _loading\n"
            "          ? const Center(child: CircularProgressIndicator())\n"
            "          : ListView.builder(\n"
            "              itemCount: _tasks.length,\n"
            "              itemBuilder: (context, index) {\n"
            "                final task = _tasks[index];\n"
            "                return ListTile(\n"
            "                  title: Text(task.title),\n"
            "                  subtitle: Text('Status: ${task.status}'),\n"
            "                  leading: Icon(\n"
            "                    task.status == 'completed'\n"
            "                        ? Icons.check_circle\n"
            "                        : Icons.pending_actions,\n"
            "                    color: task.status == 'completed'\n"
            "                        ? Colors.green\n"
            "                        : Colors.orange,\n"
            "                  ),\n"
            "                );\n"
            "              },\n"
            "            ),\n"
            "      floatingActionButton: FloatingActionButton(\n"
            "        onPressed: () {\n"
            "          setState(() {\n"
            "            _tasks.add(TaskModel(\n"
            "              id: _tasks.length + 1,\n"
            "              title: 'New Task ${_tasks.length + 1}',\n"
            "            ));\n"
            "          });\n"
            "        },\n"
            "        child: const Icon(Icons.add),\n"
            "      ),\n"
            "    );\n"
            "  }\n"
            "}\n"
        ).replace("Scaffold({", "Scaffold(")

        # 7. lib/main.dart
        main_dart = (
            "import 'package:flutter/material.dart';\n"
            "import 'theme/app_theme.dart';\n"
            "import 'screens/home_screen.dart';\n\n"
            "void main() {\n"
            "  runApp(const NRFlutterApp());\n"
            "}\n\n"
            "class NRFlutterApp extends StatelessWidget {\n"
            "  const NRFlutterApp({super.key});\n\n"
            "  @override\n"
            "  Widget build(BuildContext context) {\n"
            "    return MaterialApp(\n"
            f"      title: '{title}',\n"
            "      theme: AppTheme.lightTheme,\n"
            f"      home: const HomeScreen(title: '{title}'),\n"
            "      debugShowCheckedModeBanner: false,\n"
            "    );\n"
            "  }\n"
            "}\n"
        )

        # 8. test/unit_test.dart
        unit_test_dart = (
            "import 'package:test/test.dart';\n"
            f"import 'package:{safe_name}/models/task_model.dart';\n\n"
            "void main() {\n"
            "  group('TaskModel Tests', () {\n"
            "    test('JSON Serialization works correctly', () {\n"
            "      const task = TaskModel(id: 1, title: 'Test Task', status: 'completed');\n"
            "      final json = task.toJson();\n"
            "      expect(json['id'], equals(1));\n"
            "      expect(json['title'], equals('Test Task'));\n"
            "      expect(json['status'], equals('completed'));\n"
            "    });\n\n"
            "    test('JSON Deserialization works correctly', () {\n"
            "      final json = {'id': 2, 'title': 'Parsed Task', 'status': 'pending'};\n"
            "      final task = TaskModel.fromJson(json);\n"
            "      expect(task.id, equals(2));\n"
            "      expect(task.title, equals('Parsed Task'));\n"
            "      expect(task.status, equals('pending'));\n"
            "    });\n"
            "  });\n"
            "}\n"
        )

        # Write files
        files_to_write = {
            root / "pubspec.yaml": pubspec_yaml,
            root / "analysis_options.yaml": analysis_options,
            root / "lib" / "main.dart": main_dart,
            root / "lib" / "theme" / "app_theme.dart": app_theme_dart,
            root / "lib" / "models" / "task_model.dart": task_model_dart,
            root / "lib" / "services" / "api_service.dart": api_service_dart,
            root / "lib" / "screens" / "home_screen.dart": home_screen_dart,
            root / "test" / "unit_test.dart": unit_test_dart,
        }

        for path, content in files_to_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        return {
            "success": True,
            "project_name": safe_name,
            "project_path": str(root),
            "files_created": len(files_to_write),
        }

    def add_dependency(self, project_dir: str | Path, package_name: str, version: str = "any") -> Dict[str, Any]:
        """Injects a dependency into pubspec.yaml."""
        p = Path(project_dir).resolve()
        pubspec = p / "pubspec.yaml"
        if not pubspec.exists():
            return {"success": False, "message": "pubspec.yaml not found"}

        text = pubspec.read_text(encoding="utf-8")
        if f"{package_name}:" in text:
            return {"success": True, "message": f"Dependency '{package_name}' already declared."}

        ver_str = f"^{version}" if version and version != "any" and not version.startswith("^") else (version or "any")
        dep_entry = f"  {package_name}: {ver_str}\n"

        if "dependencies:\n" in text:
            updated = text.replace("dependencies:\n", f"dependencies:\n{dep_entry}", 1)
        else:
            updated = text + f"\ndependencies:\n{dep_entry}"

        pubspec.write_text(updated, encoding="utf-8")
        return {"success": True, "message": f"Added '{package_name}: {ver_str}' to pubspec.yaml"}

    def add_login_screen(self, project_dir: str | Path) -> Dict[str, Any]:
        """Scaffolds lib/screens/login_screen.dart."""
        p = Path(project_dir).resolve()
        login_file = p / "lib" / "screens" / "login_screen.dart"

        login_code = (
            "import 'package:flutter/material.dart';\n\n"
            "class LoginScreen extends StatefulWidget {\n"
            "  final Function(String email)? onLoginSuccess;\n\n"
            "  const LoginScreen({super.key, this.onLoginSuccess});\n\n"
            "  @override\n"
            "  State<LoginScreen> createState() => _LoginScreenState();\n"
            "}\n\n"
            "class _LoginScreenState extends State<LoginScreen> {\n"
            "  final _formKey = GlobalKey<FormState>();\n"
            "  final _emailController = TextEditingController();\n"
            "  final _passwordController = TextEditingController();\n"
            "  bool _obscurePassword = true;\n\n"
            "  @override\n"
            "  void dispose() {\n"
            "    _emailController.dispose();\n"
            "    _passwordController.dispose();\n"
            "    super.dispose();\n"
            "  }\n\n"
            "  void _handleLogin() {\n"
            "    if (_formKey.currentState?.validate() ?? false) {\n"
            "      widget.onLoginSuccess?.call(_emailController.text);\n"
            "      ScaffoldMessenger.of(context).showSnackBar(\n"
            "        SnackBar(content: Text('Welcome, ${_emailController.text}!')), \n"
            "      );\n"
            "    }\n"
            "  }\n\n"
            "  @override\n"
            "  Widget build(BuildContext context) {\n"
            "    return Scaffold(\n"
            "      appBar: AppBar(title: const Text('Sign In')),\n"
            "      body: SafeArea(\n"
            "        child: Center(\n"
            "          child: SingleChildScrollView(\n"
            "            padding: const EdgeInsets.all(24.0),\n"
            "            child: Form(\n"
            "              key: _formKey,\n"
            "              child: Column(\n"
            "                mainAxisAlignment: MainAxisAlignment.center,\n"
            "                crossAxisAlignment: CrossAxisAlignment.stretch,\n"
            "                children: [\n"
            "                  const Icon(Icons.lock_outline, size: 72, color: Colors.deepPurple),\n"
            "                  const SizedBox(height: 24),\n"
            "                  TextFormField(\n"
            "                    controller: _emailController,\n"
            "                    keyboardType: TextInputType.emailAddress,\n"
            "                    decoration: const InputDecoration(\n"
            "                      labelText: 'Email Address',\n"
            "                      prefixIcon: Icon(Icons.email),\n"
            "                      border: OutlineInputBorder(),\n"
            "                    ),\n"
            "                    validator: (v) => (v != null && v.contains('@')) ? null : 'Enter valid email',\n"
            "                  ),\n"
            "                  const SizedBox(height: 16),\n"
            "                  TextFormField(\n"
            "                    controller: _passwordController,\n"
            "                    obscureText: _obscurePassword,\n"
            "                    decoration: InputDecoration(\n"
            "                      labelText: 'Password',\n"
            "                      prefixIcon: const Icon(Icons.lock),\n"
            "                      border: const OutlineInputBorder(),\n"
            "                      suffixIcon: IconButton(\n"
            "                        icon: Icon(_obscurePassword ? Icons.visibility : Icons.visibility_off),\n"
            "                        onPressed: () => setState(() => _obscurePassword = !_obscurePassword),\n"
            "                      ),\n"
            "                    ),\n"
            "                    validator: (v) => (v != null && v.length >= 6) ? null : 'Minimum 6 characters',\n"
            "                  ),\n"
            "                  const SizedBox(height: 24),\n"
            "                  ElevatedButton(\n"
            "                    onPressed: _handleLogin,\n"
            "                    style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),\n"
            "                    child: const Text('Sign In', style: TextStyle(fontSize: 16)),\n"
            "                  ),\n"
            "                ],\n"
            "              ),\n"
            "            ),\n"
            "          ),\n"
            "        ),\n"
            "      ),\n"
            "    );\n"
            "  }\n"
            "}\n"
        )

        login_file.parent.mkdir(parents=True, exist_ok=True)
        login_file.write_text(login_code, encoding="utf-8")
        return {"success": True, "file": str(login_file), "message": "Created lib/screens/login_screen.dart"}

    def add_firebase_auth(self, project_dir: str | Path) -> Dict[str, Any]:
        """Injects Firebase Auth dependencies and writes lib/services/auth_service.dart."""
        self.add_dependency(project_dir, "firebase_core", "2.27.0")
        self.add_dependency(project_dir, "firebase_auth", "4.17.8")

        p = Path(project_dir).resolve()
        auth_file = p / "lib" / "services" / "auth_service.dart"

        auth_code = (
            "class AuthService {\n"
            "  String? _currentUserEmail;\n\n"
            "  String? get currentUserEmail => _currentUserEmail;\n"
            "  bool get isAuthenticated => _currentUserEmail != null;\n\n"
            "  Future<bool> signIn(String email, String password) async {\n"
            "    await Future.delayed(const Duration(milliseconds: 100));\n"
            "    if (email.contains('@') && password.length >= 6) {\n"
            "      _currentUserEmail = email;\n"
            "      return true;\n"
            "    }\n"
            "    return false;\n"
            "  }\n\n"
            "  Future<void> signOut() async {\n"
            "    _currentUserEmail = null;\n"
            "  }\n"
            "}\n"
        )

        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.write_text(auth_code, encoding="utf-8")
        return {
            "success": True,
            "message": "Injected firebase_core & firebase_auth, created lib/services/auth_service.dart",
        }

    def change_theme(self, project_dir: str | Path, primary_color_hex: str = "0xFF00C853") -> Dict[str, Any]:
        """Updates primaryColor in lib/theme/app_theme.dart."""
        p = Path(project_dir).resolve()
        theme_file = p / "lib" / "theme" / "app_theme.dart"
        if not theme_file.exists():
            return {"success": False, "message": "lib/theme/app_theme.dart not found"}

        text = theme_file.read_text(encoding="utf-8")
        updated = re.sub(r"static const Color primaryColor = Color\(0x[0-9A-Fa-f]+\);", f"static const Color primaryColor = Color({primary_color_hex});", text)
        theme_file.write_text(updated, encoding="utf-8")
        return {"success": True, "file": str(theme_file), "message": f"Updated primaryColor to {primary_color_hex}"}

    def analyze(self, project_dir: str | Path, timeout: float = 20.0) -> Dict[str, Any]:
        """Runs 'dart analyze' on project."""
        p = Path(project_dir).resolve()
        dart_cmd = shutil.which("dart") or shutil.which("dart.bat") or (r"C:\flutter\bin\dart.bat" if os.path.exists(r"C:\flutter\bin\dart.bat") else "dart")
        try:
            cmd = [dart_cmd, "analyze"]
            res = subprocess.run(
                cmd,
                cwd=str(p),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=(sys.platform == "win32"),
            )
            output = (res.stdout + "\n" + res.stderr).strip()
            is_clean = "No issues found" in output or res.returncode == 0
            return {
                "success": is_clean,
                "exit_code": res.returncode,
                "output": output,
                "diagnostics": FlutterErrorAnalyzer.analyze(output) if not is_clean else None,
            }
        except Exception as e:
            return {"success": False, "message": f"Dart analyze error: {e}"}

    def run_tests(self, project_dir: str | Path, timeout: float = 30.0) -> Dict[str, Any]:
        """Runs 'dart test' or 'flutter test'."""
        p = Path(project_dir).resolve()
        dart_cmd = shutil.which("dart") or shutil.which("dart.bat") or (r"C:\flutter\bin\dart.bat" if os.path.exists(r"C:\flutter\bin\dart.bat") else "dart")
        try:
            cmd = [dart_cmd, "test"]
            res = subprocess.run(
                cmd,
                cwd=str(p),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=(sys.platform == "win32"),
            )
            output = (res.stdout + "\n" + res.stderr).strip()
            is_pass = res.returncode == 0 or "All tests passed" in output
            return {
                "success": is_pass,
                "exit_code": res.returncode,
                "output": output,
            }
        except Exception as e:
            return {"success": False, "message": f"Dart test execution error: {e}"}

    def list_devices(self) -> Dict[str, Any]:
        """Probes for connected devices/emulators via flutter or adb."""
        try:
            res = subprocess.run(["flutter", "devices"], capture_output=True, text=True, timeout=5.0)
            return {"success": True, "output": res.stdout.strip()}
        except Exception:
            try:
                res = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5.0)
                return {"success": True, "output": res.stdout.strip()}
            except Exception as e:
                return {"success": False, "message": str(e)}
